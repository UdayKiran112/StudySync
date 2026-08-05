#!/usr/bin/env python3
"""
Load coaching-class enrollment (the "Digital Class" column) into the
library SQLite database from the CLEANED digital_class.csv produced by
clean_student_data.py -- not from the raw students_activity.csv export.

Not explicitly requested as its own category, but split out for the same
reason as attendance/offline/digital library: it's a distinct activity type
in the same source CSV, so it gets its own report instead of being folded
into one of the other three.

Usage:
    python3 clean_student_data.py students_activity.csv cleaned_output/
    python3 load_coaching.py --csv cleaned_output/digital_class.csv --db library.db

Requires that library.db already exists and its `students` table is
already populated (e.g. via load_members.py) -- every row here is linked
to an existing student purely by "Student ID", never by name.

WHY THIS READS THE CLEANED CSV INSTEAD OF THE RAW EXPORT
-----------------------------------------------------------
clean_student_data.py already corrects the autofill/copy-paste date bug
(day/month staying the same while the year drifted) and flags Digital
Class values that are purely numeric -- which don't look like a real class
name -- in error_log_digital_class.log, rather than silently loading them
as a topic here.

WHAT THIS LOADS, PER CLEANED CSV ROW
---------------------------------------
  Student ID, Date  -> the student/date this enrollment belongs to.
  Class Name        -> coaching_classes (one row per unique topic+date,
                        instructor_id left NULL) + coaching_enrollments
                        (participant_type 'Library Student').

WHAT GETS SKIPPED (and logged to the report)
---------------------------------------------
  - Rows with no parseable numeric Student ID, or a Student ID not present
    in students.
  - Rows with no parseable Date.
  - Rows with a blank Class Name cell (nothing to enroll in).
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
import os
from pathlib import Path

common_dir = Path(__file__).parent.parent
if str(common_dir) not in sys.path:
    sys.path.insert(0, str(common_dir))


from common import collapse_ws, log_review_item, module_report_dir, parse_date


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
    ap.add_argument("--csv", required=True, type=Path, help="cleaned digital_class.csv")
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument(
        "--report",
        type=Path,
        default=module_report_dir("coaching") / "coaching_load_report.txt",
    )
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
        reader = csv.DictReader(f)
        for line_no, row in enumerate(reader, start=2):  # +1 for header row
            id_raw = (row.get("Student ID") or "").strip()
            if not id_raw.isdigit():
                continue
            total_rows += 1
            student_id = int(id_raw)
            if student_id not in existing_student_ids:
                skipped_id += 1
                loader.skips.append(
                    f"line {line_no}: student_id {student_id} not found in students table -> row SKIPPED"
                )
                log_review_item(
                    {
                        "table": "coaching_enrollments",
                        "row": line_no,
                        "student_id": id_raw,
                        "date": row.get("Date", ""),
                        "problem": "student_id_not_found",
                        "detail": f"digital class row {line_no}",
                    }
                )
                continue

            date = parse_date(row.get("Date", ""), min_year=2005, bound_today=True)
            if date is None:
                skipped_date += 1
                loader.skips.append(
                    f"line {line_no} (student {student_id}): unparseable date "
                    f"{row.get('Date')!r} -> row SKIPPED"
                )
                log_review_item(
                    {
                        "table": "coaching_enrollments",
                        "row": line_no,
                        "student_id": id_raw,
                        "date": row.get("Date", ""),
                        "problem": "unparseable_date",
                        "detail": f"digital class row {line_no}",
                    }
                )
                continue

            loader.load_digital_class(
                student_id, date, row.get("Class Name", ""), line_no
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
