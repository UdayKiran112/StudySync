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
  Date, IN, OUT, DURATION       -> attendance (one row per student/date)
  Book ID + Reference Book      -> books (auto-created master rows) and
                                   offline_library_usage (one row per book;
                                   a cell can list several books comma-
                                   separated -- each becomes its own row)
  Digital Library + Purpose +
  Online Subscription           -> digital_library_usage. Online
                                   Subscription present -> account_type
                                   'Library Subscription', with a
                                   subscriptions master row auto-created
                                   per unique platform name; absent ->
                                   'Own Account'. Re-uses the row's IN/OUT
                                   as in_time/out_time (no separate
                                   timestamp exists for this activity).
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


def parse_duration_minutes(raw: str, check_in, check_out):
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", raw or "")
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    if check_in and check_out:
        h1, m1 = map(int, check_in.split(":"))
        h2, m2 = map(int, check_out.split(":"))
        diff = (h2 * 60 + m2) - (h1 * 60 + m1)
        return diff if diff > 0 else None
    return None


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
        self.book_cache = {}  # book_id -> title actually stored
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

    def log(self, msg):
        self.report.append(msg)

    # ---- master-data getters (cached) -------------------------------
    def get_or_create_book(self, book_id, title):
        if book_id in self.book_cache:
            return
        self.book_cache[book_id] = title
        try:
            self.conn.execute(
                "INSERT INTO books (book_id, title) VALUES (?, ?)", (book_id, title)
            )
        except sqlite3.IntegrityError as e:
            self.log(f"books insert failed for {book_id!r}/{title!r}: {e}")

    def get_or_create_subscription(self, platform_name):
        sub_id = slugify(platform_name)
        if sub_id in self.subscription_cache:
            return sub_id
        self.subscription_cache.add(sub_id)
        try:
            self.conn.execute(
                "INSERT INTO subscriptions (subscription_id, name, status) VALUES (?, ?, 'Active')",
                (sub_id, platform_name),
            )
        except sqlite3.IntegrityError:
            pass  # already exists
        return sub_id

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
    def load_attendance(
        self, student_id, date, check_in, check_out, duration_raw, line_no
    ):
        session = derive_session(check_in, check_out)
        if session is None:
            self.log(f"line {line_no}: no usable check-in time -> attendance SKIPPED")
            return
        duration = parse_duration_minutes(duration_raw, check_in, check_out)
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
                self.get_or_create_book(bid, name)
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
        platform = collapse_ws(platform_raw)
        if not platform:
            self.log(
                f"line {line_no}: digital library activity with no platform name -> SKIPPED"
            )
            return
        if check_in is None:
            self.log(
                f"line {line_no}: digital library usage with no check-in time -> SKIPPED"
            )
            return
        is_subscription = bool(collapse_ws(sub_raw))
        sub_id = self.get_or_create_subscription(platform) if is_subscription else None
        account_type = "Library Subscription" if is_subscription else "Own Account"
        purpose = collapse_ws(purpose_raw) or None
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
            duration_raw = row[COL_DURATION] if len(row) > COL_DURATION else ""

            loader.load_attendance(
                student_id, date, check_in, check_out, duration_raw, line_no
            )

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
        f.write("\n=== PER-ROW WARNINGS/SKIPS ===\n")
        f.write("\n".join(report) + "\n")

    print(f"Processed {total_rows} CSV rows.")
    print(
        f"Skipped: {skipped_id} (unknown student_id), {skipped_date} (unparseable date)"
    )
    print("Inserted this run:", loader.counts)
    print(f"Full details in {args.report}")


if __name__ == "__main__":
    main()
