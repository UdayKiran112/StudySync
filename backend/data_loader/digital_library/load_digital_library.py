#!/usr/bin/env python3
"""
Load digital library / online subscription usage into the library SQLite
database from the CLEANED digital_library.csv produced by
clean_student_data.py -- not from the raw students_activity.csv export.

Usage:
    python3 clean_student_data.py students_activity.csv cleaned_output/
    python3 load_digital_library.py --csv cleaned_output/digital_library.csv --db library.db

Requires that library.db already exists and its `students` table is
already populated (e.g. via load_members.py) -- every row here is linked
to an existing student purely by "Student ID", never by name.

WHY THIS READS THE CLEANED CSV INSTEAD OF THE RAW EXPORT
-----------------------------------------------------------
clean_student_data.py already:
  - splits a comma cell like "IACE, Youtube" / "RRB Exam, RRB NTPC" into one
    row per platform/purpose (instead of this loader re-doing that split
    from scratch against a differently-shaped raw row),
  - normalizes Online Subscription typos (Subcription, Subscrption, ...) to
    a clean "Library Subscription" / "Own" Account Type,
  - normalizes recoverable IN/OUT time typos (missing colon, ';'/'.'/'"'
    used as a separator) via its shared fix_times() step, and
  - validates In Time is actually present (digital_library_usage.in_time is
    NOT NULL) and logs a row-specific reason to
    error_log_digital_library.log when it isn't, rather than this loader
    silently skipping it.
This loader now only has to trust that shape and focus on what still needs
doing at load time: platform-name canonicalization (a curated alias table
plus fuzzy spelling merge -- a coarser-grained problem than the
exact-typo cleanup above) and the actual database inserts.

WHAT THIS LOADS, PER CLEANED CSV ROW
---------------------------------------
  Student ID                 -> students.student_id (existing row; the FK)
  Date, In Time, Out Time    -> the usage window (in_time/out_time)
  Account Name                -> platform_name, run through
                                 common.canonicalize_subscription_name:
                                 a curated alias table first (merges the
                                 short-name/low-similarity spelling families
                                 fuzzy matching can't bridge, e.g. 'Jhan
                                 Acadami' -> "Jan's English Academy"), then
                                 the fuzzy common.Canonicalizer for plain
                                 spelling/case/spacing variants of the same
                                 real-world platform name (e.g. 'Adda247' /
                                 'Adda 247' / 'Add247').
  Account Type                -> 'Library Subscription' (CSV value 'Library
                                 Subscription') or 'Own Account' (CSV value
                                 'Own'), with a subscriptions master row
                                 auto-created per canonical platform name
                                 when it's a subscription.
  Purpose                    -> purpose (nullable)

WHAT GETS SKIPPED (and logged to the report)
---------------------------------------------
  - Rows with no parseable numeric Student ID, or a Student ID not present
    in students (shouldn't happen against a library.db built from the
    matching Members export, but checked defensively).
  - Rows with no parseable Date.
  - Rows with no parseable In Time (in_time is NOT NULL in the schema) --
    the small remainder clean_student_data.py couldn't safely recover
    (e.g. a 5-digit typo like '17520', or a value missing its hour
    entirely) is already itemized in error_log_digital_library.log.
  - Rows with no parseable Out Time: a digital session that never received
    a check-out is NOT inserted -- it is flagged for manual review instead,
    so no digital_library_usage row is stored with an open (NULL) out_time.
  - Any insert that trips a UNIQUE constraint (e.g. two rows would both
    leave a student's digital session "open" with no check-out, which the
    schema only allows once per student) -- caught per-row and logged.

LOGGING
--------
This script writes only to its own report (default
digital_library_load_report.txt). Attendance, offline library, coaching,
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
    Canonicalizer,
    CLOSE_TIME,
    OPEN_TIME,
    canonicalize_subscription_name,
    collapse_ws,
    log_review_item,
    module_report_dir,
    normalize_key,
    parse_date,
    parse_time,
    slugify,
)


class DigitalLibraryLoader:
    def __init__(self, conn):
        self.conn = conn
        self.subscription_cache = set()
        # subscription_id -> normalized cluster key, so that once every row
        # has been seen, finalize_canonical_names() can sync an
        # already-inserted subscription whose spelling turned out not to be
        # the cluster's most common one.
        self.subscription_id_to_cluster_key = {}
        self.counts = {"digital_usage": 0}
        self.autocorrection_counts = {"subscription_name_merged": 0}
        self.autocorrections = []
        self.review_notes = []
        self.skips = []
        self.subscription_canon = Canonicalizer(
            self.log_auto, self.log_review, "subscription_name_merged"
        )

    def log_auto(self, category, msg):
        self.autocorrection_counts[category] = (
            self.autocorrection_counts.get(category, 0) + 1
        )
        self.autocorrections.append(msg)

    def log_review(self, msg):
        self.review_notes.append(msg)

    def get_or_create_subscription(self, platform_name, date, line_no=None):
        """subscriptions.start_date is NOT NULL, so it's always supplied
        here from the row's own date -- otherwise the insert would fail
        with an IntegrityError and leave subscriptions permanently empty,
        which would in turn FK-fail every 'Library Subscription' row."""
        canonical = canonicalize_subscription_name(
            platform_name,
            self.subscription_canon,
            context=f"line {line_no}" if line_no is not None else "",
        )
        sub_id = slugify(canonical)
        self.subscription_id_to_cluster_key[sub_id] = normalize_key(
            collapse_ws(platform_name)
        )
        if sub_id in self.subscription_cache:
            return sub_id
        self.subscription_cache.add(sub_id)
        try:
            self.conn.execute(
                "INSERT INTO subscriptions (subscription_id, name, status, start_date) "
                "VALUES (?, ?, 'Active', ?)",
                (sub_id, canonical, date),
            )
        except sqlite3.IntegrityError:
            pass  # already exists
        return sub_id

    def finalize_canonical_names(self):
        """A spelling cluster's "winning" spelling (most frequent variant)
        can shift as later rows come in, but earlier rows already wrote
        whichever spelling was winning *at the time*. Re-sync any
        subscriptions row whose stored name no longer matches its
        cluster's final majority spelling, once the whole CSV has been
        read."""
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

    def load_digital_usage(
        self,
        student_id,
        date,
        check_in,
        check_out,
        platform_raw,
        purpose_raw,
        account_type_raw,
        line_no,
    ):
        platform_val = collapse_ws(platform_raw)
        if not platform_val:
            self.skips.append(
                f"line {line_no}: digital library activity with no Account Name (platform) -> SKIPPED"
            )
            return
        if check_in is None:
            self.skips.append(
                f"line {line_no}: digital library usage with no usable In Time -> SKIPPED"
            )
            return
        if check_in < OPEN_TIME or check_in > CLOSE_TIME:
            self.skips.append(
                f"line {line_no}: digital library usage in time {check_in} "
                f"outside operating hours ({OPEN_TIME}-{CLOSE_TIME}) -> SKIPPED"
            )
            log_review_item(
                {
                    "table": "digital_library_usage",
                    "row": line_no,
                    "student_id": student_id,
                    "date": date,
                    "problem": "outside_operating_hours",
                    "detail": f"in_time {check_in}, out_time {check_out}",
                }
            )
            return

        if check_out is None:
            # A digital session that never got a check-out is incomplete:
            # it must NOT be stored (that would leave a row with an open
            # NULL out_time). Flag it for a human to supply the time.
            self.skips.append(
                f"line {line_no}: digital library usage with no usable Out Time -> "
                f"SKIPPED, flagged for manual review"
            )
            log_review_item(
                {
                    "table": "digital_library_usage",
                    "row": line_no,
                    "student_id": student_id,
                    "date": date,
                    "problem": "missing_out_time",
                    "detail": f"digital library session on platform "
                              f"{platform_val!r} has no out_time; only an "
                              f"in_time ({check_in}) was recorded",
                }
            )
            return

        account_type = (
            "Library Subscription"
            if account_type_raw.strip() == "Library Subscription"
            else "Own Account"
        )
        platform = canonicalize_subscription_name(
            platform_val, self.subscription_canon, context=f"line {line_no}"
        )
        sub_id = None
        if account_type == "Library Subscription":
            sub_id = self.get_or_create_subscription(platform, date, line_no)
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
            self.skips.append(
                f"line {line_no}: digital_library_usage insert failed ({e}) -> SKIPPED"
            )
            log_review_item(
                {
                    "table": "digital_library_usage",
                    "row": line_no,
                    "student_id": student_id,
                    "date": date,
                    "problem": "insert_failed",
                    "detail": f"platform {platform}, {e}",
                }
            )


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--csv", required=True, type=Path, help="cleaned digital_library.csv"
    )
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument(
        "--report",
        type=Path,
        default=module_report_dir("digital_library") / "digital_library_load_report.txt",
    )
    args = ap.parse_args()

    if not args.db.exists():
        sys.exit(f"--db {args.db} does not exist. Load members into it first.")

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys = ON;")

    existing_student_ids = {
        r[0] for r in conn.execute("SELECT student_id FROM students")
    }

    loader = DigitalLibraryLoader(conn)
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
                        "table": "digital_library_usage",
                        "row": line_no,
                        "student_id": id_raw,
                        "date": row.get("Date", ""),
                        "problem": "student_id_not_found",
                        "detail": f"digital library row {line_no}",
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
                        "table": "digital_library_usage",
                        "row": line_no,
                        "student_id": id_raw,
                        "date": row.get("Date", ""),
                        "problem": "unparseable_date",
                        "detail": f"digital library row {line_no}",
                    }
                )
                continue

            check_in = parse_time(row.get("In Time", ""))
            check_out = parse_time(row.get("Out Time", ""))

            loader.load_digital_usage(
                student_id,
                date,
                check_in,
                check_out,
                row.get("Account Name", ""),
                row.get("Purpose", ""),
                row.get("Account Type", ""),
                line_no,
            )

    loader.finalize_canonical_names()
    conn.commit()

    totals = {}
    for t in ["digital_library_usage", "subscriptions"]:
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
        f.write("\n".join(loader.skips) + "\n")
        f.write(
            "\n=== PER-ROW AUTO-CORRECTIONS (loaded, but adjusted from the cleaned CSV) ===\n"
        )
        f.write("\n".join(loader.autocorrections) + "\n")
        f.write(
            "\n=== POSSIBLE DUPLICATES NOT MERGED (similar platform name, kept separate -- please review) ===\n"
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
