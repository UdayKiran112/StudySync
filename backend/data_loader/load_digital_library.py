#!/usr/bin/env python3
"""
Load digital library / online subscription usage from the daily
activity-log CSV into the library SQLite database.

Usage:
    python3 load_digital_library.py --csv students_activity.csv --db library.db

Requires that library.db already exists and its `students` table is
already populated (e.g. via load_members.py) -- every row here is linked
to an existing student purely by "ID NO", never by name.

WHAT THIS LOADS, PER CSV ROW
-----------------------------
  ID NO, Date, IN, OUT           -> the student/date/time window this
                                     usage belongs to (re-uses IN/OUT as
                                     in_time/out_time -- no separate
                                     timestamp exists for this activity).
  Digital Library + Purpose +
  Online Subscription            -> digital_library_usage. A cell can
                                     list several platform/purpose values
                                     comma-separated -- each becomes its
                                     own row instead of being inserted as
                                     one garbled "Adda 247, Youtube"-style
                                     value. Platform names are run through
                                     a canonicalizer (common.Canonicalizer)
                                     that merges pure spelling/case/spacing
                                     variants of the same real-world name.
                                     Online Subscription present ->
                                     account_type 'Library Subscription',
                                     with a subscriptions master row
                                     auto-created per canonical platform
                                     name; absent -> 'Own Account'.

WHAT GETS SKIPPED (and logged to the report)
---------------------------------------------
  - Rows with no parseable numeric ID NO, or an ID NO not present in
    students.
  - Rows with no parseable Date.
  - digital_library_usage for a row with a subscription/purpose but no
    platform name (platform_name is NOT NULL) -- rare.
  - digital_library_usage for a row with no parseable check-in time
    (in_time is NOT NULL in the schema).
  - Any insert that trips a UNIQUE constraint (e.g. two rows would both
    leave a student's digital session "open" with no check-out, which the
    schema only allows once per student) -- caught per-row and logged.

BUG FIX (vs. the original combined loader)
--------------------------------------------
subscriptions.start_date is NOT NULL in schema.sql, but the original
get_or_create_subscription() never supplied it, so every subscription
insert failed with an IntegrityError that was silently swallowed by a bare
`except sqlite3.IntegrityError: pass` (whose comment assumed the only
possible failure was "row already exists"). That left `subscriptions`
permanently empty and caused every 'Library Subscription' row to be
skipped later with "FOREIGN KEY constraint failed", since it referenced a
subscription_id that was never actually created. This script now passes
the activity row's own date as start_date, so the insert succeeds and
those rows load.

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
from pathlib import Path

from common import (
    Canonicalizer,
    collapse_ws,
    normalize_key,
    parse_date,
    parse_time,
    slugify,
)

COL_DATE = 1
COL_ID = 2
COL_IN = 5
COL_OUT = 6
COL_DIGITAL_LIBRARY = 10
COL_PURPOSE = 11
COL_ONLINE_SUB = 12


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
        self.autocorrection_counts = {
            "digital_usage_row_split": 0,
            "subscription_name_merged": 0,
        }
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
        """
        date: the activity-log date of the row that first introduced this
        subscription, used to fill subscriptions.start_date (NOT NULL in
        schema.sql, no default). FIX: the original loader never supplied
        this column, so every subscription insert failed with an
        IntegrityError that was silently swallowed by the bare except
        below (its comment wrongly assumed the only possible IntegrityError
        was "row already exists") -- leaving `subscriptions` permanently
        empty and every 'Library Subscription' digital_library_usage row
        skipped downstream on the resulting FK violation. Supplying
        start_date here is what makes the insert actually succeed.
        """
        canonical = self.subscription_canon.canonicalize(
            platform_name, context=f"line {line_no}" if line_no is not None else ""
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
        """
        A spelling cluster's "winning" spelling (most frequent variant) can
        shift as later rows come in, but earlier rows already wrote
        whichever spelling was winning *at the time*. Re-sync any
        subscriptions row whose stored name no longer matches its
        cluster's final majority spelling, once the whole CSV has been read.
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
        # of being split into separate rows at data-entry time.
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
                self.skips.append(
                    f"line {line_no}: digital library activity with no platform name -> SKIPPED"
                )
                continue
            if check_in is None:
                self.skips.append(
                    f"line {line_no}: digital library usage with no check-in time -> SKIPPED"
                )
                continue
            platform = self.subscription_canon.canonicalize(
                platform_val, context=f"line {line_no}"
            )
            sub_id = None
            if is_subscription:
                sub_id = self.get_or_create_subscription(platform, date, line_no)
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
                self.skips.append(
                    f"line {line_no}: digital_library_usage insert failed ({e}) -> SKIPPED"
                )


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--csv", required=True, type=Path)
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument(
        "--report", type=Path, default=Path("digital_library_load_report.txt")
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

            check_in = parse_time(row[COL_IN]) if len(row) > COL_IN else None
            check_out = parse_time(row[COL_OUT]) if len(row) > COL_OUT else None

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
            "\n=== PER-ROW AUTO-CORRECTIONS (loaded, but adjusted from the raw CSV) ===\n"
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
