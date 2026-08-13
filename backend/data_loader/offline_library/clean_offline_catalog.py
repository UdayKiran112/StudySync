#!/usr/bin/env python3
"""
Plan and apply cleanup of the Offline Library book catalog.

Scans the library database, groups every book_id under its cleaned title,
and plans merges of near-duplicate book IDs onto the most-used ID per title
(see book_cleaner.plan_id_merges for the conservative merge rules). Also
plans title renames for book rows whose stored spelling differs from the
cleaned/official one.

Dry-run by default: nothing is written to the database, only a report is
produced. With --apply --yes the script first backs up the database to
--backup-dir, then applies the merges (rewrites offline_library_usage.book_id
to the canonical ID and deletes the orphaned books rows) and title renames
in a single transaction.

Usage:
    python3 clean_offline_catalog.py --db library.db
    python3 clean_offline_catalog.py --db library.db --apply --yes
    python3 clean_offline_catalog.py --db library.db --report out.txt
"""

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

common_dir = Path(__file__).parent.parent
if str(common_dir) not in sys.path:
    sys.path.insert(0, str(common_dir))

from common import Canonicalizer, log_review_item, module_report_dir  # noqa: E402

from book_cleaner import (  # noqa: E402
    clean_title,
    plan_id_merges,
    plan_title_merges,
    _resolve_title,
)


def _noop(*_args, **_kwargs):
    pass


def collect_catalog(conn):
    """Return (book_rows, usage_total, null_usage), where book_rows is one
    (book_id, title, usage_count) tuple per books row."""
    book_rows = conn.execute(
        """
        SELECT b.book_id, b.title, COUNT(u.usage_id) AS usage_count
        FROM books b
        LEFT JOIN offline_library_usage u ON u.book_id = b.book_id
        GROUP BY b.book_id, b.title
        """
    ).fetchall()
    usage_total = conn.execute(
        "SELECT COUNT(*) FROM offline_library_usage"
    ).fetchone()[0]
    null_usage = conn.execute(
        "SELECT COUNT(*) FROM offline_library_usage WHERE book_id IS NULL"
    ).fetchone()[0]
    return book_rows, usage_total, null_usage


def build_plan(conn):
    """Group books by cleaned title and produce (merges, reviews, renames,
    title_to_ids, alias_reviews, title_merges, title_reviews, book_rows,
    usage_total, null_usage). Renames include both per-book alias spellings
    and whole-corpus title merges (variant title -> canonical title)."""
    book_rows, usage_total, null_usage = collect_catalog(conn)
    canon = Canonicalizer(_noop, _noop, "book_title_clean")
    cleaned_by_book = {}
    title_to_ids = {}
    renames = []
    alias_reviews = []

    def note_alias(msg):
        if msg not in alias_reviews:
            alias_reviews.append(msg)

    for book_id, title, _count in book_rows:
        cleaned = clean_title(
            title, canon=canon, context="catalog: ", log_review=note_alias
        )
        cleaned_by_book[book_id] = cleaned
        title_to_ids.setdefault(cleaned, {})[book_id] = _count

    # Whole-corpus title pass: catch word-order / significant misspelling /
    # phonetic variants the online Canonicalizer couldn't bridge.
    title_totals = {t: sum(ids.values()) for t, ids in title_to_ids.items()}
    title_merges, title_reviews = plan_title_merges(title_totals)
    title_remap = {m["variant"]: m["canonical"] for m in title_merges}

    # Remap titles so ID merges run in the unified post-title-merge space.
    remapped_title_to_ids = {}
    for title, ids in title_to_ids.items():
        target = _resolve_title(title, title_remap)
        merged = remapped_title_to_ids.setdefault(target, {})
        for book_id, count in ids.items():
            merged[book_id] = merged.get(book_id, 0) + count

    for book_id, title, _count in book_rows:
        cleaned = _resolve_title(cleaned_by_book[book_id], title_remap)
        if cleaned != title:
            renames.append((book_id, title, cleaned))

    merges, reviews = plan_id_merges(remapped_title_to_ids)
    return (
        merges,
        reviews,
        renames,
        remapped_title_to_ids,
        alias_reviews,
        title_merges,
        title_reviews,
        book_rows,
        usage_total,
        null_usage,
    )


def backup_db(conn, backup_dir):
    """Snapshot the connected database (WAL-aware) into a timestamped file
    under backup_dir using the sqlite backup API. Returns the new path."""
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = backup_dir / f"library_backup_{stamp}.db"
    bconn = sqlite3.connect(dest)
    try:
        conn.backup(bconn)
    finally:
        bconn.close()
    return dest


def apply_plan(conn, merges, renames):
    """Rewrite usage rows to canonical IDs, delete orphaned variant books,
    then apply title renames. All in one transaction. Returns
    (merged_count, renamed_count)."""
    for m in merges:
        conn.execute(
            "UPDATE offline_library_usage SET book_id = ? WHERE book_id = ?",
            (m["canonical_id"], m["variant_id"]),
        )
        conn.execute("DELETE FROM books WHERE book_id = ?", (m["variant_id"],))
    for book_id, _old_title, new_title in renames:
        conn.execute(
            "UPDATE books SET title = ? WHERE book_id = ?", (new_title, book_id)
        )
    conn.commit()
    return len(merges), len(renames)


def _fmt_id_list(ids):
    return ", ".join(f"{bid} ({cnt})" for bid, cnt in sorted(ids.items(), key=lambda kv: (-kv[1], kv[0])))


def write_report(path, args, mode, merges, reviews, renames, alias_reviews,
                 title_to_ids, title_merges, title_reviews, book_rows,
                 usage_total, null_usage, applied, renamed, backup_path):
    multi = sorted(
        (t, ids) for t, ids in title_to_ids.items() if len(ids) > 1
    )
    with path.open("w", encoding="utf-8") as f:
        f.write("Offline Library book-catalog cleanup report\n")
        f.write(f"Mode: {mode}\n")
        f.write(f"Run: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Database: {args.db}\n")
        if backup_path:
            f.write(f"Backup: {backup_path}\n")
        f.write(f"\nBooks rows: {len(book_rows)}\n")
        f.write(f"Offline library usage rows: {usage_total}\n")
        f.write(f"  with NULL book_id (unrecoverable, left alone): {null_usage}\n")
        f.write(f"Titles seen under more than one book_id: {len(multi)}\n\n")

        f.write("=== TITLE MERGES (variant title -> canonical title) ===\n")
        if title_merges:
            for m in title_merges:
                f.write(
                    f"  {m['variant']!r} ({m['variant_count']}) -> {m['canonical']!r} "
                    f"({m['canonical_count']}), similarity {m['score']} ({m['kind']})\n"
                )
        else:
            f.write("  none\n")
        f.write(f"\nTotal title merges: {len(title_merges)}"
                f"{' (applied via renames)' if mode == 'APPLIED' else ' (PROPOSED)'}\n")

        f.write("\n=== ID MERGES (variant -> canonical) ===\n")
        if merges:
            for m in merges:
                f.write(
                    f"  {m['title']!r}: {m['variant_id']} ({m['variant_count']}) -> "
                    f"{m['canonical_id']} ({m['canonical_count']})\n"
                )
        else:
            f.write("  none\n")
        f.write(f"\nTotal merges: {applied if mode == 'APPLIED' else len(merges)}"
                f"{' (APPLIED)' if mode == 'APPLIED' else ' (PROPOSED)'}\n")

        f.write("\n=== TITLE RENAMES (stored -> cleaned) ===\n")
        if renames:
            for book_id, old, new in renames:
                f.write(f"  book {book_id!r}: {old!r} -> {new!r}\n")
        else:
            f.write("  none\n")
        f.write(f"\nTotal renames: {renamed if mode == 'APPLIED' else len(renames)}"
                f"{' (APPLIED)' if mode == 'APPLIED' else ' (PROPOSED)'}\n")

        f.write("\n=== CURATED ALIAS APPLIED -- CONFIRM SPELLING ===\n")
        if alias_reviews:
            for msg in alias_reviews:
                f.write(f"  {msg}\n")
        else:
            f.write("  none\n")

        f.write("\n=== TITLE MATCHES FOR HUMAN REVIEW (with proposed spelling) ===\n")
        if title_reviews:
            for r in title_reviews:
                f.write(
                    f"  {r['variant']!r} ({r['variant_count']}) vs {r['canonical']!r} "
                    f"({r['canonical_count']}) -> similarity {r['score']} ({r['kind']}, "
                    f"phonetic {r.get('phonetic_score')}) -- proposed spelling {r['canonical']!r}\n"
                )
        else:
            f.write("  none\n")

        f.write("\n=== FOR HUMAN REVIEW (NOT auto-merged) ===\n")
        if reviews:
            for r in reviews:
                extra = ""
                if r["reason"] == "not_similar":
                    extra = f", lev {r['levenshtein']}"
                elif r["reason"] == "wrong_id_by_title":
                    extra = f", lev {r['levenshtein']}"
                suggestion = (
                    f" -- SUGGESTED correction: rewrite these rows to {r['suggest']!r}"
                    if r.get("suggest")
                    else ""
                )
                f.write(
                    f"  {r['title']!r}: {r['variant_id']} ({r['variant_count']}) vs "
                    f"{r['canonical_id']} ({r['canonical_count']}) -> {r['reason']}"
                    f"{extra}{suggestion}\n"
                )
        else:
            f.write("  none\n")

        f.write("\n=== TITLES WITH MULTIPLE BOOK IDS (full picture) ===\n")
        for title, ids in multi:
            f.write(f"  {title!r}: {_fmt_id_list(ids)}\n")

    print(f"Report written to {path}")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--db", required=True, type=Path, help="library.db to scan")
    ap.add_argument(
        "--apply",
        action="store_true",
        help="apply the planned merges/renames (requires --yes)",
    )
    ap.add_argument(
        "--yes",
        action="store_true",
        help="required with --apply: backup first, then rewrite the DB",
    )
    ap.add_argument("--backup-dir", type=Path, help="where the pre-apply backup goes")
    ap.add_argument(
        "--report",
        type=Path,
        default=module_report_dir("offline_library")
        / "book_catalog_clean_report.txt",
    )
    args = ap.parse_args()

    if args.apply and not args.yes:
        sys.exit("--apply requires --yes (a backup is taken before any write).")

    if not args.db.exists():
        sys.exit(f"--db {args.db} does not exist.")

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys = ON;")

    merges, reviews, renames, title_to_ids, alias_reviews, title_merges, title_reviews, book_rows, usage_total, null_usage = (
        build_plan(conn)
    )

    backup_path = None
    applied = renamed = 0
    mode = "DRY-RUN"
    if args.apply:
        backup_dir = args.backup_dir or (args.db.parent / "backups")
        backup_path = backup_db(conn, backup_dir)
        applied, renamed = apply_plan(conn, merges, renames)
        mode = "APPLIED"

    for msg in alias_reviews:
        log_review_item(
            {
                "table": "books",
                "problem": "book_title_alias_confirm",
                "detail": msg,
            }
        )
    for m in title_reviews:
        log_review_item(
            {
                "table": "books",
                "problem": f"book_title_merge_review:{m['kind']}",
                "detail": (
                    f"title {m['variant']!r} ({m['variant_count']}) vs canonical "
                    f"{m['canonical']!r} ({m['canonical_count']}), similarity "
                    f"{m['score']} (phonetic {m.get('phonetic_score')}) -- "
                    f"proposed spelling {m['canonical']!r}"
                ),
            }
        )
    for r in reviews:
        detail = (
            f"title {r['title']!r}: {r['variant_id']} ({r['variant_count']}) "
            f"vs canonical {r['canonical_id']} ({r['canonical_count']})"
        )
        if r.get("suggest"):
            detail += f" -- suggested correction: {r['suggest']!r}"
        log_review_item(
            {
                "table": "books",
                "problem": f"book_id_merge_review:{r['reason']}",
                "detail": detail,
            }
        )

    write_report(
        args.report, args, mode, merges, reviews, renames, alias_reviews,
        title_to_ids, title_merges, title_reviews, book_rows, usage_total,
        null_usage, applied, renamed, backup_path,
    )
    conn.close()

    print(f"[{mode}] {len(merges)} ID merges, {len(title_merges)} title merge(s), "
          f"{len(reviews)} ID review item(s), {len(title_reviews)} title review item(s), "
          f"{len(renames)} title rename(s), {len(alias_reviews)} alias confirm(s).")
    if args.apply:
        print(f"Applied: {applied} merges, {renamed} renames. Backup: {backup_path}")
    else:
        print("Re-run with --apply --yes to write the changes.")


if __name__ == "__main__":
    main()
