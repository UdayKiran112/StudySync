#!/usr/bin/env python3
"""
Load coaching-class enrollment (the "Digital Class" column) from the daily
activity-log CSV into the library SQLite database.

Not explicitly requested as its own category, but split out for the same
reason as attendance/offline/digital library: it's a distinct activity type
in the same source CSV, so it gets its own report instead of being folded
into one of the other three.

Usage:
    python3 load_coaching.py --csv students_activity.csv --db library.db

Requires that library.db already exists and its `students` table is
already populated (e.g. via load_members.py) -- every row here is linked
to an existing student purely by "ID NO", never by name.

WHAT THIS LOADS, PER CSV ROW
-----------------------------
  ID NO, Date      -> the student/date this enrollment belongs to.
  Digital Class    -> coaching_classes (one row per unique topic+date,
                       instructor_id left NULL) + coaching_enrollments
                       (participant_type 'Library Student').

WHAT GETS SKIPPED (and logged to the report)
---------------------------------------------
  - Rows with no parseable numeric ID NO, or an ID NO not present in
    students.
  - Rows with no parseable Date.
  - Rows with a blank Digital Class cell (nothing to enroll in).
  - A duplicate (class, student) enrollment is silently ignored (UNIQUE
    constraint), not logged as an error -- it just means the student was
    already enrolled in that class.

LOGGING
--------
This script writes only to its own report (default
coaching_load_report.txt). Attendance, offline library, digital library,
and exam marks are handled by the other loader scripts in this folder,
each with their own separate report.

Re-running this script against the same --db will insert everything again
where a fresh (topic, date) class doesn't already exist, so run it once
per fresh load.
"""

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

from common import collapse_ws, parse_date

COL_DATE = 1
COL_ID = 2
COL_DIGITAL_CLASS = 15


class CoachingLoader:
    def __init__(self, conn):
        self.conn = conn
        self.class_cache = {}  # (topic, date) -> class_id
        self.counts = {"coaching_enrollments": 0}
        self.skips = []

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
            pass  # already enrolled in this class


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--csv", required=True, type=Path)
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--report", type=Path, default=Path("coaching_load_report.txt"))
    args = ap.parse_args()

    if not args.db.exists():
        sys.exit(f"--db {args.db} does not exist. Load members into it first.")

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys = ON;")

    existing_student_ids = {
        r[0] for r in conn.execute("SELECT student_id FROM students")
    }

    loader = CoachingLoader(conn)
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
                continue
            total_rows += 1
            student_id = int(id_raw)
            if student_id not in existing_student_ids:
                skipped_id += 1
                loader.skips.append(
                    f"line {line_no}: student_id {student_id} not found in students table -> row SKIPPED"
                )
                continue

            date = (
                parse_date(row[COL_DATE], min_year=2005, bound_today=True)
                if len(row) > COL_DATE
                else None
            )
            if date is None:
                skipped_date += 1
                loader.skips.append(
                    f"line {line_no} (student {student_id}): unparseable date {row[COL_DATE]!r} -> row SKIPPED"
                )
                continue

            if len(row) > COL_DIGITAL_CLASS:
                loader.load_digital_class(
                    student_id, date, row[COL_DIGITAL_CLASS], line_no
                )

    conn.commit()

    totals = {}
    for t in ["coaching_classes", "coaching_enrollments"]:
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
        f.write("\n=== PER-ROW SKIPS ===\n")
        f.write("\n".join(loader.skips) + "\n")

    print(f"Processed {total_rows} CSV rows.")
    print(
        f"Skipped: {skipped_id} (unknown student_id), {skipped_date} (unparseable date)"
    )
    print("Inserted this run:", loader.counts)
    print(f"Full details in {args.report}")


if __name__ == "__main__":
    main()
