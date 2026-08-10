#!/usr/bin/env python3
"""
End-to-end data pipeline for the StudySync library database.

    python3 run_pipeline.py [--python PY] [--skip-clean]

Steps, in order:
  1. reset the shared manual-review ledger
     (backend/data_loader/review/review_items.jsonl);
  2. clean_student_data.py  student_details.csv  -> per-section cleaned CSVs
     (this is where the operating-hours / 12h-clock corrections, the
     offline-exam section builder, and the book-ID junk filter live);
  3. organize_internal_marks.py                  -> marks/internal_marks_organized.csv
  4. rebuild backend/library.db from schema.sql (wipe + recreate);
  5. load_members.py                             -> students (all FKs depend on it);
   6. section loaders, in dependency-safe order:
       load_attendance, load_digital_library, load_offline_library,
       load_coaching,
       load_exam_marks (marks register -- the ONLY source of real scores;
       creates the exams + exam_marks rows, all with marks_obtained set);
       load_offline_exam (offline-exam sittings WITHOUT a score in the
       register are not inserted -- marks_obtained is NOT NULL -- and are
       flagged for manual review instead);
   7. backfill_renewals.py                     -> students.renewal_count / status
      (replays the attendance auto-renewal math once attendance is loaded,
      so current members are never left marked 'Inactive' at rest);
   8. validate_database.py                     -> final gate: abort if any
      invariant is broken (marks must never exceed an exam's max, no orphan
      rows, no impossible averages, no duplicates);
   9. consolidate the manual-review ledger into one report and print final
      row counts.

Everything a loader cannot safely auto-correct is written to the shared
review ledger during step 6 and rendered into the consolidated report in
step 8, so a human gets one file to review instead of digging through each
loader's individual report.

Reports: every report and log the pipeline produces lands in the shared
reports tree, one subfolder per module:

    backend/data_loader/reports/
      members/          members_load_report.txt, members_gender_report.txt
      attendance/       attendance_load_report*.txt, error/corrections logs
      digital_library/  digital_library_load_report.txt, ...
      offline_library/  offline_library_load_report.txt, ...
      coaching/         coaching_load_report.txt, ...
      marks/            exam_marks_load_report*.txt,
                        offline_exam_load_report.txt
      review/           review_items.jsonl + the consolidated
                        manual_review_report.txt
      pipeline_run_report.txt   this pipeline's summary

The whole reports/ tree is gitignored -- it is regenerated every run.

--python overrides the interpreter used for the sub-steps (defaults to
the interpreter this script itself is running under).
"""

import argparse
import json
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

import common

BASE = Path(__file__).resolve().parent
BACKEND = BASE.parent
DB = BACKEND / "library.db"
SCHEMA = BACKEND / "schema.sql"
RAW_ACTIVITY = BASE / "student_details.csv"
MEMBERS_CSV = BASE / "members" / "member_details.csv"
MARKS_REGISTER = BASE / "marks" / "internal_marks_organized.csv"

LEDGER = common.LEDGER_PATH
REVIEW_REPORT = common.LEDGER_DIR / "manual_review_report.txt"
PIPELINE_REPORT = common.REPORTS_DIR / "pipeline_run_report.txt"

SECTIONS = [
    ("attendance", "attendance/attendance.csv", "attendance/load_attendance.py"),
    (
        "digital_library",
        "digital_library/digital_library.csv",
        "digital_library/load_digital_library.py",
    ),
    (
        "offline_library",
        "offline_library/offline_library.csv",
        "offline_library/load_offline_library.py",
    ),
    (
        "coaching",
        "coaching/digital_class.csv",
        "coaching/load_coaching.py",
    ),
]

# tables (in FK order) counted for the final summary
COUNT_TABLES = [
    "students",
    "attendance",
    "subscriptions",
    "digital_library_usage",
    "books",
    "offline_library_usage",
    "exams",
    "exam_marks",
    "coaching_classes",
    "coaching_enrollments",
]


def run_step(python, script, args, cwd=None, allow_fail=False):
    cmd = [str(python), str(script)] + [str(a) for a in args]
    print(f"\n>>> {subprocess.list2cmdline(cmd)}")
    result = subprocess.run(cmd, cwd=cwd or BASE)
    if result.returncode != 0 and not allow_fail:
        print(f"FATAL: step failed with exit code {result.returncode}; aborting.")
        sys.exit(result.returncode)
    return result.returncode


def rebuild_db(python):
    # A WAL-mode database keeps live state in sidecar -wal/-shm files; if
    # those survive the main file's deletion, a fresh connection can try to
    # replay them against an empty main file. Wipe all three together.
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(DB) + suffix)
        if path.exists():
            path.unlink()
            print(f"\n>>> removed existing {path} (rebuild in place)")
    run_step(
        python,
        BASE / "members" / "load_members.py",
        ["--csv", MEMBERS_CSV, "--db", DB, "--schema", SCHEMA],
    )


def load_sections(python):
    for name, csv_rel, loader_rel in SECTIONS:
        run_step(
            python,
            BASE / loader_rel,
            ["--csv", BASE / csv_rel, "--db", DB],
        )
    # Marks register first: it is the ONLY source of real scores and owns
    # exam_marks creation (marks_obtained is NOT NULL in the schema).
    run_step(
        python,
        BASE / "marks" / "load_exam_marks.py",
        ["--csv", MARKS_REGISTER, "--db", DB],
    )
    # Then the offline-exam sittings: ones the register never scored are
    # NOT inserted -- they're flagged for manual review instead.
    run_step(
        python,
        BASE / "marks" / "load_offline_exam.py",
        ["--csv", BASE / "marks" / "offline_exam.csv", "--db", DB],
    )


def count_rows():
    conn = sqlite3.connect(DB)
    totals = {}
    for t in COUNT_TABLES:
        try:
            totals[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.Error:
            totals[t] = "?"
    conn.close()
    return totals


def render_review_report():
    by_problem = Counter()
    by_table = Counter()
    details = defaultdict(list)
    if LEDGER.exists():
        with LEDGER.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                problem = entry.get("problem", "unknown")
                by_problem[problem] += 1
                by_table[entry.get("table", "?")] += 1
                if len(details[problem]) < 8:
                    details[problem].append(
                        {
                            k: entry.get(k)
                            for k in ("student_id", "student", "date", "row", "detail")
                        }
                    )
    REVIEW_REPORT.parent.mkdir(parents=True, exist_ok=True)
    with REVIEW_REPORT.open("w", encoding="utf-8") as f:
        f.write("MANUAL REVIEW REPORT -- rows the pipeline could not safely auto-correct\n")
        f.write("=" * 72 + "\n\n")
        f.write(f"Total review items: {sum(by_problem.values())}\n\n")
        if by_table:
            f.write("By source table:\n")
            for t, c in sorted(by_table.items()):
                f.write(f"  {t}: {c}\n")
            f.write("\n")
        if by_problem:
            f.write("By problem type:\n")
            for p, c in by_problem.most_common():
                f.write(f"  {p}: {c}\n")
            f.write("\n")
        for p, c in by_problem.most_common():
            f.write(f"\n--- {p} ({c}) ---\n")
            for d in details[p]:
                f.write("  " + json.dumps(d, ensure_ascii=False) + "\n")
        if not by_problem:
            f.write("(no review items -- every row was auto-handled)\n")
    return REVIEW_REPORT


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--skip-clean", action="store_true")
    args = ap.parse_args()

    print("=== StudySync data pipeline ===")
    common.reset_review_ledger()
    print("Review ledger reset.")

    if not args.skip_clean:
        run_step(args.python, BASE / "clean_student_data.py", [RAW_ACTIVITY])
    else:
        print("Skipping clean step (--skip-clean).")

    run_step(args.python, BASE / "organize_internal_marks.py", [], allow_fail=True)

    rebuild_db(args.python)
    load_sections(args.python)

    run_step(
        args.python,
        BASE / "members" / "backfill_renewals.py",
        ["--db", DB],
    )

    # Final gate: every loader has run and the database is at rest. Aborts
    # the run (non-zero exit) if any FATAL invariant is broken.
    run_step(args.python, BASE / "validate_database.py", ["--db", DB])

    totals = count_rows()
    print("\n=== Final row counts ===")
    for t in COUNT_TABLES:
        print(f"  {t}: {totals[t]}")

    review_path = render_review_report()
    print(f"\nConsolidated manual-review report: {review_path}")

    PIPELINE_REPORT.parent.mkdir(parents=True, exist_ok=True)
    with PIPELINE_REPORT.open("w", encoding="utf-8") as f:
        f.write("StudySync pipeline run summary\n")
        f.write("=" * 72 + "\n\n")
        f.write("Final row counts:\n")
        for t in COUNT_TABLES:
            f.write(f"  {t}: {totals[t]}\n")
        f.write(f"\nManual-review report: {review_path}\n")
    print(f"Pipeline summary: {PIPELINE_REPORT}")


if __name__ == "__main__":
    main()
