#!/usr/bin/env python3
"""
Load members data from the CSV export into the `students` table of the
library SQLite database defined by schema.sql.

Usage:
    python3 load_students.py --csv members.csv --db library.db [--schema schema.sql]

If --db does not exist yet, it is created and schema.sql is executed against
it first. If it already exists, rows are just inserted into the existing
students table (the schema is NOT re-applied).

The CSV is a messy real-world export (Members_details.xlsx saved as CSV),
so this script:
  - Skips completely blank rows (no name).
  - Collapses stray internal whitespace/padding in text fields.
  - Parses DOB / join-date in whatever mangled DD.MM.YYYY-ish form they were
    typed in, tolerating stray commas, dashes, colons, doubled separators,
    2-digit years, and trailing punctuation.
  - Derives gender from the two "Male" / "Female" marker columns (which also
    contain unrelated stray text in some rows).
  - Ignores CSV columns that have no home in the `students` table
    (fee category, "frequently referred books", the ~45 always-empty
    trailing columns).
  - Uses the CSV's own member-number column as student_id directly, so a
    member's ID in the database matches their original register number.
    Rows skipped for any reason (bad join date, duplicate ID) leave a gap
    in the numbering rather than shifting every later ID down to close it.
    The one row with a blank ID (the very first member) is left for SQLite
    to auto-assign. The one malformed ID (a trailing backtick) has the
    stray character stripped. The one duplicate ID in this export (two
    different members both numbered the same) keeps its first occurrence
    and skips the second, since a student_id can't be reused.
  - join_date is NOT NULL in the schema, so any row whose join date cannot be
    parsed at all is skipped rather than inserted with a fabricated date.
  - Every skip/uncertain parse is written to load_report.txt so nothing is
    silently dropped without a trace.

Re-running this script against the same --db will insert the rows again
(there's no dedup key), so run it once per fresh database.
"""

import argparse
import csv
import re
import sqlite3
import sys
from pathlib import Path

# ---- column indices in the CSV (0-based, after the header row) -----------
COL_MEMBER_NO = 0
COL_NAME = 1
COL_FATHER = 2
COL_ADDRESS = 3
COL_DOB = 4
COL_QUALIFICATION = 5
COL_GOAL = 6
COL_PREPARING_FOR = 7
# COL 8 = "Frequently refer books" -> no column in schema, intentionally skipped
COL_JOIN_DATE = 9
# COL 10/11/12 = General / Whitecard holder / Free (fee category) -> skipped
COL_MALE_MARKER = 13
COL_FEMALE_MARKER = 14
COL_PHONE = 15

FEMALE_SPELLINGS = {"female", "femele", "femla"}


def collapse_ws(s: str) -> str:
    """Trim and collapse internal whitespace runs (handles padded cells)."""
    return re.sub(r"\s+", " ", s or "").strip()


def parse_date(raw: str):
    """
    Parse a DD.MM.YYYY-ish date that may use '.', ',', '-', ':', '/' as
    separators (including doubled/mixed separators and trailing junk).
    Returns 'YYYY-MM-DD' or None if it can't be confidently parsed.
    """
    if not raw:
        return None
    s = raw.strip().rstrip("`$.-")
    digit_groups = re.findall(r"\d+", s)
    if len(digit_groups) < 3:
        return None
    day_s, month_s, year_s = digit_groups[0], digit_groups[1], digit_groups[2]

    # Malformed month field (e.g. "012") - not safely recoverable.
    if len(month_s) > 2:
        return None

    try:
        day, month, year = int(day_s), int(month_s), int(year_s)
    except ValueError:
        return None

    if year < 100:
        # 2-digit year: this dataset only spans births in the 1900s and
        # joins from 2005 onward, so 00-26 -> 2000s, else 1900s.
        year += 2000 if year <= 26 else 1900

    if not (1 <= month <= 12):
        return None
    if not (1 <= day <= 31):
        return None
    if not (1900 <= year <= 2100):
        return None

    try:
        import datetime

        datetime.date(year, month, day)
    except ValueError:
        return None

    return f"{year:04d}-{month:02d}-{day:02d}"


def parse_member_id(raw: str):
    """
    Parse the CSV's own member-number column into an int for use as
    student_id. Strips stray non-digit characters (e.g. a trailing
    backtick). Returns None if the cell is blank -> caller should let
    SQLite auto-assign an ID in that case.
    """
    digits = re.sub(r"\D", "", raw or "")
    return int(digits) if digits else None


def derive_gender(male_marker: str, female_marker: str):
    fm = collapse_ws(female_marker).lower()
    if fm in FEMALE_SPELLINGS:
        return "Female"
    if fm == "male":
        return "Male"
    if collapse_ws(male_marker).lower() == "male":
        return "Male"
    return None


def load_rows(csv_path: Path):
    """Yield (row_dict, warnings) for each usable data row, or record a skip."""
    skipped = []
    warnings_report = []
    good_rows = []
    seen_ids = set()

    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        for line_no, row in enumerate(reader, start=2):  # 2: header was line 1
            if len(row) <= COL_NAME or not row[COL_NAME].strip():
                continue  # blank filler row, not an error

            name = collapse_ws(row[COL_NAME])

            member_id_raw = row[COL_MEMBER_NO] if len(row) > COL_MEMBER_NO else ""
            student_id = parse_member_id(member_id_raw)
            if student_id is not None:
                if student_id in seen_ids:
                    skipped.append(
                        f"line {line_no} ({name}): CSV id {student_id} was already used by an "
                        f"earlier row -> row SKIPPED (duplicate student_id)"
                    )
                    continue
                seen_ids.add(student_id)
            father_name = collapse_ws(row[COL_FATHER]) or None
            address = collapse_ws(row[COL_ADDRESS]) or None
            qualification = collapse_ws(row[COL_QUALIFICATION]) or None
            goal = collapse_ws(row[COL_GOAL]) or None
            preparing_for = collapse_ws(row[COL_PREPARING_FOR]) or None
            phone = (
                collapse_ws(row[COL_PHONE]) or None if len(row) > COL_PHONE else None
            )

            dob_raw = row[COL_DOB] if len(row) > COL_DOB else ""
            dob = parse_date(dob_raw)
            if dob_raw.strip() and dob is None:
                warnings_report.append(
                    f"line {line_no} ({name}): could not parse DOB {dob_raw!r} -> stored as NULL"
                )

            join_raw = row[COL_JOIN_DATE] if len(row) > COL_JOIN_DATE else ""
            join_date = parse_date(join_raw)
            if join_date is None:
                skipped.append(
                    f"line {line_no} ({name}): could not parse join date {join_raw!r} "
                    f"(join_date is NOT NULL) -> row SKIPPED"
                )
                continue

            gender = derive_gender(row[COL_MALE_MARKER], row[COL_FEMALE_MARKER])

            good_rows.append(
                {
                    "student_id": student_id,
                    "name": name,
                    "gender": gender,
                    "date_of_birth": dob,
                    "phone": phone,
                    "email": None,
                    "father_name": father_name,
                    "Qualification": qualification,
                    "Goal": goal,
                    "Preparing_for": preparing_for,
                    "address": address,
                    "join_date": join_date,
                    "photo_path": None,
                    "status": "Active",
                }
            )

    return good_rows, skipped, warnings_report


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--csv", required=True, type=Path)
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--schema", type=Path, default=Path("schema.sql"))
    ap.add_argument("--report", type=Path, default=Path("load_report.txt"))
    args = ap.parse_args()

    db_is_new = not args.db.exists()

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys = ON;")

    if db_is_new:
        if not args.schema.exists():
            sys.exit(
                f"--db {args.db} does not exist and --schema {args.schema} was not found."
            )
        with args.schema.open() as f:
            conn.executescript(f.read())
        print(f"Created new database {args.db} from {args.schema}")
    else:
        print(f"Using existing database {args.db} (schema not re-applied)")

    rows, skipped, warnings_report = load_rows(args.csv)

    conn.executemany(
        """
        INSERT INTO students
            (student_id, name, gender, date_of_birth, phone, email, father_name,
             Qualification, Goal, Preparing_for, address, join_date,
             photo_path, status)
        VALUES
            (:student_id, :name, :gender, :date_of_birth, :phone, :email, :father_name,
             :Qualification, :Goal, :Preparing_for, :address, :join_date,
             :photo_path, :status)
        """,
        rows,
    )
    conn.commit()

    total_students = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
    conn.close()

    with args.report.open("w") as f:
        f.write(f"Rows inserted: {len(rows)}\n")
        f.write(
            f"Rows skipped (unparseable join_date or duplicate student_id): {len(skipped)}\n"
        )
        f.write(f"DOB parse warnings (stored as NULL): {len(warnings_report)}\n")
        f.write(f"Total rows now in students table: {total_students}\n\n")
        if skipped:
            f.write("=== SKIPPED ROWS ===\n")
            f.write("\n".join(skipped) + "\n\n")
        if warnings_report:
            f.write("=== DOB WARNINGS ===\n")
            f.write("\n".join(warnings_report) + "\n")

    print(f"Inserted {len(rows)} students into {args.db}")
    print(f"Skipped {len(skipped)} rows (see {args.report})")
    print(
        f"{len(warnings_report)} rows had an unparseable DOB (stored as NULL, see {args.report})"
    )
    print(f"Total rows in students table now: {total_students}")


if __name__ == "__main__":
    main()
