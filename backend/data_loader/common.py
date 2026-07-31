"""
Shared helpers used by the three loader scripts in this folder:

    load_members.py           -- members_details.csv  -> students
    load_student_activity.py  -- students_activity.csv -> attendance, books,
                                  offline/digital library usage, coaching
    load_exam_marks.py        -- students_activity.csv (Offline Exam column)
                                  + internal_marks.csv  -> exams, exam_marks

Nothing in here talks to a specific CSV column layout -- it's pure text/date
parsing and the fuzzy-name-canonicalization engine, factored out so the three
loaders don't each carry their own copy.
"""

import difflib
import re
import sqlite3
import datetime


# --------------------------------------------------------------------------
# text / date / time helpers
# --------------------------------------------------------------------------
def collapse_ws(s: str) -> str:
    """Trim and collapse internal whitespace runs (handles padded cells)."""
    return re.sub(r"\s+", " ", s or "").strip()


def parse_date(
    raw: str, min_year: int = 1900, max_year: int = 2100, bound_today: bool = False
):
    """
    Parse a DD.MM.YYYY-ish date that may use '.', ',', '-', ':', '/' as
    separators (including doubled/mixed separators and trailing junk).
    Returns 'YYYY-MM-DD' or None if it can't be confidently parsed.

    min_year/max_year: plausibility bound on the parsed year.
    bound_today: if True, additionally reject any year above the current
        year and any full date after today (used for the daily activity
        log, which contains typos like '14.07.2048'). When True, max_year
        is ignored in favor of the current year.
    """
    if not raw:
        return None
    s = raw.strip().rstrip("`$.-")
    digit_groups = re.findall(r"\d+", s)
    if len(digit_groups) < 3:
        return None
    day_s, month_s, year_s = digit_groups[0], digit_groups[1], digit_groups[2]

    # Malformed month field (e.g. "012") - not safely recoverable.
    if len(month_s) > 2:
        return None

    try:
        day, month, year = int(day_s), int(month_s), int(year_s)
    except ValueError:
        return None

    if year < 100:
        # 2-digit year: this dataset only spans births in the 1900s and
        # joins from 2005 onward, so 00-26 -> 2000s, else 1900s.
        year += 2000 if year <= 26 else 1900

    if not (1 <= month <= 12):
        return None
    if not (1 <= day <= 31):
        return None

    today = datetime.date.today()
    upper = today.year if bound_today else max_year
    if not (min_year <= year <= upper):
        return None

    try:
        parsed = datetime.date(year, month, day)
    except ValueError:
        return None

    if bound_today and parsed > today:
        return None

    return f"{year:04d}-{month:02d}-{day:02d}"


def parse_time(raw: str):
    """Normalize to zero-padded HH:MM (24h), matching the schema's GLOB check."""
    if not raw:
        return None
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", raw)
    if not m:
        return None
    h, mnt = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23) or not (0 <= mnt <= 59):
        return None
    return f"{h:02d}:{mnt:02d}"


def parse_member_id(raw: str):
    """
    Parse a CSV member-number cell into an int for use as student_id.
    Strips stray non-digit characters (e.g. a trailing backtick). Returns
    None if the cell is blank -> caller should let SQLite auto-assign.
    """
    digits = re.sub(r"\D", "", raw or "")
    return int(digits) if digits else None


def slugify(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", s.strip().lower()).strip("_")
    return s or "unknown"


def normalize_key(s: str) -> str:
    """Strip everything but letters/digits and lowercase, so 'Adda 247',
    'adda_247', and 'ADDA-247' all reduce to the same key."""
    return re.sub(r"[^a-z0-9]+", "", s.lower())


# --------------------------------------------------------------------------
# fuzzy name/label canonicalizer (subscriptions, book titles, exam topics)
# --------------------------------------------------------------------------
class Canonicalizer:
    """
    Groups near-duplicate raw strings (case/spacing/punctuation variants,
    and minor typos) under one canonical spelling, so a messy CSV column
    doesn't create a new master-data row for every misspelling of the same
    real-world name (e.g. 'Adda247' / 'Adda 247' / 'adda_247' / 'Add247'
    all landing on one entry instead of four).

    Three tiers, each handled with a different confidence level:
      1. EXACT match after stripping case/spacing/punctuation -> merged
         silently. This is a pure formatting difference, not a guess.
      2. ANAGRAM match (same letters, reordered) -> merged, logged as an
         auto-correction.
      3. FUZZY match above `merge_threshold` -> merged, logged as an
         auto-correction with the similarity score.
      4. Below `merge_threshold` but above `review_threshold` -> NOT
         merged (too risky to guess), but logged as a possible duplicate
         for a human to review.
      5. Below `review_threshold` -> treated as a genuinely new/distinct
         entry, no log entry (the common case).

    The canonical spelling kept for a cluster is whichever raw form has
    been seen most often, so a one-off typo doesn't become the "official"
    spelling stored in the database.
    """

    def __init__(
        self,
        log_auto,
        log_review,
        category,
        merge_threshold=0.90,
        review_threshold=0.78,
    ):
        self.log_auto = log_auto
        self.log_review = log_review
        self.category = category
        self.merge_threshold = merge_threshold
        self.review_threshold = review_threshold
        self.key_to_canonical = {}  # normalized key -> current canonical spelling
        self.spelling_counts = {}  # canonical spelling -> {raw spelling: count}

    def canonicalize(self, raw: str, context: str = "") -> str:
        raw = collapse_ws(raw)
        if not raw:
            return raw
        key = normalize_key(raw)
        if not key:
            return raw

        if key in self.key_to_canonical:
            canonical = self.key_to_canonical[key]
        else:
            canonical = self._find_match(key, raw, context)
            self.key_to_canonical[key] = canonical

        counts = self.spelling_counts.setdefault(canonical, {})
        counts[raw] = counts.get(raw, 0) + 1
        # The most frequently-seen spelling becomes the display/stored form.
        canonical = max(counts, key=counts.get)
        # Repoint every key that currently maps to this cluster at the
        # (possibly updated) most-frequent spelling.
        for k in list(self.key_to_canonical):
            if self.key_to_canonical[k] in counts:
                self.key_to_canonical[k] = canonical
        return canonical

    def _find_match(self, key: str, raw: str, context: str) -> str:
        sorted_key = "".join(sorted(key))
        best_canonical, best_score = None, 0.0
        for existing_key, existing_canonical in self.key_to_canonical.items():
            if existing_key == key:
                continue
            if "".join(sorted(existing_key)) == sorted_key and len(key) >= 4:
                self.log_auto(
                    self.category,
                    f"{context}: {raw!r} is the same letters reordered as "
                    f"existing entry {existing_canonical!r} -> merged",
                )
                return existing_canonical
            score = difflib.SequenceMatcher(None, key, existing_key).ratio()
            if score > best_score:
                best_canonical, best_score = existing_canonical, score

        if best_canonical is not None and best_score >= self.merge_threshold:
            self.log_auto(
                self.category,
                f"{context}: {raw!r} matched existing entry {best_canonical!r} "
                f"(similarity {best_score:.2f}) -> merged rather than creating a duplicate",
            )
            return best_canonical

        if best_canonical is not None and best_score >= self.review_threshold:
            self.log_review(
                f"{context}: {raw!r} looks similar to existing entry {best_canonical!r} "
                f"(similarity {best_score:.2f}) but not similar enough to auto-merge -- "
                f"kept as a separate entry, please review",
            )

        return raw


# --------------------------------------------------------------------------
# one-time schema migration shared by anything that writes exams/quizzes
# --------------------------------------------------------------------------
def relax_marks_schema(conn: sqlite3.Connection):
    """
    Make quizzes.max_marks, exams.max_marks, quiz_scores.score, and
    exam_marks.marks_obtained nullable, if they aren't already.

    The daily activity CSV and the internal marks register never supply a
    quiz/exam max_marks up front, and the activity CSV's Offline Exam
    column never supplies a numeric score at all -- only a topic. schema.sql
    declares those four columns NOT NULL, so this relaxes them
    (CHECK ... IS NULL OR ... > 0 in place of NOT NULL) the first time it
    runs against a given database. It's a no-op if a database has already
    been migrated (checked via PRAGMA table_info before touching anything),
    so it's safe to call from more than one loader script.
    """
    info = {row[1]: row[3] for row in conn.execute("PRAGMA table_info(quiz_scores)")}
    if info.get("score") == 0:
        return  # already relaxed (0 = not NOT NULL)

    conn.executescript("""
        PRAGMA foreign_keys = OFF;

        CREATE TABLE quizzes_new (
            quiz_id         INTEGER PRIMARY KEY AUTOINCREMENT,
            quiz_name       TEXT NOT NULL CHECK(length(trim(quiz_name)) > 0),
            quiz_date       DATE,
            subject         TEXT,
            max_marks       REAL CHECK(max_marks IS NULL OR max_marks > 0)
        );
        INSERT INTO quizzes_new SELECT * FROM quizzes;
        DROP TABLE quizzes;
        ALTER TABLE quizzes_new RENAME TO quizzes;
        CREATE INDEX idx_quizzes_quiz_date ON quizzes(quiz_date);

        CREATE TABLE quiz_scores_new (
            score_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id      INTEGER NOT NULL,
            quiz_id         INTEGER NOT NULL,
            score           REAL CHECK(score IS NULL OR score >= 0),
            remarks         TEXT,
            FOREIGN KEY (student_id) REFERENCES students(student_id) ON DELETE RESTRICT,
            FOREIGN KEY (quiz_id) REFERENCES quizzes(quiz_id) ON DELETE RESTRICT,
            UNIQUE(student_id, quiz_id)
        );
        INSERT INTO quiz_scores_new SELECT * FROM quiz_scores;
        DROP TABLE quiz_scores;
        ALTER TABLE quiz_scores_new RENAME TO quiz_scores;
        CREATE INDEX idx_quiz_scores_student_id ON quiz_scores(student_id);
        CREATE INDEX idx_quiz_scores_quiz_id ON quiz_scores(quiz_id);

        CREATE TABLE exams_new (
            exam_id         INTEGER PRIMARY KEY AUTOINCREMENT,
            exam_name       TEXT NOT NULL CHECK(length(trim(exam_name)) > 0),
            exam_date       DATE,
            subject         TEXT,
            max_marks       REAL CHECK(max_marks IS NULL OR max_marks > 0)
        );
        INSERT INTO exams_new SELECT * FROM exams;
        DROP TABLE exams;
        ALTER TABLE exams_new RENAME TO exams;
        CREATE INDEX idx_exams_exam_date ON exams(exam_date);

        CREATE TABLE exam_marks_new (
            mark_id         INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id      INTEGER NOT NULL,
            exam_id         INTEGER NOT NULL,
            marks_obtained  REAL CHECK(marks_obtained IS NULL OR marks_obtained >= 0),
            remarks         TEXT,
            FOREIGN KEY (student_id) REFERENCES students(student_id) ON DELETE RESTRICT,
            FOREIGN KEY (exam_id) REFERENCES exams(exam_id) ON DELETE RESTRICT,
            UNIQUE(student_id, exam_id)
        );
        INSERT INTO exam_marks_new SELECT * FROM exam_marks;
        DROP TABLE exam_marks;
        ALTER TABLE exam_marks_new RENAME TO exam_marks;
        CREATE INDEX idx_exam_marks_student_id ON exam_marks(student_id);
        CREATE INDEX idx_exam_marks_exam_id ON exam_marks(exam_id);

        PRAGMA foreign_keys = ON;
        """)
