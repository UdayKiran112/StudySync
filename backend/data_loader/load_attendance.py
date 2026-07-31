#!/usr/bin/env python3
"""
Load attendance (check-in/check-out per student/date) into the library
SQLite database from the CLEANED attendance.csv produced by
clean_student_data.py -- not from the raw students_activity.csv export.

Usage:
    python3 clean_student_data.py students_activity.csv cleaned_output/
    python3 load_attendance.py --csv cleaned_output/attendance.csv --db library.db

Requires that library.db already exists and its `students` table is
already populated (e.g. via load_members.py) -- every row here is linked
to an existing student purely by "Student ID", never by name (the name
column in this export is unreliable/misspelled).

WHY THIS READS THE CLEANED CSV INSTEAD OF THE RAW EXPORT
-----------------------------------------------------------
clean_student_data.py already:
  - normalizes recoverable In/Out time typos (missing colon, ';'/'.'/'"'
    used as a separator) via its shared fix_times() step, and
  - corrects the autofill/copy-paste date bug where the day/month stayed
    the same but the year drifted (e.g. '14:07.2026' -> '14.07.2025').
This loader trusts that cleanup and focuses on what's specific to
attendance: deriving the session type and duration.

WHAT THIS LOADS, PER CLEANED CSV ROW
---------------------------------------
  Student ID                  -> students.student_id (existing row; the FK,
                                  never re-derived from Name)
  Date, In Time, Out Time     -> attendance (one row per student/date).
                                  duration_minutes is always DERIVED from
                                  In/Out Time, never read from the source
                                  CSV's own DURATION column (it disagreed
                                  with In/Out often enough, and can't
                                  express the lunch rule below). Any
                                  overlap with 13:00-14:00 is excluded from
                                  duration_minutes as an unattended lunch
                                  break, even for a session that spans
                                  across it.

ATTENDANCE SESSION RULE
-------------------------
  - check_out <= 13:00                              -> 'Morning'
  - check_in  >= 13:00                               -> 'Afternoon'
  - check_in  < 13:00 and check_out > 13:00 (spans)   -> 'Full Day'
  - check_in present but check_out missing/unknown    -> based on check_in
    alone: check_in >= 13:00 -> 'Afternoon', else 'Morning' (best guess for
    an open/incomplete session)

WHAT GETS SKIPPED (and logged to the report)
---------------------------------------------
  - Rows with no parseable numeric Student ID, or a Student ID not present
    in students (shouldn't happen against a library.db built from the
    matching Members export, but checked defensively).
  - Rows with no parseable Date.
  - Rows with no parseable check-in time (can't derive a session) -- the
    small remainder clean_student_data.py couldn't safely recover is
    already itemized in error_log_attendance.log.
  - Any insert that still trips a UNIQUE constraint (e.g. a student would
    end up with two "open" sessions -- no check-out -- on record at once,
    which the schema only allows once per student).

LOGGING
--------
This script writes only to its own report (default
attendance_load_report.txt). Offline library, digital library, coaching,
and exam marks are handled by the other loader scripts in this folder,
each with their own separate report.

Re-running this script against the same --db will insert everything again
(no dedup key across runs), so run it once per fresh load.
"""

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

from common import parse_date, parse_time

LUNCH_START_MIN = 13 * 60  # 13:00
LUNCH_END_MIN = 14 * 60  # 14:00


def compute_duration_minutes(check_in, check_out):
    """
    Derive duration purely from check_in/check_out -- the CSV's own
    DURATION column is never read or trusted. Any overlap with the
    13:00-14:00 lunch break is subtracted from the total, since that hour
    is unattended time even for a session that spans across it (e.g.
    11:30-15:00 counts as 2h30m, not 3h30m).

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


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--csv", required=True, type=Path, help="cleaned attendance.csv")
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--report", type=Path, default=Path("attendance_load_report.txt"))
    args = ap.parse_args()

    if not args.db.exists():
        sys.exit(f"--db {args.db} does not exist. Load members into it first.")

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys = ON;")

    existing_student_ids = {
        r[0] for r in conn.execute("SELECT student_id FROM students")
    }

    skips = []
    counts = {"attendance": 0}
    autocorrection_counts = {
        "lunch_break_excluded": 0,
        "duration_left_null_no_checkout": 0,
    }

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
                skips.append(
                    f"line {line_no}: student_id {student_id} not found in students table -> row SKIPPED"
                )
                continue

            date = parse_date(row.get("Date", ""), min_year=2005, bound_today=True)
            if date is None:
                skipped_date += 1
                skips.append(
                    f"line {line_no} (student {student_id}): unparseable date "
                    f"{row.get('Date')!r} -> row SKIPPED"
                )
                continue

            check_in = parse_time(row.get("In Time", ""))
            check_out = parse_time(row.get("Out Time", ""))

            session = derive_session(check_in, check_out)
            if session is None:
                skips.append(
                    f"line {line_no}: no usable check-in time -> attendance SKIPPED"
                )
                continue

            duration, lunch_overlap = compute_duration_minutes(check_in, check_out)
            if lunch_overlap > 0:
                autocorrection_counts["lunch_break_excluded"] += 1
            if check_out is None:
                autocorrection_counts["duration_left_null_no_checkout"] += 1
            elif duration is None:
                skips.append(
                    f"line {line_no} (student {student_id}, {date}): check_out {check_out} "
                    f"not after check_in {check_in} -> duration_minutes left NULL"
                )

            try:
                conn.execute(
                    """INSERT INTO attendance
                       (student_id, date, session, check_in, check_out, duration_minutes)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (student_id, date, session, check_in, check_out, duration),
                )
                counts["attendance"] += 1
            except sqlite3.IntegrityError as e:
                skips.append(
                    f"line {line_no}: attendance insert failed ({e}) -> SKIPPED"
                )

    conn.commit()
    total_attendance = conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0]
    conn.close()

    with args.report.open("w") as f:
        f.write(f"CSV data rows processed: {total_rows}\n")
        f.write(f"Rows skipped (student_id not found): {skipped_id}\n")
        f.write(f"Rows skipped (unparseable date): {skipped_date}\n\n")
        f.write("Rows inserted this run, by table:\n")
        for k, v in counts.items():
            f.write(f"  {k}: {v}\n")
        f.write(f"\nTotal rows now in attendance table: {total_attendance}\n")
        f.write(
            "\nAuto-corrections this run (row WAS loaded, value derived/adjusted):\n"
        )
        for k, v in autocorrection_counts.items():
            f.write(f"  {k}: {v}\n")
        f.write("\n=== PER-ROW SKIPS / NOTES ===\n")
        f.write("\n".join(skips) + "\n")

    print(f"Processed {total_rows} CSV rows.")
    print(
        f"Skipped: {skipped_id} (unknown student_id), {skipped_date} (unparseable date)"
    )
    print("Inserted this run:", counts)
    print("Auto-corrected this run:", autocorrection_counts)
    print(f"Total rows in attendance table now: {total_attendance}")
    print(f"Full details in {args.report}")


if __name__ == "__main__":
    main()
