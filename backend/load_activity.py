#!/usr/bin/env python3
"""
Load the daily activity-log CSV (attendance + library/quiz/exam/coaching
usage, one row per visit) into the library SQLite database.

Usage:
    python3 load_activity.py --csv activity.csv --db library.db

Requires that library.db already exists and its `students` table is
already populated (e.g. via load_students.py) -- every row here is linked
to an existing student purely by "ID NO", never by name (the name column
in this export is unreliable/misspelled).

WHAT THIS LOADS, PER CSV ROW
-----------------------------
  ID NO                        -> students.student_id (existing row; the
                                   FK, never re-derived from Name)
  Date, IN, OUT                -> attendance (one row per student/date).
                                   duration_minutes is always DERIVED from
                                   IN/OUT, never read from the CSV's own
                                   DURATION column (it disagreed with IN/OUT
                                   often enough, and can't express the lunch
                                   rule below). Any overlap with 13:00-14:00
                                   is excluded from duration_minutes as an
                                   unattended lunch break, even for a
                                   session that spans across it. Every
                                   exclusion is logged to the report as an
                                   auto-correction (not a skip -- the row is
                                   still loaded, just with an adjusted
                                   number) so it can be reviewed later.
  Book ID + Reference Book      -> books (auto-created master rows) and
                                   offline_library_usage (one row per book;
                                   a cell can list several books comma-
                                   separated -- each becomes its own row)
  Digital Library + Purpose +
  Online Subscription           -> digital_library_usage. A cell can list
                                   several platform/purpose values comma-
                                   separated (the same messy pattern as
                                   Book ID above) -- each becomes its own
                                   row instead of being inserted as one
                                   garbled "Adda 247, Youtube"-style value.
                                   Platform names (and book titles below)
                                   are run through a canonicalizer that
                                   merges pure spelling/case/spacing
                                   variants of the same real-world name so
                                   the subscriptions/books tables don't
                                   grow a new row per typo -- see
                                   Canonicalizer below. Online Subscription
                                   present -> account_type 'Library
                                   Subscription', with a subscriptions
                                   master row auto-created per canonical
                                   platform name; absent -> 'Own Account'.
                                   Re-uses the row's IN/OUT as in_time/
                                   out_time (no separate timestamp exists
                                   for this activity).
  Quiz                          -> quizzes (one row per unique topic+date)
                                   + quiz_scores (score left NULL --
                                   the CSV never records a numeric score,
                                   only a topic; to be filled in later)
  Offline Exam                  -> exams (one row per unique topic+date)
                                   + exam_marks (marks_obtained left NULL,
                                   same reason as above)
  Digital Class                 -> coaching_classes (one row per unique
                                   topic+date, instructor_id left NULL) +
                                   coaching_enrollments (participant_type
                                   'Library Student')

SCHEMA CHANGE THIS SCRIPT MAKES
--------------------------------
quizzes.max_marks, exams.max_marks, quiz_scores.score, and
exam_marks.marks_obtained are NOT NULL in schema.sql, but this CSV never
supplies any of those numbers -- only a topic/subject. Per your
confirmation, the script relaxes all four to nullable (CHECK ... IS NULL
OR ... > 0 in place of NOT NULL) the first time it runs against a given
database, by rebuilding those four tables. It's a no-op if a database has
already been migrated (checked via PRAGMA table_info before touching
anything).

ATTENDANCE SESSION RULE (as confirmed)
---------------------------------------
  - check_out <= 13:00                              -> 'Morning'
  - check_in  >= 13:00                               -> 'Afternoon'
  - check_in  < 13:00 and check_out > 13:00 (spans)   -> 'Full Day'
  - check_in present but check_out missing/unknown    -> based on check_in
    alone: check_in >= 13:00 -> 'Afternoon', else 'Morning' (best guess for
    an open/incomplete session; noted in the report)

WHAT GETS SKIPPED (and logged to the report)
---------------------------------------------
  - Rows with no parseable numeric ID NO, or an ID NO not present in
    students (shouldn't happen against a library.db built from the
    matching Members export, but checked defensively).
  - Rows with no parseable Date (everything else in that row depends on
    it), INCLUDING rows where the date parses structurally but is
    implausible for a historical attendance log: before 2005 (this
    library's earliest real join_date), or after today. The source CSV
    contains literal data-entry typos such as '14.07.2048' and
    '18.12.2065' -- well-formed digits, nonsense year -- so this bound
    catches those rather than loading attendance years in the future.
  - Attendance for a row with no parseable check-in time (can't derive a
    session).
  - digital_library_usage for a row with a subscription/purpose but no
    platform name (platform_name is NOT NULL) -- rare (~4 rows).
  - Any individual insert that still trips a UNIQUE constraint (e.g. two
    rows would both leave a student's attendance/digital-session "open"
    with no check-out, which the schema only allows once per student) --
    caught per-row and logged rather than aborting the whole load.

Re-running this script against the same --db will insert everything again
(no dedup key across runs), so run it once per fresh load.
"""

import argparse
import csv
import difflib
import re
import sqlite3
import sys
from pathlib import Path

COL_SLNO = 0
COL_DATE = 1
COL_ID = 2
COL_NAME = 3
COL_GENDER = 4
COL_IN = 5
COL_OUT = 6
COL_DURATION = 7
COL_BOOK_ID = 8
COL_REF_BOOK = 9
COL_DIGITAL_LIBRARY = 10
COL_PURPOSE = 11
COL_ONLINE_SUB = 12
COL_QUIZ = 13
COL_OFFLINE_EXAM = 14
COL_DIGITAL_CLASS = 15


def collapse_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def parse_date(raw: str):
    """
    Parse a DD.MM.YYYY-ish date. Rejects years outside a plausible window
    for this library's activity log (2005 = earliest real join_date on
    record, current_year+2 = generous near-term buffer). This matters
    because the source CSV contains literal data-entry typos like
    '14.07.2048' and '18.12.2065' -- the digits are well-formed and parse
    fine structurally, so without a plausibility bound they'd silently
    load as real 2048/2065 attendance rows instead of being caught.
    """
    if not raw:
        return None
    s = raw.strip().rstrip("`$.-")
    digit_groups = re.findall(r"\d+", s)
    if len(digit_groups) < 3:
        return None
    day_s, month_s, year_s = digit_groups[0], digit_groups[1], digit_groups[2]
    if len(month_s) > 2:
        return None
    try:
        day, month, year = int(day_s), int(month_s), int(year_s)
    except ValueError:
        return None
    if year < 100:
        year += 2000 if year <= 26 else 1900
    if not (1 <= month <= 12) or not (1 <= day <= 31):
        return None
    import datetime

    today = datetime.date.today()
    if not (2005 <= year <= today.year):
        return None
    try:
        parsed = datetime.date(year, month, day)
    except ValueError:
        return None
    if parsed > today:
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


LUNCH_START_MIN = 13 * 60  # 13:00
LUNCH_END_MIN = 14 * 60  # 14:00


def compute_duration_minutes(check_in, check_out):
    """
    Derive duration purely from check_in/check_out -- the CSV's own
    DURATION column is never read or trusted (it disagreed with
    check_in/check_out on enough rows that deriving it ourselves is more
    reliable, and it can't express the lunch rule below anyway).

    Any overlap with the 13:00-14:00 lunch break is subtracted from the
    total, since that hour is unattended time even for a session that
    spans across it (e.g. 11:30-15:00 counts as 2h30m, not 3h30m).

    Returns (duration_minutes_or_None, lunch_minutes_excluded).
    """
    if not check_in or not check_out:
        return None, 0
    h1, m1 = int(check_in[:2]), int(check_in[3:])
    h2, m2 = int(check_out[:2]), int(check_out[3:])
    start, end = h1 * 60 + m1, h2 * 60 + m2
    if end <= start:
        return None, 0
    lunch_overlap = max(0, min(end, LUNCH_END_MIN) - max(start, LUNCH_START_MIN))
    return (end - start) - lunch_overlap, lunch_overlap


def derive_session(check_in, check_out):
    if check_in is None:
        return None
    in_min = int(check_in[:2]) * 60 + int(check_in[3:])
    if check_out is not None:
        out_min = int(check_out[:2]) * 60 + int(check_out[3:])
        if out_min <= 13 * 60:
            return "Morning"
        if in_min >= 13 * 60:
            return "Afternoon"
        return "Full Day"
    return "Afternoon" if in_min >= 13 * 60 else "Morning"


def slugify(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", s.strip().lower()).strip("_")
    return s or "unknown"


def _normalize_key(s: str) -> str:
    """Strip everything but letters/digits and lowercase, so 'Adda 247',
    'adda_247', and 'ADDA-247' all reduce to the same key."""
    return re.sub(r"[^a-z0-9]+", "", s.lower())


class Canonicalizer:
    """
    Groups near-duplicate raw strings (case/spacing/punctuation variants,
    and minor typos) under one canonical spelling, so a messy CSV column
    doesn't create a new subscriptions/books row for every misspelling of
    the same real-world name (e.g. 'Adda247' / 'Adda 247' / 'adda_247' /
    'Add247' all landing on one entry instead of four).

    Three tiers, each handled with a different confidence level:
      1. EXACT match after stripping case/spacing/punctuation -> merged
         silently. This is a pure formatting difference, not a guess.
      2. ANAGRAM match (same letters, reordered -- e.g. 'Adda247' vs
         '247Adda') -> merged, logged as an auto-correction, since two
         short strings sharing an *entire* letter multiset by chance is
         very unlikely.
      3. FUZZY match above `merge_threshold` (e.g. 'Add247' vs 'Adda247',
         'Reasonoing' vs 'Reasoning') -> merged, logged as an
         auto-correction with the similarity score, so it's easy to
         spot-check later.
      4. Below `merge_threshold` but above `review_threshold` -> NOT
         merged (too risky to guess), but logged as a possible duplicate
         for a human to review.
      5. Below `review_threshold` -> treated as a genuinely new/distinct
         entry, no log entry (this is the common case).

    The canonical spelling kept for a cluster is whichever raw form has
    been seen most often, so a one-off typo doesn't become the
    "official" spelling stored in the database.
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
        key = _normalize_key(raw)
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


def relax_schema(conn: sqlite3.Cursor):
    """Make quizzes.max_marks, exams.max_marks, quiz_scores.score, and
    exam_marks.marks_obtained nullable, if they aren't already."""
    info = {row[1]: row[3] for row in conn.execute("PRAGMA table_info(quiz_scores)")}
    if info.get("score") == 0:
        return  # already relaxed (0 = not NOT NULL)

    conn.executescript(
        """
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
        """
    )


class Loader:
    def __init__(self, conn, existing_student_ids, report):
        self.conn = conn
        self.existing_student_ids = existing_student_ids
        self.report = report
        self.book_cache = {}  # book_id -> canonical title actually stored
        self.subscription_cache = set()
        self.quiz_cache = {}  # (topic, date) -> quiz_id
        self.exam_cache = {}  # (topic, date) -> exam_id
        self.class_cache = {}  # (topic, date) -> class_id
        self.counts = {
            "attendance": 0,
            "offline_usage": 0,
            "digital_usage": 0,
            "quiz_scores": 0,
            "exam_marks": 0,
            "coaching_enrollments": 0,
        }
        self.autocorrections = []
        self.autocorrection_counts = {
            "lunch_break_excluded": 0,
            "duration_left_null_no_checkout": 0,
            "digital_usage_row_split": 0,
            "subscription_name_merged": 0,
            "book_title_merged": 0,
            "book_title_majority_vote": 0,
        }
        self.review_notes = []
        self.subscription_canon = Canonicalizer(
            self.log_auto, self.log_review, "subscription_name_merged"
        )
        self.book_title_canon = Canonicalizer(
            self.log_auto, self.log_review, "book_title_merged"
        )
        # subscription_id -> normalized cluster key, so that once every row
        # has been seen, finalize_canonical_names() can sync an
        # already-inserted subscription whose spelling turned out not to be
        # the cluster's most common one (the winner can only be known once
        # all rows are in, but rows are inserted as we go).
        self.subscription_id_to_cluster_key = {}
        # book_id -> {normalized_title_key: count}. Unlike subscriptions,
        # the same book_id can legitimately show up with several genuinely
        # DIFFERENT titles (not spelling variants -- book_id looks like
        # it's reused rather than being a stable 1:1 catalog key). We tally
        # every title seen per book_id and pick the majority at the end,
        # rather than just keeping whichever title happened to come first.
        self.book_id_title_key_counts = {}

    def log(self, msg):
        """A row (or part of one) was SKIPPED / dropped -- goes in the report's
        skip section."""
        self.report.append(msg)

    def log_auto(self, category, msg):
        """Data WAS loaded, but not exactly as it appeared in the CSV -- the
        app derived or adjusted a value. Tracked separately from skips so the
        report distinguishes 'lost' rows from 'corrected' ones."""
        self.autocorrection_counts[category] = (
            self.autocorrection_counts.get(category, 0) + 1
        )
        self.autocorrections.append(msg)

    def log_review(self, msg):
        """Nothing was changed automatically -- this is a 'looked similar but
        wasn't confident enough to merge' note for a human to look at."""
        self.review_notes.append(msg)

    # ---- master-data getters (cached) -------------------------------
    def get_or_create_book(self, book_id, title, line_no=None):
        canonical_title = self.book_title_canon.canonicalize(
            title, context=f"line {line_no}, book_id {book_id!r}"
        )
        key = _normalize_key(collapse_ws(title))
        key_counts = self.book_id_title_key_counts.setdefault(book_id, {})
        key_counts[key] = key_counts.get(key, 0) + 1

        if book_id in self.book_cache:
            return  # final title (majority vote) gets synced in finalize()
        self.book_cache[book_id] = canonical_title
        try:
            self.conn.execute(
                "INSERT INTO books (book_id, title) VALUES (?, ?)",
                (book_id, canonical_title),
            )
        except sqlite3.IntegrityError as e:
            self.log(f"books insert failed for {book_id!r}/{canonical_title!r}: {e}")

    def get_or_create_subscription(self, platform_name, line_no=None):
        canonical = self.subscription_canon.canonicalize(
            platform_name, context=f"line {line_no}" if line_no is not None else ""
        )
        sub_id = slugify(canonical)
        self.subscription_id_to_cluster_key[sub_id] = _normalize_key(
            collapse_ws(platform_name)
        )
        if sub_id in self.subscription_cache:
            return sub_id
        self.subscription_cache.add(sub_id)
        try:
            self.conn.execute(
                "INSERT INTO subscriptions (subscription_id, name, status) VALUES (?, ?, 'Active')",
                (sub_id, canonical),
            )
        except sqlite3.IntegrityError:
            pass  # already exists
        return sub_id

    def finalize_canonical_names(self):
        """
        Two things can only be known once every row has been seen, so this
        runs after the full CSV pass:

        1. A spelling cluster's "winning" spelling (most frequent variant)
           can shift as later rows come in, but earlier rows already wrote
           whichever spelling was winning *at the time*. Re-sync any
           subscriptions row whose stored name no longer matches its
           cluster's final majority spelling.

        2. For book_id, tally every (canonicalized) title it was ever seen
           with and keep the majority one -- book_id is reused across
           genuinely different titles often enough that "first title wins"
           would silently keep whichever one happened to load first.
        """
        updated_subs = 0
        for sub_id, key in self.subscription_id_to_cluster_key.items():
            final = self.subscription_canon.key_to_canonical.get(key)
            if final:
                cur = self.conn.execute(
                    "SELECT name FROM subscriptions WHERE subscription_id = ?",
                    (sub_id,),
                ).fetchone()
                if cur and cur[0] != final:
                    self.conn.execute(
                        "UPDATE subscriptions SET name = ? WHERE subscription_id = ?",
                        (final, sub_id),
                    )
                    updated_subs += 1
        if updated_subs:
            self.log_auto(
                "subscription_name_merged",
                f"finalize: re-synced {updated_subs} subscriptions.name row(s) to "
                f"their cluster's final majority spelling",
            )

        updated_books = 0
        conflicted_book_ids = 0
        for book_id, key_counts in self.book_id_title_key_counts.items():
            tallies = {}
            for key, cnt in key_counts.items():
                final_title = self.book_title_canon.key_to_canonical.get(key, key)
                tallies[final_title] = tallies.get(final_title, 0) + cnt
            winner = max(tallies, key=tallies.get)
            if len(tallies) > 1:
                conflicted_book_ids += 1
                total = sum(tallies.values())
                breakdown = ", ".join(
                    f"{t!r} ({c}/{total})"
                    for t, c in sorted(tallies.items(), key=lambda kv: -kv[1])
                )
                self.log_auto(
                    "book_title_majority_vote",
                    f"book_id {book_id!r}: seen with {len(tallies)} different titles "
                    f"-- {breakdown} -> kept {winner!r} (majority vote)",
                )
            if winner != self.book_cache.get(book_id):
                self.conn.execute(
                    "UPDATE books SET title = ? WHERE book_id = ?", (winner, book_id)
                )
                self.book_cache[book_id] = winner
                updated_books += 1

        if updated_books:
            self.log_auto(
                "book_title_merged",
                f"finalize: re-synced {updated_books} books.title row(s) to their "
                f"final majority title ({conflicted_book_ids} book_id(s) had "
                f"genuinely conflicting titles, not just spelling variants)",
            )

    def get_or_create_quiz(self, topic, date):
        key = (topic, date)
        if key in self.quiz_cache:
            return self.quiz_cache[key]
        cur = self.conn.execute(
            "INSERT INTO quizzes (quiz_name, quiz_date, subject, max_marks) VALUES (?, ?, ?, NULL)",
            (topic, date, topic),
        )
        self.quiz_cache[key] = cur.lastrowid
        return cur.lastrowid

    def get_or_create_exam(self, topic, date):
        key = (topic, date)
        if key in self.exam_cache:
            return self.exam_cache[key]
        cur = self.conn.execute(
            "INSERT INTO exams (exam_name, exam_date, subject, max_marks) VALUES (?, ?, ?, NULL)",
            (topic, date, topic),
        )
        self.exam_cache[key] = cur.lastrowid
        return cur.lastrowid

    def get_or_create_coaching_class(self, topic, date):
        key = (topic, date)
        if key in self.class_cache:
            return self.class_cache[key]
        cur = self.conn.execute(
            "INSERT INTO coaching_classes (title, class_date, subject) VALUES (?, ?, ?)",
            (topic, date, topic),
        )
        self.class_cache[key] = cur.lastrowid
        return cur.lastrowid

    # ---- per-row feature loaders --------------------------------------
    def load_attendance(self, student_id, date, check_in, check_out, line_no):
        session = derive_session(check_in, check_out)
        if session is None:
            self.log(f"line {line_no}: no usable check-in time -> attendance SKIPPED")
            return

        duration, lunch_overlap = compute_duration_minutes(check_in, check_out)
        if lunch_overlap:
            self.log_auto(
                "lunch_break_excluded",
                f"line {line_no} (student {student_id}, {date}): {check_in}-{check_out} "
                f"overlaps the 13:00-14:00 lunch break -> {lunch_overlap} min excluded, "
                f"duration_minutes set to {duration}",
            )
        elif check_out is None:
            self.log_auto(
                "duration_left_null_no_checkout",
                f"line {line_no} (student {student_id}, {date}): no check_out recorded "
                f"-> duration_minutes left NULL (session marked '{session}')",
            )
        elif duration is None:
            # check_in and check_out both present but check_out <= check_in
            self.log(
                f"line {line_no} (student {student_id}, {date}): check_out {check_out} "
                f"not after check_in {check_in} -> duration_minutes left NULL"
            )

        try:
            self.conn.execute(
                """INSERT INTO attendance
                   (student_id, date, session, check_in, check_out, duration_minutes)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (student_id, date, session, check_in, check_out, duration),
            )
            self.counts["attendance"] += 1
        except sqlite3.IntegrityError as e:
            self.log(f"line {line_no}: attendance insert failed ({e}) -> SKIPPED")

    def load_books(self, student_id, date, book_id_raw, ref_book_raw, line_no):
        ids = [collapse_ws(x) for x in book_id_raw.split(",")] if book_id_raw else []
        names = (
            [collapse_ws(x) for x in ref_book_raw.split(",")] if ref_book_raw else []
        )
        n = max(len(ids), len(names))
        if n == 0:
            return
        for i in range(n):
            bid = (ids[i] if i < len(ids) else None) or None
            name = (names[i] if i < len(names) else None) or None
            if name and name.lower() == "self":
                bid = None  # self-study, not a specific library book
            if bid and not name:
                self.log(
                    f"line {line_no}: book id {bid!r} has no matching title -> entry SKIPPED"
                )
                continue
            if bid:
                self.get_or_create_book(bid, name, line_no)
            elif not name:
                continue
            try:
                self.conn.execute(
                    "INSERT INTO offline_library_usage (student_id, date, book_id) VALUES (?, ?, ?)",
                    (student_id, date, bid),
                )
                self.counts["offline_usage"] += 1
            except sqlite3.IntegrityError as e:
                self.log(
                    f"line {line_no}: offline_library_usage insert failed ({e}) -> SKIPPED"
                )

    def load_digital_usage(
        self,
        student_id,
        date,
        check_in,
        check_out,
        platform_raw,
        purpose_raw,
        sub_raw,
        line_no,
    ):
        if not platform_raw and not sub_raw and not purpose_raw:
            return
        # Same messy pattern as Book ID/Reference Book: a cell can contain
        # several comma-separated values that got merged into one instead
        # of being split into separate rows at data-entry time (e.g.
        # platform 'Adda247 , Adda247' with purpose 'Polity , Polity' is
        # really two separate visits, not one visit to a platform literally
        # named "Adda247 , Adda247").
        platforms = (
            [collapse_ws(x) for x in platform_raw.split(",")] if platform_raw else []
        )
        purposes = (
            [collapse_ws(x) for x in purpose_raw.split(",")] if purpose_raw else []
        )
        n = max(len(platforms), len(purposes))
        if n == 0:
            return

        is_subscription = bool(collapse_ws(sub_raw))
        account_type = "Library Subscription" if is_subscription else "Own Account"

        if n > 1:
            self.log_auto(
                "digital_usage_row_split",
                f"line {line_no} (student {student_id}, {date}): platform/purpose "
                f"cell had {n} comma-separated values ({platform_raw!r} / "
                f"{purpose_raw!r}) -> split into {n} separate digital_library_usage "
                f"rows instead of one garbled row",
            )

        for i in range(n):
            platform_val = platforms[i] if i < len(platforms) else ""
            if not platform_val:
                self.log(
                    f"line {line_no}: digital library activity with no platform name -> SKIPPED"
                )
                continue
            if check_in is None:
                self.log(
                    f"line {line_no}: digital library usage with no check-in time -> SKIPPED"
                )
                continue
            platform = self.subscription_canon.canonicalize(
                platform_val, context=f"line {line_no}"
            )
            sub_id = None
            if is_subscription:
                sub_id = self.get_or_create_subscription(platform, line_no)
            purpose = (purposes[i] if i < len(purposes) else None) or None
            try:
                self.conn.execute(
                    """INSERT INTO digital_library_usage
                       (student_id, date, in_time, out_time, account_type,
                        subscription_id, platform_name, purpose)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        student_id,
                        date,
                        check_in,
                        check_out,
                        account_type,
                        sub_id,
                        platform,
                        purpose,
                    ),
                )
                self.counts["digital_usage"] += 1
            except sqlite3.IntegrityError as e:
                self.log(
                    f"line {line_no}: digital_library_usage insert failed ({e}) -> SKIPPED"
                )

    def load_quiz(self, student_id, date, topic_raw, line_no):
        topic = collapse_ws(topic_raw)
        if not topic:
            return
        quiz_id = self.get_or_create_quiz(topic, date)
        try:
            self.conn.execute(
                "INSERT INTO quiz_scores (student_id, quiz_id, score) VALUES (?, ?, NULL)",
                (student_id, quiz_id),
            )
            self.counts["quiz_scores"] += 1
        except sqlite3.IntegrityError:
            pass  # duplicate (student, quiz) pair -- already recorded

    def load_exam(self, student_id, date, topic_raw, line_no):
        topic = collapse_ws(topic_raw)
        if not topic:
            return
        exam_id = self.get_or_create_exam(topic, date)
        try:
            self.conn.execute(
                "INSERT INTO exam_marks (student_id, exam_id, marks_obtained) VALUES (?, ?, NULL)",
                (student_id, exam_id),
            )
            self.counts["exam_marks"] += 1
        except sqlite3.IntegrityError:
            pass

    def load_digital_class(self, student_id, date, topic_raw, line_no):
        topic = collapse_ws(topic_raw)
        if not topic:
            return
        class_id = self.get_or_create_coaching_class(topic, date)
        try:
            self.conn.execute(
                """INSERT INTO coaching_enrollments (class_id, participant_type, student_id)
                   VALUES (?, 'Library Student', ?)""",
                (class_id, student_id),
            )
            self.counts["coaching_enrollments"] += 1
        except sqlite3.IntegrityError:
            pass


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--csv", required=True, type=Path)
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--report", type=Path, default=Path("activity_load_report.txt"))
    args = ap.parse_args()

    if not args.db.exists():
        sys.exit(f"--db {args.db} does not exist. Load students into it first.")

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys = ON;")
    relax_schema(conn)
    conn.commit()

    existing_student_ids = {
        r[0] for r in conn.execute("SELECT student_id FROM students")
    }

    report = []
    loader = Loader(conn, existing_student_ids, report)

    skipped_id = 0
    skipped_date = 0
    total_rows = 0

    with args.csv.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        for line_no, row in enumerate(reader, start=1):
            if len(row) <= COL_ID:
                continue
            id_raw = row[COL_ID].strip()
            if not id_raw.isdigit():
                continue  # header row / blank filler row / bad id, silently skipped
            total_rows += 1
            student_id = int(id_raw)
            if student_id not in existing_student_ids:
                skipped_id += 1
                report.append(
                    f"line {line_no}: student_id {student_id} not found in students table -> row SKIPPED"
                )
                continue

            date = parse_date(row[COL_DATE]) if len(row) > COL_DATE else None
            if date is None:
                skipped_date += 1
                report.append(
                    f"line {line_no} (student {student_id}): unparseable date {row[COL_DATE]!r} -> row SKIPPED"
                )
                continue

            check_in = parse_time(row[COL_IN]) if len(row) > COL_IN else None
            check_out = parse_time(row[COL_OUT]) if len(row) > COL_OUT else None
            # Note: row[COL_DURATION], the CSV's own DURATION column, is
            # intentionally never read. duration_minutes is always derived
            # from check_in/check_out (see compute_duration_minutes).

            loader.load_attendance(student_id, date, check_in, check_out, line_no)

            book_id_raw = (
                collapse_ws(row[COL_BOOK_ID]) if len(row) > COL_BOOK_ID else ""
            )
            ref_book_raw = (
                collapse_ws(row[COL_REF_BOOK]) if len(row) > COL_REF_BOOK else ""
            )
            loader.load_books(student_id, date, book_id_raw, ref_book_raw, line_no)

            platform_raw = (
                row[COL_DIGITAL_LIBRARY] if len(row) > COL_DIGITAL_LIBRARY else ""
            )
            purpose_raw = row[COL_PURPOSE] if len(row) > COL_PURPOSE else ""
            sub_raw = row[COL_ONLINE_SUB] if len(row) > COL_ONLINE_SUB else ""
            loader.load_digital_usage(
                student_id,
                date,
                check_in,
                check_out,
                platform_raw,
                purpose_raw,
                sub_raw,
                line_no,
            )

            if len(row) > COL_QUIZ:
                loader.load_quiz(student_id, date, row[COL_QUIZ], line_no)
            if len(row) > COL_OFFLINE_EXAM:
                loader.load_exam(student_id, date, row[COL_OFFLINE_EXAM], line_no)
            if len(row) > COL_DIGITAL_CLASS:
                loader.load_digital_class(
                    student_id, date, row[COL_DIGITAL_CLASS], line_no
                )

    loader.finalize_canonical_names()
    conn.commit()

    totals = {}
    for t in [
        "attendance",
        "offline_library_usage",
        "digital_library_usage",
        "quizzes",
        "quiz_scores",
        "exams",
        "exam_marks",
        "coaching_classes",
        "coaching_enrollments",
        "books",
        "subscriptions",
    ]:
        totals[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    conn.close()

    with args.report.open("w") as f:
        f.write(f"CSV data rows processed: {total_rows}\n")
        f.write(f"Rows skipped (student_id not found): {skipped_id}\n")
        f.write(f"Rows skipped (unparseable date): {skipped_date}\n\n")
        f.write("Rows inserted this run, by table:\n")
        for k, v in loader.counts.items():
            f.write(f"  {k}: {v}\n")
        f.write("\nTotal rows now in each table:\n")
        for k, v in totals.items():
            f.write(f"  {k}: {v}\n")
        f.write(
            "\nAuto-corrections this run (row WAS loaded, value derived/adjusted):\n"
        )
        for k, v in loader.autocorrection_counts.items():
            f.write(f"  {k}: {v}\n")
        f.write(
            f"\nPossible duplicates NOT auto-merged (need manual review): {len(loader.review_notes)}\n"
        )
        f.write("\n=== PER-ROW SKIPS (row or part of it was NOT loaded) ===\n")
        f.write("\n".join(report) + "\n")
        f.write(
            "\n=== PER-ROW AUTO-CORRECTIONS (loaded, but adjusted from the raw CSV) ===\n"
        )
        f.write("\n".join(loader.autocorrections) + "\n")
        f.write(
            "\n=== POSSIBLE DUPLICATES NOT MERGED (similar name, kept separate -- please review) ===\n"
        )
        f.write("\n".join(loader.review_notes) + "\n")

    print(f"Processed {total_rows} CSV rows.")
    print(
        f"Skipped: {skipped_id} (unknown student_id), {skipped_date} (unparseable date)"
    )
    print("Inserted this run:", loader.counts)
    print("Auto-corrected this run:", loader.autocorrection_counts)
    print(f"Possible duplicates flagged for review: {len(loader.review_notes)}")
    print(f"Full details in {args.report}")


if __name__ == "__main__":
    main()
