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

import calendar
import difflib
import json
import re
import sqlite3
import datetime
from pathlib import Path


# --------------------------------------------------------------------------
# text / date / time helpers
# --------------------------------------------------------------------------
def collapse_ws(s: str) -> str:
    """Trim and collapse internal whitespace runs (handles padded cells)."""
    return re.sub(r"\s+", " ", s or "").strip()


def parse_date(
    raw: str,
    min_year: int = 1900,
    max_year: int = 2100,
    bound_today: bool = False,
    clamp_day: bool = False,
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
    clamp_day: if True, a day of the month that overflows the month's real
        length (e.g. '31.09.2021') is clamped to that month's last valid
        day (30.09.2021) instead of being rejected. Only applied when the
        year and month themselves are valid -- a day overflow is the one
        recoverable typo class this dataset actually contains, and is
        always logged by the caller when used.
    """
    if not raw:
        return None
    s = raw.strip().rstrip("`$.-")
    digit_groups = re.findall(r"\d+", s)
    if len(digit_groups) < 3:
        return None
    day_s, month_s, year_s = digit_groups[0], digit_groups[1], digit_groups[2]

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
        # Day overflows the month's real length (e.g. '31.09.2021').
        # Clamp to the month's last valid day when asked, else reject.
        if not clamp_day:
            return None
        last_day = calendar.monthrange(year, month)[1]
        if day > last_day:
            day = last_day
            try:
                parsed = datetime.date(year, month, day)
            except ValueError:
                return None
        else:
            return None

    if bound_today and parsed > today:
        return None

    return f"{year:04d}-{month:02d}-{day:02d}"


def parse_time(raw: str):
    """Normalize to zero-padded HH:MM (24h), matching the schema's GLOB check.

    Tolerates the recoverable typos this dataset actually contains:
      - a separator other than ':' (';', '.', '"') between hour and minute
        (e.g. '14.05', '14;05' -> '14:05')
      - a missing leading zero on the minute (e.g. '16:5' -> '16:05')
      - a missing separator entirely, with 3 or 4 digits total
        (e.g. '930' -> '09:30', '1752' -> '17:52')
    Anything else (no hour digits at all, a bare trailing separator, an
    ambiguous digit count like a stray 5-digit run) is left unparseable
    and returns None so the caller can log/skip it rather than guess.
    """
    if not raw:
        return None
    s = raw.strip()
    m = re.match(r'^(\d{1,2})[:;."](\d{1,2})$', s)
    if m:
        h, mnt = int(m.group(1)), int(m.group(2))
    else:
        m2 = re.match(r"^(\d{3,4})$", s)
        if not m2:
            return None
        digits = m2.group(1)
        if len(digits) == 3:
            h, mnt = int(digits[0]), int(digits[1:])
        else:
            h, mnt = int(digits[:2]), int(digits[2:])
    if not (0 <= h <= 23) or not (0 <= mnt <= 59):
        return None
    return f"{h:02d}:{mnt:02d}"


# --------------------------------------------------------------------------
# operating hours / 12-hour-clock slip helpers (shared by the attendance,
# digital library, and offline library loaders and by clean_student_data)
# --------------------------------------------------------------------------

# The library opens at 09:00 and closes at 19:00 at the latest (usually
# 17:30, but students genuinely stay past 18:00). These bounds drive the
# PM-clock-slip correction and the closing clamp below.
OPEN_TIME = "09:00"
CLOSE_TIME = "19:00"


def _time_to_min(t: str) -> int:
    return int(t[:2]) * 60 + int(t[3:])


def fix_checkin_pm_offset(check_in: str, check_out: str) -> str | None:
    """
    A check-in before the library's 09:00 opening is almost always the
    swipe device recording an afternoon time on a 12-hour clock without
    adding 12 hours (e.g. '02:00' really meant 14:00). This dataset has a
    whole block of them (42 rows on one day, all '02:00'..'02:45').

    Reads the raw time as a PM time (+12h) whenever it is before opening
    and its hour is < 12. The caller decides what to do with the result --
    a fixed time that is still not before check_out makes the row invalid
    (check_out > check_in fails), which is the correct outcome for a pair
    that was broken no matter how it is read.

    Returns the corrected 'HH:MM', or None if there is nothing to fix.
    """
    if not check_in:
        return None
    h = int(check_in[:2])
    if h >= 12 or check_in >= OPEN_TIME:
        return None
    fixed = _time_to_min(check_in) + 12 * 60
    if fixed >= 24 * 60:
        return None
    fh, fm = divmod(fixed, 60)
    return f"{fh:02d}:{fm:02d}"


def fix_checkout_pm_offset(check_in: str, check_out: str) -> str | None:
    """
    If check_out is not after check_in, this is almost always the swipe
    device recording an afternoon/evening check-out on a 12-hour clock
    without adding 12 hours (e.g. checkout '01:00' really meant 13:00).

    Try adding 12 hours to check_out and accept the fix only if it (a)
    lands after check_in and (b) produces a plausible same-day session of
    at most 13 hours, so an actually-swapped or badly mistyped pair of
    times (which a 12-hour-clock slip can't explain) is left alone for the
    caller to skip/log instead of being papered over.

    Returns the corrected 'HH:MM' check_out, or None if no safe fix applies.
    """
    if not check_in or not check_out:
        return None
    start = _time_to_min(check_in)
    end = _time_to_min(check_out)
    if end > start:
        return None  # nothing to fix
    if int(check_out[:2]) >= 12:
        return None  # already PM/noon-or-later; a +12 wrap isn't a clock slip
    fixed_end = end + 12 * 60
    if fixed_end <= start or fixed_end - start > 13 * 60:
        return None
    fh, fm = divmod(fixed_end, 60)
    return f"{fh:02d}:{fm:02d}"


def clamp_out_time(check_out: str) -> str | None:
    """Clamp a check-out past the library's closing time to the closing
    time itself (e.g. '19:03' -> '19:00'). The library is never open past
    19:00, so an out time beyond it is a data-entry overrun, not a real
    stay. Returns the corrected 'HH:MM', or None if nothing to clamp."""
    if check_out and check_out > CLOSE_TIME:
        return CLOSE_TIME
    return None


# --------------------------------------------------------------------------
# reports / logs tree
# --------------------------------------------------------------------------

# Every report and log the data pipeline produces lands under this one
# folder, one subfolder per module (members, attendance, digital_library,
# offline_library, coaching, marks, review, ...). See run_pipeline.py for
# the layout.
REPORTS_DIR = Path(__file__).resolve().parent / "reports"


def module_report_dir(module_name: str) -> Path:
    """Return the subfolder of reports/ for one pipeline module, creating
    it if it doesn't exist yet (the containing folder is a generated
    artifacts tree, so it is never checked into git)."""
    d = REPORTS_DIR / module_name
    d.mkdir(parents=True, exist_ok=True)
    return d


# --------------------------------------------------------------------------
# manual-review ledger
# --------------------------------------------------------------------------

# Structured "needs a human" records, one JSON object per line. Every
# loader appends anything it cannot safely auto-correct here; run_pipeline
# resets the file before a run and renders it into the consolidated
# manual-review report afterwards. Lives under the reports tree so the
# whole pipeline's output is one gitignored folder.
LEDGER_DIR = module_report_dir("review")
LEDGER_PATH = LEDGER_DIR / "review_items.jsonl"


def log_review_item(entry: dict):
    """Append one structured review item to the shared ledger."""
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    with LEDGER_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def reset_review_ledger():
    """Clear the ledger before a fresh pipeline run."""
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    LEDGER_PATH.write_text("", encoding="utf-8")


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
# exam/quiz topic canonicalization (shared by the marks-register loader and
# the offline-exam / quiz loaders so both sides agree on a (topic, date) key)
# --------------------------------------------------------------------------

# A raw "Name of Exam" / topic that is actually just a date (a stray
# column-shift surviving from the source spreadsheet) should never become
# its own fake exam/quiz topic.
BARE_DATE_RE = re.compile(r"^\d{1,2}[.\-/ ]\d{1,2}[.\-/ ]\d{2,4}$")

# Exam-topic abbreviations that edit-distance/anagram matching can't bridge
# (e.g. 'Ari & Rea' vs 'Arithmetic & Reasoning' share almost no characters
# despite meaning the same exam). Deliberately NOT exhaustive: ambiguous
# short forms that could mean more than one real subject in this dataset
# (e.g. 'G S' could be General Science or General Studies) are left OUT on
# purpose -- they become their own distinct topic rather than being
# guessed into the wrong one. Keys are pre-normalized with normalize_key
# after stripping a trailing Exam/Test/Grand Test suffix and any trailing
# "(...)" annotation (see strip_exam_suffix).
_EXAM_TOPIC_ALIAS_GROUPS = {
    "Arithmetic & Reasoning": [
        "Ari & Rea",
        "Ari&Rea",
        "Ari  & Rea",
        "Ari &Rea",
        "Ar i& Rea",
        "Ari & Reasoning",
        "ARI & REA",
        "A & R",
        "A&R",
        "A& R",
        "A&R Eam",
    ],
    "RRB NTPC": ["RRB NTPC", "RRBNTPC", "RRB NTPC EXAM"],
    "RRB Group D": [
        "RRB Group D",
        "RRB Group-D",
        "RRB Group - D",
        "RRB GRoup D",
        "RRB Group-D Test",
        "RRB Group-D GT",
        "RRBGroup D GT",
        "RRB GROUP D G Test",
        "RRB Gr.D",
        "RRB Gr. D",
        "RRBGr.D",
        "RRB Gr.D Exam",
        "RRB Gr.D Test",
    ],
    "SSC GD": ["SSC GD", "SSC G D", "SSC  GD", "SSCGD", "SSC GD  TEST"],
    "Current Affairs": ["Current Affairs", "C A", "C.A", "Current Afairs", "C.Affairs"],
    "Modern History": ["Modern History", "Modren    history"],
    "General Science": [
        "General Science",
        "G Science",
        "G.Science",
        "G. Science",
        "G.Sci",
    ],
    "General Studies": [
        "General Studies",
        "G Studies",
        # NOTE: 'G S' is deliberately NOT included here -- see the
        # ambiguity note above (could be General Science or General
        # Studies). Only the unambiguous misspelling is aliased.
        "Genaral Studies",
    ],
    "Constable Grand Test": [
        "Constable Grand Test",
        "Constable G T",
        "Constable G  T",
        "Contable Grand Test",
        "Constable",
    ],
    "General Maths": [
        "General Maths",
        "G.Maths",
        "G Maths",
        "G.Maths Exam",
    ],
    "Spelling Test": [
        "Spelling Test",
        "Spelling",
        "EnglishSpelling",
        "EnglishSpelling Test",
    ],
    "General Awareness": [
        "General Awareness",
        "G Awareness",
        "G.Awareness",
        "G Awareness Exam",
        "G.Awareness Exam",
    ],
    "Polity": [
        "Polity",
        "Polity Exam",
        "Polity Eaxam",
    ],
}
EXAM_TOPIC_ALIASES = {
    normalize_key(
        re.sub(r"(?i)\b(grand\s*test|exam|test)\b\s*$", "", v).strip(" .-")
    ): canon
    for canon, variants in _EXAM_TOPIC_ALIAS_GROUPS.items()
    for v in variants
}


def strip_exam_suffix(cleaned: str) -> str:
    """Drop a trailing '(11)'/'(EM)'-style annotation and a trailing
    Exam/Test/Grand Test word, for matching purposes only -- doesn't
    change what gets displayed if there's no alias/fuzzy match."""
    s = re.sub(r"\s*[\(\[][^)\]]*[\)\]]\s*$", "", cleaned)
    s = re.sub(r"(?i)\b(grand\s*test|exam|test)\b\s*$", "", s).strip(" .-")
    return s or cleaned


def canonicalize_exam_topic(raw, canon, context="", log_date_reject=None):
    """
    Canonicalize an exam/quiz topic string so the same real exam (from
    either the daily activity CSV's Offline Exam/Quiz column or a separate
    --marks-csv register) lands on the same (topic, date) key.

    Tries, in order: (0) strip a stray leading/trailing '"' left by an
    unescaped quote in the source spreadsheet, and reject a bare date
    (a stray column-shift, not a real topic); (1) the explicit
    abbreviation alias table (handles 'Ari & Rea' -> 'Arithmetic &
    Reasoning', which edit-distance can't bridge); (2) the generic
    fuzzy/anagram/exact canonicalizer (handles plain typos like
    'Reasoing' -> 'Reasoning').

    `canon` is a common.Canonicalizer instance that performs tier 2.
    `log_date_reject` is an optional callable invoked with the raw value
    when the topic is rejected as a bare date.
    """
    cleaned = collapse_ws(raw).strip('"').strip()
    if not cleaned:
        return cleaned
    if BARE_DATE_RE.match(cleaned):
        if log_date_reject:
            log_date_reject(cleaned)
        return ""
    stripped = strip_exam_suffix(cleaned)
    key = normalize_key(stripped) or normalize_key(cleaned)
    if key in EXAM_TOPIC_ALIASES:
        return EXAM_TOPIC_ALIASES[key]
    return canon.canonicalize(stripped, context=context)


def exam_identity_key(raw: str) -> str:
    """
    Deterministic, run-independent identity key for an exam topic, used to
    match the same real exam across the two source CSVs (the marks register
    and the daily activity log) even when they spell it differently.

    Unlike canonicalize_exam_topic -- which may lean on a per-run
    Canonicalizer for fuzzy/typo matches and therefore isn't stable across
    loader processes -- this is pure alias resolution + normalization: two
    spellings produce the same key if and only if they reduce to the same
    letters after the shared alias table and suffix/punctuation stripping
    (so 'polity' and 'Polity' collapse, as do 'G.Maths Exam' and 'General
    Maths'). It exists solely for the one-exam-per-(real exam, date)
    guarantee: when a loader is about to create an exams row for
    (topic, date), any existing same-date row with the same key is the same
    real exam and is reused instead of creating a duplicate.

    Returns '' for blank topics and for topics that are really bare dates
    (stray column-shifts that must never become a fake exam).
    """
    cleaned = collapse_ws(raw).strip('"').strip()
    if not cleaned or BARE_DATE_RE.match(cleaned):
        return ""
    stripped = strip_exam_suffix(cleaned)
    key = normalize_key(stripped) or normalize_key(cleaned)
    canonical = EXAM_TOPIC_ALIASES.get(key)
    return normalize_key(canonical) if canonical else key


def get_or_create_exam(
    conn: sqlite3.Connection,
    cache: dict,
    topic: str,
    date: str,
    log_merge=None,
    rename_on_merge: bool = False,
):
    """
    Shared exam lookup/create used by BOTH the offline-exam loader and the
    marks-register loader, so a real exam never ends up as two rows on the
    same date just because the two sources spell its name differently.

    Lookup order:
      1. in-memory `cache` (same (topic, date) seen earlier this run);
      2. exact exam_name + date in the database (fast path; also makes
         re-runs against an already-populated DB work);
      3. any same-date exam whose deterministic identity key
         (exam_identity_key) matches -- this is what collapses cross-source
         spelling variants (e.g. 'polity' vs 'Polity', 'RRB Gr.D Exam' vs
         'RRB Group D') that each loader's separate per-run Canonicalizer
         instance can never see.

    Returns (exam_id, created). When an existing row is reused under a
    different stored name it is renamed to `topic` if `rename_on_merge` is
    set (the marks register is the authoritative spelling; the offline
    loader should leave existing names alone), and `log_merge`, if given,
    is called with (exam_id, old_name, new_name, date) so every merge is
    auditable. The caller is responsible for counting `exams_created` from
    the `created` flag.
    """
    key = (topic, date)
    if key in cache:
        return cache[key], False

    row = conn.execute(
        "SELECT exam_id FROM exams WHERE exam_name = ? AND exam_date = ?",
        (topic, date),
    ).fetchone()
    if row:
        cache[key] = row[0]
        return row[0], False

    idkey = exam_identity_key(topic)
    if idkey:
        for exam_id, exam_name in conn.execute(
            "SELECT exam_id, exam_name FROM exams WHERE exam_date = ?", (date,)
        ):
            if exam_identity_key(exam_name) == idkey:
                if exam_name != topic:
                    if log_merge:
                        log_merge(exam_id, exam_name, topic, date)
                    if rename_on_merge:
                        conn.execute(
                            "UPDATE exams SET exam_name = ? WHERE exam_id = ?",
                            (topic, exam_id),
                        )
                cache[key] = exam_id
                return exam_id, False

    cur = conn.execute(
        "INSERT INTO exams (exam_name, exam_date, subject, max_marks) VALUES (?, ?, ?, NULL)",
        (topic, date, topic),
    )
    cache[key] = cur.lastrowid
    return cur.lastrowid, True


# --------------------------------------------------------------------------
# subscription / platform-name canonicalization (digital library loader)
# --------------------------------------------------------------------------

# A raw "Account Name" (platform) value is normally a real online product
# (Testbook, Adda247, Jan's English Academy, ...), but data-entry typos,
# abbreviations, and flatly different spellings of the same institution are
# far too messy for edit-distance alone to bridge: 'Jhan Acadami' vs
# "Jan's English Academy" share almost no characters, and short forms like
# 'Jhan' vs 'Jan' sit below any safe fuzzy threshold. The canonical name
# for each cluster is the brand the library actually sells. Keys are
# pre-normalized with normalize_key, exactly like EXAM_TOPIC_ALIASES.
# Anything not listed here still falls through to the generic
# Canonicalizer's exact/anagram/fuzzy tiers.
#
# NOTE: values are the single-platform Account Names seen in the CLEANED
# digital_library.csv -- clean_student_data.py splits comma-separated
# multi-platform cells into one row per platform before this loader runs,
# so no comma-joined variants belong here.
_SUBSCRIPTION_ALIAS_GROUPS = {
    "Jan's English Academy": [
        "Jan", "Jan Academy", "Jan academy", "Jan Acadami", "Jan acadami",
        "Jan Accademy", "JAN Accademy", "Jan Acadeny", "Jan Acamedy",
        "Jan Eng", "Jan Eng Academy", "Jan Eng Acadami", "Jan Eng acadami",
        "Jan eng Acadami", "Jan Eng Aca", "Jan Eng. Academy", "Jan Eng Akadami",
        "Jan English", "Jan English Academy", "Jan English Acadmy",
        "Jan Englih Academy", "Jan acadami Eng", "Jan's Eng", "Jan's Eng Academy",
        "Jan's English", "Jan's Academy", "Jan's academy", "Jans Eng",
        "jans Eng Academy", "Jans Eng Academy", "Jans Eng Academy6",
        "Jans English", "Jans Academy", "Jhan", "jhan", "Jhan Academy",
        "Jhan Acadami", "Jhan acadami", "Jhan Aca", "Jhan Akadami",
        "Jhan Acadeny", "Jhan Acedemy", "Jhan cademy", "Jhan Eng Academy",
        "Jhan English Academy", "Jhan acadde", "Jhanacadami", "Jhans Academy",
        "Jhans academy",
    ],
    "Testbook": [
        "Testbook", "testbook", "Test book", "Test Book", "test book",
        "TestbookRRB", "Textbook", "TextBook", "TExtbook", "TEstbook",
        "TEestbook", "Tesbook", "Tesstbook", "Testook", "Testboo", "TestbooK",
        "Tstbook", "Test bokk", "Test books", "est book",
    ],
    "Chandan Logic": [
        "Chandan", "Chandanlogic", "Chandan logic", "chandan logic",
        "Chandan Logics", "Chandan logics", "Chandan Logic", "Chandanlogics",
        "Chandhan Logics", "Chandhan Logic", "Chandanalogic", "Chandlogic",
        "Chanlogic",
    ],
    "Sreedhar CCE": [
        "Sreedhar", "Sreedhar cce", "sreedhar cce", "Sreedhar cc", "Sreedhar CC",
        "Sreedhar CCE", "Sreedhar CCe", "Sreedharcce", "Sreeedhar cce",
        "Sreedhar ce", "Sreedher cce", "Sreedha cce", "Sreedhare cc",
        "Sreedharr cce", "Sreeddhar cce", "Sreedhacce", "Sedharcce",
        "Sreetdhar CC", "Screedhar CC", "Sreedjar CC", "Sreedhars cce",
        "Sreedhar C", "Seedhar cce", "Sreedher",
    ],
    "Yes & Yes": [
        "Yes & Yes", "YES & YES", "Yes&Yes", "YES &YES", "Yes &yes",
        "YES& YES", "Yes& Yes", "Yes&yes", "YES & Yes", "Yes & yes",
    ],
    "Yes Officer": [
        "Yes Officer", "Yes officer", "yes officer", "Yesofficer",
        "Yes Office", "Yes office", "Yes Oficer", "Yes offier", "Yes offiver",
        "Ye Officer", "Ye officer", "Yews officer", "Yes  Officer",
        "Yes  officer", "YES Officer", "Yes Officerr",
    ],
    "Adda247": [
        "Adda247", "Adda 247", "adda247", "Add247", "247Adda", "Adda 27",
        "Adda2147", "ADDA247",
    ],
    "Winner": ["Winner", "Winners", "winner"],
    "Everest": ["Everest", "Evrest", "Everst"],
    "Everest Coaching": ["Everest Coaching"],
    "Everest Impact": ["Everest impact", "Everest Impact"],
    "Olive Board": [
        "Oliveboard", "Olive board", "Olive Board", "Oilive board",
        "oilive board", "oilive Board", "oilive oard", "oilive boadr",
        "oilive boad", "Oilive boadr", "oilive", "Oliveboar", "Oilive boad",
    ],
    "Azzu": ["Azzu", "AZZU"],
    "Shyam": ["Shyam", "Shym", "Syam", "Shaym", "Shyam Institute"],
    "IACE": ["IACE", "IAEC"],
    "Irise": ["Irise", "IRise", "I Rise", "irise"],
    "Practice Mock": ["Practice Mock", "Practice mock", "Pretice mock", "Mock Practice"],
    "RRB": ["RRB"],
    "Telegram": ["Telegram"],
    "Youtube": [
        "Youtube", "youtube", "YOutube", "Youtbe", "Youtub", "Yuotube",
        "Yotube", "Youtue", "Youdtube", "Youtube. youtube",
    ],
    "Vision IAS": ["Vision IAS", "Visaion IAS"],
    "Prepusion": ["Prepusion"],
}

SUBSCRIPTION_ALIASES = {
    normalize_key(v): canon
    for canon, variants in _SUBSCRIPTION_ALIAS_GROUPS.items()
    for v in variants
}


def canonicalize_subscription_name(raw, canon, context=""):
    """Canonicalize a platform/subscription Account Name so the same real
    product lands on one canonical spelling instead of one row per
    misspelling.

    Tries, in order: (1) the explicit alias table above -- bridges the
    short-name and low-similarity spelling families edit-distance can't
    (e.g. 'Jhan Acadami' -> "Jan's English Academy"); (2) the generic
    fuzzy/anagram/exact canonicalizer (common.Canonicalizer) for typos the
    table doesn't enumerate (e.g. 'Adda 247' -> 'Adda247').
    """
    cleaned = collapse_ws(raw).strip('"').strip()
    if not cleaned:
        return cleaned
    key = normalize_key(cleaned)
    if key in SUBSCRIPTION_ALIASES:
        return SUBSCRIPTION_ALIASES[key]
    return canon.canonicalize(cleaned, context=context)


# --------------------------------------------------------------------------
# one-time schema migration shared by anything that writes exams/quizzes
# --------------------------------------------------------------------------
def relax_marks_schema(conn: sqlite3.Connection):
    """
    Make quizzes.max_marks, quiz_scores.score, and exams.max_marks
    nullable, if they aren't already.

    exam_marks.marks_obtained is deliberately NOT relaxed: it is NOT NULL
    in schema.sql, and no loader ever inserts a scoreless row -- an
    offline-exam sitting without a score is flagged for the manual-review
    ledger instead (see load_offline_exam.py). This migration is a no-op
    against any database built from the current schema.sql (those columns
    are already nullable, so PRAGMA table_info reports notnull = 0 and the
    guard returns immediately); it exists for older databases only.
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
            marks_obtained  REAL NOT NULL CHECK(marks_obtained >= 0),
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
