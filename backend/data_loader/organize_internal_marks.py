#!/usr/bin/env python3
"""
Clean the raw internal_marks.csv exam-marks register (an Excel
merged-cell export) into internal_marks_organized.csv -- one fully-resolved
row per (student, exam) mark, ready for load_exam_marks.py --marks-csv.

Usage:
    python3 organize_internal_marks.py --csv internal_marks.csv \\
        --out internal_marks_organized.csv

This is a pure CSV-to-CSV cleaning step: it does no database work and
doesn't know about student IDs -- matching a name to students.student_id
only happens later, in load_exam_marks.py, which is the only place that
has the roster to match against.

WHY THIS NEEDS TO EXIST
-------------------------
The register only fills in Name of the Exam / Date / Max. Marks on the
FIRST row of each block (a merged Excel cell exported as one filled row
followed by several blank ones), e.g.:

    ,,Ari & Rea,16.07.2021,30,
    1,Siva B,,,,18
    2,Rajesh Y,,,,21

Every row after the first in a block needs those three values carried
forward from above. Blindly carrying forward is risky, though: a stray
mis-keyed value partway through a block (a student ID typed into the Exam
column, a date typed where a number was expected) would otherwise silently
overwrite the block's real header and corrupt every row that follows. So a
candidate forward-fill value is only accepted if it actually looks valid
for that field:
    - Name of the Exam: must contain a letter (a bare number doesn't look
      like a topic).
    - Date: must parse via common.parse_date.
    - Max. Marks: must be numeric.
Anything that fails is rejected (not forward-filled) and logged, and the
last good value keeps being used instead.

WHAT GETS DROPPED (and logged to the report)
-----------------------------------------------
  - Fully blank filler rows (no name at all).
  - A named row with no valid topic/date established yet for its block
    (the block's header was missing or rejected).
  - A named row with a blank or non-numeric Marks Obtained -- this
    register exists purely to supply scores, so a row with nothing to
    contribute isn't written out.

STUDENT ID OVERRIDE (a quirk of this register)
-------------------------------------------------
Most blocks leave the Exam-name column blank after the block's first row
(the normal forward-fill case above). A few blocks instead put a numeric
Student ID there on every row -- apparently added to disambiguate students
who share a name, since this register otherwise only has a name column.
That's captured per-row as a Student ID override (not forward-filled, and
not treated as a rejected topic value) so load_exam_marks.py can match
those rows by ID directly instead of by name, which is more reliable.

OUTPUT COLUMNS
----------------
  Sl.No, Student ID (blank except where the quirk above applies),
  Name of Student, Date of Exam (DD-MM-YYYY, matching the other cleaned
  CSVs in this project), Name of Exam, Marks Obtained, Max Marks
"""

import argparse
import csv
import re
import sys
from datetime import datetime
from pathlib import Path

from common import collapse_ws, parse_date


def looks_like_topic(cell: str) -> bool:
    return bool(re.search(r"[A-Za-z]", cell))


def looks_numeric(cell: str) -> bool:
    return bool(re.match(r"^\d+(\.\d+)?$", cell))


def normalize_marks_value(cell: str):
    """Recovers a decimal mark from a colon-typo like '18:50' -> '18.50'
    (surrounding marks in the same block are consistently decimal, e.g.
    '28.5', so this is the same ':' vs '.' slip seen in IN/OUT attendance
    times, not a legitimate ratio). Returns None if not this exact shape."""
    if re.fullmatch(r"\d+:\d+", cell):
        return cell.replace(":", ".")
    return None


def organize(src: Path, out: Path, report_path: Path):
    with src.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))

    header_idx = None
    for i, row in enumerate(rows):
        if row and collapse_ws(row[0]).lower() == "sl no":
            header_idx = i
            break
    if header_idx is None:
        sys.exit(f"{src}: no 'Sl No' header row found -- can't organize this file.")

    rejections = []
    skips = []
    corrections = []
    written = []

    cur_topic_raw, cur_date_iso, cur_max = None, None, None

    for line_no, row in enumerate(rows[header_idx + 1 :], start=header_idx + 2):
        if len(row) < 6:
            row = row + [""] * (6 - len(row))
        name = collapse_ws(row[1])
        topic_cell = collapse_ws(row[2])
        date_cell = collapse_ws(row[3])
        max_cell = collapse_ws(row[4])
        marks_cell = collapse_ws(row[5])

        # Forward-fill each of the three block-header fields, but only from
        # a value that actually looks valid for that field. In some blocks
        # of this register, though, the Exam-name column holds a numeric
        # Student ID instead (apparently added to disambiguate students
        # sharing a name) -- that's a per-ROW override, not a forward-fill
        # candidate, and it's a more reliable match than name alone, so
        # it's captured separately rather than rejected outright.
        row_student_id = None
        if topic_cell:
            if looks_like_topic(topic_cell):
                cur_topic_raw = topic_cell
            elif looks_numeric(topic_cell):
                row_student_id = topic_cell
            else:
                rejections.append(
                    f"line {line_no}: Name of the Exam {topic_cell!r} has no letters "
                    f"-- doesn't look like a topic, ignored (not forward-filled)"
                )
        if date_cell:
            parsed = parse_date(date_cell, min_year=2005, bound_today=True)
            if parsed:
                cur_date_iso = parsed
            else:
                rejections.append(
                    f"line {line_no}: Date {date_cell!r} didn't parse -- ignored "
                    f"(not forward-filled)"
                )
        if max_cell:
            if looks_numeric(max_cell):
                cur_max = max_cell
            else:
                rejections.append(
                    f"line {line_no}: Max. Marks {max_cell!r} isn't numeric -- "
                    f"ignored (not forward-filled)"
                )

        if not name:
            continue  # fully blank filler row

        if not cur_topic_raw or not cur_date_iso:
            skips.append(
                f"line {line_no} ({name!r}): no valid exam topic/date established "
                f"yet for this block -> row SKIPPED"
            )
            continue
        if not marks_cell:
            continue  # name present but no mark entered -- nothing to add
        if not looks_numeric(marks_cell):
            fixed = normalize_marks_value(marks_cell)
            if fixed:
                corrections.append(
                    f"line {line_no} ({name!r}): Marks Obtained normalized from "
                    f"{marks_cell!r} to {fixed!r} (colon-for-decimal typo corrected)"
                )
                marks_cell = fixed
            else:
                skips.append(
                    f"line {line_no} ({name!r}): Marks Obtained {marks_cell!r} isn't "
                    f"numeric -> row SKIPPED"
                )
                continue

        date_ddmmyyyy = datetime.strptime(cur_date_iso, "%Y-%m-%d").strftime("%d-%m-%Y")
        written.append(
            {
                "student_id": row_student_id or "",
                "name": name,
                "date": date_ddmmyyyy,
                "topic": cur_topic_raw,
                "marks": marks_cell,
                "max_marks": cur_max or "",
                "src_line": line_no,
            }
        )

    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "Sl.No",
                "Student ID",
                "Name of Student",
                "Date of Exam",
                "Name of Exam",
                "Marks Obtained",
                "Max Marks",
            ]
        )
        for i, r in enumerate(written, start=1):
            writer.writerow(
                [
                    i,
                    r["student_id"],
                    r["name"],
                    r["date"],
                    r["topic"],
                    r["marks"],
                    r["max_marks"],
                ]
            )

    with report_path.open("w") as f:
        f.write(f"Source file: {src}\n")
        f.write(f"Rows written to {out.name}: {len(written)}\n")
        f.write(
            f"Block-header values rejected (not forward-filled): {len(rejections)}\n"
        )
        f.write(
            f"Marks values auto-corrected (colon-for-decimal typo): {len(corrections)}\n"
        )
        f.write(f"Named rows skipped entirely: {len(skips)}\n")
        f.write(
            "\n=== BLOCK-HEADER REJECTIONS (value ignored, previous value kept) ===\n"
        )
        f.write("\n".join(rejections) + "\n")
        f.write("\n=== MARKS AUTO-CORRECTIONS (row WAS written, value adjusted) ===\n")
        f.write("\n".join(corrections) + "\n")
        f.write("\n=== ROWS SKIPPED (nothing written for this row) ===\n")
        f.write("\n".join(skips) + "\n")

    return written, rejections, corrections, skips


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--csv", required=True, type=Path, help="raw internal_marks.csv")
    ap.add_argument("--out", type=Path, default=Path("internal_marks_organized.csv"))
    ap.add_argument(
        "--report", type=Path, default=Path("internal_marks_organize_report.txt")
    )
    args = ap.parse_args()

    if not args.csv.exists():
        sys.exit(f"--csv {args.csv} does not exist.")

    written, rejections, corrections, skips = organize(args.csv, args.out, args.report)

    print(f"Rows written to {args.out}: {len(written)}")
    print(f"Block-header values rejected (not forward-filled): {len(rejections)}")
    print(f"Marks values auto-corrected (colon-for-decimal typo): {len(corrections)}")
    print(f"Named rows skipped entirely: {len(skips)}")
    print(f"Full details in {args.report}")


if __name__ == "__main__":
    main()
