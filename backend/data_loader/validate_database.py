#!/usr/bin/env python3
"""
Post-load validation for the StudySync library database.

Run against a freshly rebuilt backend/library.db after every loader has
run. Verifies the invariants the whole application depends on -- the big
one being that an exam mark can never exceed its exam's max marks (batch
averages are computed as a percentage of max, so a single over-max mark
inflates every average/rank it touches). This is the last gate before the
pipeline calls the database done.

    python3 validate_database.py --db library.db

Checks (each is FATAL unless marked WARN):
  FK      PRAGMA foreign_key_check -- every row in a child table must
          reference a real parent row.           [FATAL if any]
  marks   exam_marks.marks_obtained <= exams.max_marks whenever max is
          known, and >= 0.                        [FATAL if any]
  dup     duplicate (student_id, exam_id) pairs and same-name same-date
          exams that should have merged.         [FATAL / WARN]
  dates   exam dates outside the plausible 2005..today window, or NULL
          where a marks-carrying exam needs one.  [WARN]
  avgs    per-exam percentage averages (avg/max*100) -- anything over
          100% is impossible; values above 95% or below 5% on a
          multi-student exam are suspicious but allowed. [FATAL if >100]
  kids    child rows referencing a missing parent. [FATAL if any]

Exit code is 0 when only WARN-level items exist, 1 when any FATAL item is
found (the pipeline aborts so a corrupt database is never "done").

Report: reports/validation/validate_database_report.txt
"""

import argparse
import sqlite3
import sys
from pathlib import Path

import common

FATAL = "FATAL"
WARN = "WARN"

TABLES = [
    "students",
    "attendance",
    "subscriptions",
    "digital_library_usage",
    "books",
    "offline_library_usage",
    "exams",
    "exam_marks",
    "quizzes",
    "quiz_scores",
    "coaching_classes",
    "coaching_enrollments",
    "instructors",
    "external_participants",
]


def validate(conn):
    findings = []  # (level, message)

    # --- referential integrity -------------------------------------------------
    bad_fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    for row in bad_fk:
        table, rowid, parent, fkid = row[:4]
        findings.append(
            (FATAL, f"FK violation: {table} row {rowid} -> missing {parent} "
             f"(constraint index {fkid})")
        )

    # --- marks never exceed max ------------------------------------------------
    over = conn.execute(
        """
        SELECT em.student_id, em.marks_obtained, e.exam_id, e.exam_name,
               e.exam_date, e.max_marks
        FROM exam_marks em
        JOIN exams e ON e.exam_id = em.exam_id
        WHERE e.max_marks IS NOT NULL AND em.marks_obtained > e.max_marks
        ORDER BY e.exam_date
        """
    ).fetchall()
    for sid, marks, eid, ename, edate, mx in over:
        findings.append(
            (FATAL, f"marks > max: student {sid} scored {marks} on {ename!r} "
             f"({edate}, exam {eid}) whose max is {mx}")
        )

    negative = conn.execute(
        "SELECT COUNT(*) FROM exam_marks WHERE marks_obtained < 0"
    ).fetchone()[0]
    if negative:
        findings.append((FATAL, f"{negative} exam_marks rows with marks_obtained < 0"))

    # --- per-exam percentage averages -----------------------------------------
    avgs = conn.execute(
        """
        SELECT e.exam_id, e.exam_name, e.exam_date, e.max_marks,
               COUNT(em.mark_id), AVG(em.marks_obtained)
        FROM exams e
        JOIN exam_marks em ON em.exam_id = e.exam_id
        WHERE e.max_marks IS NOT NULL
        GROUP BY e.exam_id
        ORDER BY e.exam_date
        """
    ).fetchall()
    for eid, ename, edate, mx, n, avg in avgs:
        pct = avg / mx * 100.0
        if pct > 100.0:
            findings.append(
                (FATAL, f"exam {eid} {ename!r} ({edate}): avg {avg:.2f}/{mx} "
                        f"= {pct:.1f}% -- impossible for a percentage")
            )
        elif pct > 95.0 and n >= 5:
            findings.append(
                (WARN, f"exam {eid} {ename!r} ({edate}): avg {avg:.2f}/{mx} "
                       f"= {pct:.1f}% on {n} students -- high, spot-check")
            )
        elif pct < 5.0 and n >= 5:
            findings.append(
                (WARN, f"exam {eid} {ename!r} ({edate}): avg {avg:.2f}/{mx} "
                       f"= {pct:.1f}% on {n} students -- low, spot-check")
            )

    # --- exams carrying marks but no max ---------------------------------------
    no_max = conn.execute(
        """
        SELECT e.exam_id, e.exam_name, e.exam_date, COUNT(em.mark_id)
        FROM exams e
        JOIN exam_marks em ON em.exam_id = e.exam_id
        WHERE e.max_marks IS NULL
        GROUP BY e.exam_id
        ORDER BY e.exam_date
        """
    ).fetchall()
    for eid, ename, edate, n in no_max:
        findings.append(
            (WARN, f"exam {eid} {ename!r} ({edate}) has {n} marks but no "
                   f"max_marks -- percentage averages are undefined for it")
        )

    # --- duplicate rows --------------------------------------------------------
    dup_marks = conn.execute(
        """
        SELECT student_id, exam_id, COUNT(*)
        FROM exam_marks
        GROUP BY student_id, exam_id
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    for sid, eid, n in dup_marks:
        findings.append(
            (FATAL, f"duplicate exam_marks: student {sid}, exam {eid} has {n} rows")
        )

    dup_exams = conn.execute(
        """
        SELECT exam_date, GROUP_CONCAT(exam_name, ' | '), COUNT(*)
        FROM exams
        WHERE exam_date IS NOT NULL
        GROUP BY exam_date, subject
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    for edate, names, n in dup_exams:
        findings.append(
            (WARN, f"same-date exams on {edate} with same subject: {names}")
        )

    # --- date sanity -----------------------------------------------------------
    for eid, ename, edate in conn.execute(
        "SELECT exam_id, exam_name, exam_date FROM exams"
    ):
        if edate is None:
            findings.append(
                (WARN, f"exam {eid} {ename!r} has no exam_date")
            )
            continue
        if not ("2005-01-01" <= edate <= "2026-12-31"):
            findings.append(
                (WARN, f"exam {eid} {ename!r} has out-of-range date {edate}")
            )

    return findings


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, type=Path)
    args = ap.parse_args()

    if not args.db.exists():
        sys.exit(f"--db {args.db} does not exist.")

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys = ON;")

    counts = {}
    for t in TABLES:
        try:
            counts[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.Error:
            counts[t] = "?"

    findings = validate(conn)
    conn.close()

    fatal = [f for f in findings if f[0] == FATAL]
    warns = [f for f in findings if f[0] == WARN]

    report = common.module_report_dir("validation") / "validate_database_report.txt"
    with report.open("w", encoding="utf-8") as f:
        f.write("STUDYSYNC DATABASE VALIDATION\n")
        f.write("=" * 60 + "\n\n")
        f.write("Row counts:\n")
        for t in TABLES:
            f.write(f"  {t}: {counts[t]}\n")
        f.write(f"\nFATAL findings: {len(fatal)}\n")
        for level, msg in fatal:
            f.write(f"  [{level}] {msg}\n")
        f.write(f"\nWARN findings: {len(warns)}\n")
        for level, msg in warns:
            f.write(f"  [{level}] {msg}\n")
        if not findings:
            f.write("\nAll checks passed.\n")
        f.write(f"\n{'VALIDATION FAILED' if fatal else 'VALIDATION PASSED'}\n")

    print(f"Row counts: {counts}")
    print(f"Validation findings: {len(fatal)} FATAL, {len(warns)} WARN")
    for level, msg in fatal:
        print(f"  [{level}] {msg}")
    for level, msg in warns:
        print(f"  [{level}] {msg}")
    print(f"Validation report: {report}")

    if fatal:
        sys.exit(1)


if __name__ == "__main__":
    main()
