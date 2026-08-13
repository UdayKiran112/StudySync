#!/usr/bin/env python3
"""
Load offline (physical) library book usage into the library SQLite database
from the CLEANED offline_library.csv produced by clean_student_data.py --
not from the raw students_activity.csv export.

Usage:
    python3 clean_student_data.py students_activity.csv cleaned_output/
    python3 load_offline_library.py --csv cleaned_output/offline_library.csv --db library.db

Requires that library.db already exists and its `students` table is
already populated (e.g. via load_members.py) -- every row here is linked
to an existing student purely by "Student ID", never by name.

WHY THIS READS THE CLEANED CSV INSTEAD OF THE RAW EXPORT
-----------------------------------------------------------
clean_student_data.py already splits a comma cell like "1556, 1556" /
"Polity, Polity" into one row per book (fixing the '.' vs ',' separator
typo along the way), so this loader trusts one Book ID + one Book Name per
row instead of re-doing that split itself.

WHAT THIS LOADS, PER CLEANED CSV ROW
---------------------------------------
  Student ID, Date          -> the student/date this usage belongs to.
  Book ID + Book Name       -> books (auto-created master rows) and
                                offline_library_usage.

Book titles are run through book_cleaner.clean_title -- a curated alias
table for the abbreviations and short forms edit-distance can't bridge
(e.g. 'Hiteck' -> 'Hi-Tech Vijaya Rahasyam', 'G Science' -> 'General
Science'), then the generic common.Canonicalizer for pure spelling/case/
spacing variants and close typos -- so the books table doesn't grow a new
row per typo (e.g. 'Shine India' / 'Shine india' / 'Merit Mind' / 'Merit
Minds' collapsing to one entry). Because the same book_id can legitimately
show up with several genuinely DIFFERENT titles (book_id looks reused
rather than a stable 1:1 catalog key), every title seen per book_id is
tallied and the majority title wins -- finalized only after the whole CSV
has been read (see finalize_canonical_names()).

NEAR-DUPLICATE BOOK IDs ARE AUTO-MERGED
-----------------------------------------
Once every row has been read, the same book_id in the same book can also
appear under typo'd/transposed/junk-suffixed IDs (e.g. '5Gtech' books
recorded as '1680', '680', '168'). finalize_canonical_names() runs
book_cleaner.plan_id_merges over the per-title ID tallies and rewrites any
rare near-duplicate ID's usage rows onto the most-used ID for that title,
deleting the orphaned books row. IDs that are frequent (genuinely distinct
catalog entries) or too different to be a typo are logged for human review,
never merged.

BOOK ID WITH NO TITLE ON ITS OWN ROW -- BACKFILLED WHERE POSSIBLE
---------------------------------------------------------------------
error_log_offline_library.log flags rows where a Book ID was recorded but
the source spreadsheet never filled in a name for that visit. Before
giving up on those, this loader first scans the WHOLE cleaned CSV for any
OTHER row that recorded a title against that same Book ID, and reuses the
most common one it finds (see prescan_book_titles()). Only if a Book ID
truly never has a title anywhere in the file is the row skipped (books.title
is NOT NULL, so an entry can't be created out of nothing) -- these are
still logged so the remainder can be filled in by hand from the physical
catalog.

WHAT ELSE GETS SKIPPED (and logged to the report)
---------------------------------------------------
  - Rows with no parseable numeric Student ID, or a Student ID not present
    in students.
  - Rows with no parseable Date.
  - A Book ID with no title anywhere in the file (see above).
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
import os
from pathlib import Path

common_dir = Path(__file__).parent.parent
if str(common_dir) not in sys.path:
    sys.path.insert(0, str(common_dir))

from common import (
    Canonicalizer,
    collapse_ws,
    log_review_item,
    module_report_dir,
    parse_date,
)
from book_cleaner import clean_title, plan_id_merges, plan_title_merges, _resolve_title


def prescan_book_titles(rows):
    """First pass over the cleaned CSV: for every Book ID, tally the raw
    (whitespace-collapsed) titles actually recorded against it on OTHER
    rows. Returns {book_id: most_common_raw_title}, used to backfill rows
    where this particular visit's Book Name was left blank."""
    titles_by_book_id = {}
    for row in rows:
        bid = collapse_ws(row.get("Book ID") or "")
        name = collapse_ws(row.get("Book Name") or "")
        if bid and name:
            counts = titles_by_book_id.setdefault(bid, {})
            counts[name] = counts.get(name, 0) + 1
    return {
        bid: max(counts, key=counts.get) for bid, counts in titles_by_book_id.items()
    }


class OfflineLibraryLoader:
    def __init__(self, conn):
        self.conn = conn
        self.book_cache = {}  # book_id -> canonical title actually stored
        self.book_id_title_key_counts = {}  # book_id -> {normalized_title_key: count}
        self.counts = {"offline_usage": 0}
        self.autocorrection_counts = {
            "book_title_merged": 0,
            "book_title_majority_vote": 0,
            "book_title_backfilled": 0,
            "book_id_merged": 0,
        }
        self.autocorrections = []
        self.review_notes = []
        self.skips = []
        self.book_title_canon = Canonicalizer(
            self.log_auto, self.log_review, "book_title_merged"
        )
        self.id_title_counts = {}  # cleaned_title -> {book_id: usage_rows}

    def log_auto(self, category, msg):
        self.autocorrection_counts[category] = (
            self.autocorrection_counts.get(category, 0) + 1
        )
        self.autocorrections.append(msg)

    def log_review(self, msg):
        self.review_notes.append(msg)

    def get_or_create_book(self, book_id, title, line_no=None):
        canonical_title = clean_title(
            title,
            canon=self.book_title_canon,
            context=f"line {line_no}, book_id {book_id!r}: ",
        )
        key_counts = self.book_id_title_key_counts.setdefault(book_id, {})
        key_counts[canonical_title] = key_counts.get(canonical_title, 0) + 1
        id_counts = self.id_title_counts.setdefault(canonical_title, {})
        id_counts[book_id] = id_counts.get(book_id, 0) + 1

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
        """book_id is reused across genuinely different titles often enough
        that "first title wins" would silently keep whichever one happened
        to load first, so every (cleaned) title seen per book_id is tallied
        and the majority one kept, once the full CSV has been read.

        Then the whole-corpus title pass (book_cleaner.plan_title_merges)
        collapses rare titles that are near-duplicates of a more common one
        (word-order swaps, significant misspellings, phonetic homophones),
        and finally rare near-duplicate book IDs are merged onto the
        most-used ID for their title (book_cleaner.plan_id_merges)."""
        updated_books = 0
        conflicted_book_ids = 0
        for book_id, tallies in self.book_id_title_key_counts.items():
            winner = max(tallies, key=tallies.get)
            if len(tallies) > 1:
                conflicted_book_ids += 1
                total = sum(tallies.values())
                ranked = sorted(tallies.items(), key=lambda kv: -kv[1])
                breakdown = ", ".join(
                    f"{t!r} ({c}/{total})"
                    for t, c in ranked
                )
                self.log_auto(
                    "book_title_majority_vote",
                    f"book_id {book_id!r}: seen with {len(tallies)} different titles "
                    f"-- {breakdown} -> kept {winner!r} (majority vote)",
                )
                # A significant runner-up share means either the ID is reused
                # for different real books or some rows carry a wrong ID --
                # surface it for review rather than silently voting it away.
                runner_title, runner_count = ranked[1]
                if runner_count >= 2 and runner_count / total >= 0.25:
                    msg = (
                        f"book_id {book_id!r}: title split {winner!r} ({ranked[0][1]}/{total}) "
                        f"vs {runner_title!r} ({runner_count}/{total}) -- reused ID or wrong "
                        f"ID on some rows, review"
                    )
                    self.review_notes.append(msg)
                    log_review_item(
                        {
                            "table": "books",
                            "problem": "book_id_title_split",
                            "detail": msg,
                        }
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

        # Whole-corpus title pass (after majority vote, so titles are stable).
        rebuilt = {}
        for book_id, tallies in self.book_id_title_key_counts.items():
            winner = max(tallies, key=tallies.get)
            rebuilt.setdefault(winner, {})[book_id] = sum(tallies.values())
        title_totals = {t: sum(ids.values()) for t, ids in rebuilt.items()}
        title_merges, title_reviews = plan_title_merges(title_totals)
        title_remap = {m["variant"]: m["canonical"] for m in title_merges}

        for book_id, tallies in self.book_id_title_key_counts.items():
            winner = max(tallies, key=tallies.get)
            target = _resolve_title(winner, title_remap)
            if target != self.book_cache.get(book_id):
                self.conn.execute(
                    "UPDATE books SET title = ? WHERE book_id = ?", (target, book_id)
                )
                self.book_cache[book_id] = target

        for m in title_merges:
            self.log_auto(
                "book_title_merged",
                f"title {m['variant']!r} ({m['variant_count']} row(s)) -> "
                f"{m['canonical']!r} ({m['canonical_count']} row(s)), similarity "
                f"{m['score']} ({m['kind']}) -- near-duplicate title, auto-merged",
            )
        for tm in title_reviews:
            msg = (
                f"title {tm['variant']!r} ({tm['variant_count']} row(s)) vs "
                f"{tm['canonical']!r} ({tm['canonical_count']} row(s)), similarity "
                f"{tm['score']} ({tm['kind']}, phonetic {tm.get('phonetic_score')}) "
                f"-- proposed spelling {tm['canonical']!r}, review"
            )
            self.review_notes.append(msg)
            log_review_item(
                {
                    "table": "books",
                    "problem": f"book_title_merge_review:{tm['kind']}",
                    "detail": msg,
                }
            )

        # ID merges run in the unified post-title-merge space.
        rebuilt = {}
        for book_id, tallies in self.book_id_title_key_counts.items():
            winner = max(tallies, key=tallies.get)
            target = _resolve_title(winner, title_remap)
            rebuilt.setdefault(target, {})[book_id] = sum(tallies.values())

        merges, reviews = plan_id_merges(rebuilt)
        for m in merges:
            self.conn.execute(
                "UPDATE offline_library_usage SET book_id = ? WHERE book_id = ?",
                (m["canonical_id"], m["variant_id"]),
            )
            self.conn.execute(
                "DELETE FROM books WHERE book_id = ?", (m["variant_id"],)
            )
            self.log_auto(
                "book_id_merged",
                f"{m['title']!r}: book_id {m['variant_id']!r} "
                f"({m['variant_count']} row(s)) -> {m['canonical_id']!r} "
                f"({m['canonical_count']} row(s)) -- near-duplicate ID, auto-merged",
            )
        for r in reviews:
            msg = (
                f"{r['title']!r}: book_id {r['variant_id']!r} "
                f"({r['variant_count']} row(s)) vs canonical {r['canonical_id']!r} "
                f"({r['canonical_count']} row(s)) -> kept separate, review ({r['reason']})"
            )
            if r.get("suggest"):
                msg += f"; suggested correction {r['suggest']!r}"
            self.review_notes.append(msg)
            log_review_item(
                {
                    "table": "books",
                    "problem": f"book_id_merge_review:{r['reason']}",
                    "detail": msg,
                }
            )
        if merges:
            self.log_auto(
                "book_title_merged",
                f"finalize: merged {len(merges)} near-duplicate book_id(s) onto their "
                f"most-used sibling",
            )

    def load_book_usage(
        self, student_id, date, book_id_raw, book_name_raw, backfill_titles, line_no
    ):
        bid = collapse_ws(book_id_raw) or None
        name = collapse_ws(book_name_raw) or None

        if not bid and not name:
            self.skips.append(
                f"line {line_no}: row has neither a Book ID nor a Book Name -> "
                f"SKIPPED (nothing to record)"
            )
            log_review_item(
                {
                    "table": "offline_library_usage",
                    "row": line_no,
                    "student_id": student_id,
                    "date": date,
                    "problem": "empty_usage_row",
                    "detail": f"offline library row {line_no} has no book id and no book name",
                }
            )
            return

        if bid and not name:
            backfilled = backfill_titles.get(bid)
            if backfilled:
                name = backfilled
                self.log_auto(
                    "book_title_backfilled",
                    f"line {line_no}: book_id {bid!r} had no title recorded on this "
                    f"visit -- backfilled with {name!r}, seen elsewhere in the file "
                    f"for the same book_id",
                )
            else:
                self.skips.append(
                    f"line {line_no}: book id {bid!r} has no title anywhere in the "
                    f"file -> entry SKIPPED (fill in the title by hand and re-run)"
                )
                log_review_item(
                    {
                        "table": "offline_library_usage",
                        "row": line_no,
                        "student_id": student_id,
                        "date": date,
                        "problem": "no_book_title",
                        "detail": f"book_id {bid!r} has no title anywhere in the file",
                    }
                )
                return

        if bid:
            self.get_or_create_book(bid, name, line_no)

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
            log_review_item(
                {
                    "table": "offline_library_usage",
                    "row": line_no,
                    "student_id": student_id,
                    "date": date,
                    "problem": "insert_failed",
                    "detail": f"book_id {bid!r}, {e}",
                }
            )


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--csv", required=True, type=Path, help="cleaned offline_library.csv"
    )
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument(
        "--report",
        type=Path,
        default=module_report_dir("offline_library") / "offline_library_load_report.txt",
    )
    args = ap.parse_args()

    if not args.db.exists():
        sys.exit(f"--db {args.db} does not exist. Load members into it first.")

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys = ON;")

    existing_student_ids = {
        r[0] for r in conn.execute("SELECT student_id FROM students")
    }

    with args.csv.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    backfill_titles = prescan_book_titles(rows)

    loader = OfflineLibraryLoader(conn)
    skipped_id = 0
    skipped_date = 0
    total_rows = 0

    for line_no, row in enumerate(rows, start=2):  # +1 for header row
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
                    "table": "offline_library_usage",
                    "row": line_no,
                    "student_id": id_raw,
                    "date": row.get("Date", ""),
                    "problem": "student_id_not_found",
                    "detail": f"offline library row {line_no}",
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
                    "table": "offline_library_usage",
                    "row": line_no,
                    "student_id": id_raw,
                    "date": row.get("Date", ""),
                    "problem": "unparseable_date",
                    "detail": f"offline library row {line_no}",
                }
            )
            continue

        loader.load_book_usage(
            student_id,
            date,
            row.get("Book ID", ""),
            row.get("Book Name", ""),
            backfill_titles,
            line_no,
        )

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
            "\n=== PER-ROW AUTO-CORRECTIONS (loaded, but adjusted from the cleaned CSV) ===\n"
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
