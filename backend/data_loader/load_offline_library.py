#!/usr/bin/env python3
"""
Load offline (physical) library book usage from the daily activity-log CSV
into the library SQLite database.

Usage:
    python3 load_offline_library.py --csv students_activity.csv --db library.db

Requires that library.db already exists and its `students` table is
already populated (e.g. via load_members.py) -- every row here is linked
to an existing student purely by "ID NO", never by name.

WHAT THIS LOADS, PER CSV ROW
-----------------------------
  ID NO, Date               -> the student/date this usage belongs to.
  Book ID + Reference Book  -> books (auto-created master rows) and
                                offline_library_usage (one row per book; a
                                cell can list several books comma-separated
                                -- each becomes its own row instead of one
                                garbled multi-book entry).

Book titles are run through a canonicalizer (common.Canonicalizer) that
merges pure spelling/case/spacing variants and close typos of the same
real title, so the books table doesn't grow a new row per typo (e.g.
'Adda247 Guide' / 'adda247 guide' / 'Add247 Guide' collapsing to one
entry). Because the same book_id can legitimately show up with several
genuinely DIFFERENT titles (book_id looks reused rather than a stable 1:1
catalog key), every title seen per book_id is tallied and the majority
title wins -- finalized only after the whole CSV has been read (see
finalize_canonical_names()).

WHAT GETS SKIPPED (and logged to the report)
---------------------------------------------
  - Rows with no parseable numeric ID NO, or an ID NO not present in
    students.
  - Rows with no parseable Date.
  - A Book ID with no matching title (can't insert into offline_library_usage
    with an unnamed book).
  - Any insert that trips a UNIQUE/FK constraint -- caught per-row and
    logged rather than aborting the whole load.

LOGGING
--------
This script writes only to its own report (default
offline_library_load_report.txt). Attendance, digital library, coaching,
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

from common import Canonicalizer, collapse_ws, normalize_key, parse_date

COL_DATE = 1
COL_ID = 2
COL_BOOK_ID = 8
COL_REF_BOOK = 9


class OfflineLibraryLoader:
    def __init__(self, conn):
        self.conn = conn
        self.book_cache = {}  # book_id -> canonical title actually stored
        self.book_id_title_key_counts = {}  # book_id -> {normalized_title_key: count}
        self.counts = {"offline_usage": 0}
        self.autocorrection_counts = {
            "book_title_merged": 0,
            "book_title_majority_vote": 0,
        }
        self.autocorrections = []
        self.review_notes = []
        self.skips = []
        self.book_title_canon = Canonicalizer(
            self.log_auto, self.log_review, "book_title_merged"
        )

    def log_auto(self, category, msg):
        self.autocorrection_counts[category] = (
            self.autocorrection_counts.get(category, 0) + 1
        )
        self.autocorrections.append(msg)

    def log_review(self, msg):
        self.review_notes.append(msg)

    def get_or_create_book(self, book_id, title, line_no=None):
        canonical_title = self.book_title_canon.canonicalize(
            title, context=f"line {line_no}, book_id {book_id!r}"
        )
        key = normalize_key(collapse_ws(title))
        key_counts = self.book_id_title_key_counts.setdefault(book_id, {})
        key_counts[key] = key_counts.get(key, 0) + 1

        if book_id in self.book_cache:
            return  # final title (majority vote) synced in finalize()
        self.book_cache[book_id] = canonical_title
        try:
            self.conn.execute(
                "INSERT INTO books (book_id, title) VALUES (?, ?)",
                (book_id, canonical_title),
            )
        except sqlite3.IntegrityError as e:
            self.skips.append(
                f"books insert failed for {book_id!r}/{canonical_title!r}: {e}"
            )

    def finalize_canonical_names(self):
        """
        book_id is reused across genuinely different titles often enough
        that "first title wins" would silently keep whichever one happened
        to load first, so every (canonicalized) title seen per book_id is
        tallied and the majority one kept, once the full CSV has been read.
        """
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
                self.skips.append(
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
                self.skips.append(
                    f"line {line_no}: offline_library_usage insert failed ({e}) -> SKIPPED"
                )


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--csv", required=True, type=Path)
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument(
        "--report", type=Path, default=Path("offline_library_load_report.txt")
    )
    args = ap.parse_args()

    if not args.db.exists():
        sys.exit(f"--db {args.db} does not exist. Load members into it first.")

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys = ON;")

    existing_student_ids = {
        r[0] for r in conn.execute("SELECT student_id FROM students")
    }

    loader = OfflineLibraryLoader(conn)
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

            book_id_raw = (
                collapse_ws(row[COL_BOOK_ID]) if len(row) > COL_BOOK_ID else ""
            )
            ref_book_raw = (
                collapse_ws(row[COL_REF_BOOK]) if len(row) > COL_REF_BOOK else ""
            )
            loader.load_books(student_id, date, book_id_raw, ref_book_raw, line_no)

    loader.finalize_canonical_names()
    conn.commit()

    totals = {}
    for t in ["offline_library_usage", "books"]:
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
            "\n=== POSSIBLE DUPLICATES NOT MERGED (similar title, kept separate -- please review) ===\n"
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
