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

  A row whose check-out is missing/unparseable is only ever placed in a
  session bucket (via check_in alone) so it can be merged with other swipe
  pairs of the same day -- it is NEVER inserted on its own: a session that
  ends without a check-out is skipped and flagged for manual review.

WHAT GETS SKIPPED (and logged to the report)
---------------------------------------------
  - Rows with no parseable numeric Student ID, or a Student ID not present
    in students (shouldn't happen against a library.db built from the
    matching Members export, but checked defensively).
  - Rows with no parseable Date.
  - Rows with no parseable check-in time (can't derive a session) -- the
    small remainder clean_student_data.py couldn't safely recover is
    already itemized in error_log_attendance.log.
  - Sessions that never received a check-out (open/incomplete): the row is
    NOT inserted -- attendance always requires a real check-out -- and is
    written to the shared manual-review ledger instead, so a human can
    supply the missing time. A check-in alone no longer creates an
    attendance row.
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
import os
from pathlib import Path

common_dir = Path(__file__).parent.parent
if str(common_dir) not in sys.path:
    sys.path.insert(0, str(common_dir))

from common import (
    OPEN_TIME,
    fix_checkout_pm_offset,
    log_review_item,
    module_report_dir,
    parse_date,
    parse_time,
)

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
    ap.add_argument(
        "--report",
        type=Path,
        default=module_report_dir("attendance") / "attendance_load_report.txt",
    )
    args = ap.parse_args()

    if not args.db.exists():
        sys.exit(f"--db {args.db} does not exist. Load members into it first.")

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys = ON;")

    existing_student_ids = {
        r[0] for r in conn.execute("SELECT student_id FROM students")
    }

    counts = {"attendance": 0}
    autocorrection_counts = {
        "lunch_break_excluded": 0,
        "checkout_pm_offset_corrected": 0,
        "multiple_swipes_merged": 0,
    }

    # Each error/correction type gets its own list, and its own output file
    # at the end -- instead of one flat "PER-ROW SKIPS/NOTES" section mixing
    # every cause together.
    detail_logs = {
        "unparseable_student_id": [],
        "student_id_not_found": [],
        "unparseable_date": [],
        "no_checkin_time": [],
        "missing_checkout": [],  # session never got a check-out -> review
        "duplicate_session": [],  # UNIQUE constraint insert failures
        "checkout_not_after_checkin": [],  # CHECK constraint insert failures
        "insert_failed_other": [],  # any other insert failure
        "checkout_pm_offset_corrected": [],
        "multiple_swipes_merged": [],
    }

    skipped_id = 0
    skipped_bad_id = 0
    skipped_date = 0
    skipped_no_checkout = 0
    total_rows = 0

    # Rows are grouped by (student_id, date, session) before being written to
    # the DB. Two swipe pairs that land in the same session on the same day
    # (e.g. a student steps out for lunch and swipes back in before 13:00)
    # would otherwise collide on the attendance table's
    # UNIQUE(student_id, date, session) constraint and silently drop the
    # second pair; instead they're merged into a single check_in (earliest)
    # / check_out (latest) row. This is always consistent with the session
    # rule: every row already assigned to the same bucket satisfies that
    # bucket's check_in/check_out condition, and min/max of values that each
    # individually satisfy the condition still satisfies it.
    sessions = {}

    with args.csv.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for line_no, row in enumerate(reader, start=2):  # +1 for header row
            id_raw = (row.get("Student ID") or "").strip()
            if not id_raw.isdigit():
                skipped_bad_id += 1
                detail_logs["unparseable_student_id"].append(
                    f"line {line_no}: unparseable Student ID {row.get('Student ID')!r} -> row SKIPPED"
                )
                continue
            total_rows += 1
            student_id = int(id_raw)
            if student_id not in existing_student_ids:
                skipped_id += 1
                detail_logs["student_id_not_found"].append(
                    f"line {line_no}: student_id {student_id} not found in students table -> row SKIPPED"
                )
                log_review_item(
                    {
                        "table": "attendance",
                        "row": line_no,
                        "student_id": id_raw,
                        "date": row.get("Date", ""),
                        "problem": "student_id_not_found",
                        "detail": f"attendance row {line_no}",
                    }
                )
                continue

            date = parse_date(row.get("Date", ""), min_year=2005, bound_today=True)
            if date is None:
                skipped_date += 1
                detail_logs["unparseable_date"].append(
                    f"line {line_no} (student {student_id}): unparseable date "
                    f"{row.get('Date')!r} -> row SKIPPED"
                )
                log_review_item(
                    {
                        "table": "attendance",
                        "row": line_no,
                        "student_id": id_raw,
                        "date": row.get("Date", ""),
                        "problem": "unparseable_date",
                        "detail": f"attendance row {line_no}",
                    }
                )
                continue

            check_in = parse_time(row.get("In Time", ""))
            check_out = parse_time(row.get("Out Time", ""))

            fixed_checkout = fix_checkout_pm_offset(check_in, check_out)
            if fixed_checkout is not None:
                detail_logs["checkout_pm_offset_corrected"].append(
                    f"line {line_no} (student {student_id}, {date}): check_out {check_out} "
                    f"was before check_in {check_in} -> read as a 12-hour-clock PM time and "
                    f"corrected to {fixed_checkout}"
                )
                check_out = fixed_checkout
                autocorrection_counts["checkout_pm_offset_corrected"] += 1

            session = derive_session(check_in, check_out)
            if session is None:
                detail_logs["no_checkin_time"].append(
                    f"line {line_no}: no usable check-in time -> attendance SKIPPED"
                )
                continue

            key = (student_id, date, session)
            entry = sessions.get(key)
            if entry is None:
                sessions[key] = {
                    "check_in": check_in,
                    "check_out": check_out,
                    "lines": [line_no],
                }
            else:
                if check_in and (
                    entry["check_in"] is None or check_in < entry["check_in"]
                ):
                    entry["check_in"] = check_in
                if check_out and (
                    entry["check_out"] is None or check_out > entry["check_out"]
                ):
                    entry["check_out"] = check_out
                entry["lines"].append(line_no)

    for (student_id, date, session), entry in sessions.items():
        check_in, check_out, lines = (
            entry["check_in"],
            entry["check_out"],
            entry["lines"],
        )

        if len(lines) > 1:
            autocorrection_counts["multiple_swipes_merged"] += 1
            detail_logs["multiple_swipes_merged"].append(
                f"lines {lines} (student {student_id}, {date}, {session}): "
                f"{len(lines)} separate swipe pairs merged into one session "
                f"({check_in}-{check_out})"
            )

        if check_out is None:
            # No check-out ever landed on this (student, date, session):
            # the session is incomplete, so it must NOT appear in the
            # attendance table (attendance requires a real check-out).
            # Flag it for a human to supply the missing time instead.
            skipped_no_checkout += 1
            detail_logs["missing_checkout"].append(
                f"lines {lines} (student {student_id}, {date}, {session}): "
                f"no check-out time on record -> attendance NOT inserted, "
                f"flagged for manual review"
            )
            log_review_item(
                {
                    "table": "attendance",
                    "row": lines,
                    "student_id": student_id,
                    "date": date,
                    "problem": "missing_check_out",
                    "detail": f"attendance session '{session}' has no check_out; "
                              f"only a check-in ({check_in}) was recorded",
                }
            )
            continue

        duration, lunch_overlap = compute_duration_minutes(check_in, check_out)
        if lunch_overlap > 0:
            autocorrection_counts["lunch_break_excluded"] += 1
        # NOTE: if check_out is present but not after check_in, duration is
        # None here and the INSERT below is guaranteed to trip the table's
        # CHECK constraint -- so that case is logged once, in the except
        # block below (checkout_not_after_checkin), instead of twice.

        try:
            conn.execute(
                """INSERT INTO attendance
                   (student_id, date, session, check_in, check_out, duration_minutes)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (student_id, date, session, check_in, check_out, duration),
            )
            counts["attendance"] += 1
        except sqlite3.IntegrityError as e:
            msg = str(e)
            if "UNIQUE constraint" in msg:
                detail_logs["duplicate_session"].append(
                    f"lines {lines} (student {student_id}, {date}, {session}): "
                    f"insert failed ({msg}) -> SKIPPED"
                )
            elif "CHECK constraint" in msg:
                detail_logs["checkout_not_after_checkin"].append(
                    f"lines {lines} (student {student_id}, {date}, {session}): "
                    f"check_out {check_out} not after check_in {check_in} -> "
                    f"insert failed ({msg}) -> SKIPPED"
                )
                log_review_item(
                    {
                        "table": "attendance",
                        "row": lines,
                        "student_id": student_id,
                        "date": date,
                        "problem": "checkout_not_after_checkin",
                        "detail": f"check_out {check_out} vs check_in {check_in}",
                    }
                )
            else:
                detail_logs["insert_failed_other"].append(
                    f"lines {lines} (student {student_id}, {date}, {session}): "
                    f"insert failed ({msg}) -> SKIPPED"
                )

    conn.commit()
    total_attendance = conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0]
    conn.close()

    # One detail file per category, named after the summary report
    # (e.g. attendance_load_report_duplicate_session.txt). Only written
    # when that category actually has entries this run.
    written_detail_files = {}
    for name, entries in detail_logs.items():
        if not entries:
            continue
        detail_path = args.report.parent / f"{args.report.stem}_{name}.txt"
        with detail_path.open("w") as f:
            f.write("\n".join(entries) + "\n")
        written_detail_files[name] = (detail_path, len(entries))

    with args.report.open("w") as f:
        f.write(f"CSV data rows processed: {total_rows}\n")
        f.write(f"Rows skipped (unparseable Student ID): {skipped_bad_id}\n")
        f.write(f"Rows skipped (student_id not found): {skipped_id}\n")
        f.write(f"Rows skipped (unparseable date): {skipped_date}\n")
        f.write(
            f"Rows skipped (no check-out time, flagged for review): {skipped_no_checkout}\n\n"
        )
        f.write("Rows inserted this run, by table:\n")
        for k, v in counts.items():
            f.write(f"  {k}: {v}\n")
        f.write(f"\nTotal rows now in attendance table: {total_attendance}\n")
        f.write(
            "\nAuto-corrections this run (row WAS loaded, value derived/adjusted):\n"
        )
        for k, v in autocorrection_counts.items():
            f.write(f"  {k}: {v}\n")
        f.write("\n=== DETAIL FILES (one per error/correction category) ===\n")
        if written_detail_files:
            for name, (path, n) in written_detail_files.items():
                f.write(f"  {path.name}: {n} rows\n")
        else:
            f.write("  (none -- no skips or corrections this run)\n")

    print(f"Processed {total_rows} CSV rows.")
    print(
        f"Skipped: {skipped_bad_id} (unparseable student_id), {skipped_id} (unknown student_id), "
        f"{skipped_date} (unparseable date), "
        f"{skipped_no_checkout} (no check-out time, flagged for review)"
    )
    print("Inserted this run:", counts)
    print("Auto-corrected this run:", autocorrection_counts)
    print(f"Total rows in attendance table now: {total_attendance}")
    print(f"Summary in {args.report}")
    if written_detail_files:
        print("Detail files written:")
        for name, (path, n) in written_detail_files.items():
            print(f"  {path} ({n} rows)")


if __name__ == "__main__":
    main()
