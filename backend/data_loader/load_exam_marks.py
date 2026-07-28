#!/usr/bin/env python3
"""
Load exam data into the library SQLite database from two sources:

  1. The daily activity-log CSV's "Offline Exam" column (one row per
     student/date who sat an exam that day -- marks_obtained left NULL,
     since the daily activity CSV never records a numeric score, only a
     topic).
  2. An optional separate exam-marks register (--marks-csv, e.g.
     internal_marks.csv) -- one row per (student name, exam) -- which fills
     in marks_obtained (and exams.max_marks) for exams that already exist
     (creating them if they don't).

Usage:
    python3 load_exam_marks.py --csv students_activity.csv --db library.db \\
        [--marks-csv internal_marks.csv]

Requires that library.db already exists and its `students` table is already
populated (e.g. via load_members.py). Attendance/library/coaching data is
NOT touched by this script -- see load_student_activity.py, which reads the
same CSV for those columns and keeps its own separate report.

SCHEMA CHANGE THIS SCRIPT MAKES
--------------------------------
exams.max_marks and exam_marks.marks_obtained are NOT NULL in schema.sql,
but neither CSV source here always supplies those numbers. The first time
this script runs against a given database, it relaxes those (and the
matching quizzes/quiz_scores columns, since they're migrated together) to
nullable -- see common.relax_marks_schema. It's a no-op if a database has
already been migrated.

TOPIC MATCHING (Canonicalizer)
--------------------------------
Exam topic strings are run through a canonicalizer that merges pure
spelling/case/spacing variants and close typos of the same real exam so the
exams table doesn't grow a new row per typo -- see common.Canonicalizer.
An explicit alias table additionally handles abbreviations that
edit-distance alone can't bridge (e.g. 'Ari & Rea' -> 'Arithmetic &
Reasoning'). Deliberately NOT exhaustive: ambiguous short forms that could
mean more than one real subject (e.g. 'G S' could be General Science or
General Studies) are left OUT on purpose -- they become their own distinct
topic rather than being guessed into the wrong one.

--marks-csv MATCHING
----------------------
There's no student ID in the marks register, only a name, so matching is by
exact normalized name (case/whitespace/'.'-vs-space-insensitive) against
students.name. Ambiguous (2+ students share that name) or unmatched names
are skipped and logged rather than guessed -- misattributing someone's
marks is a worse error than leaving them unfilled.

Name of the Exam / Date / Max. Marks are only filled on the first row of
each block in the source register (an Excel merged-cell export) --
forward-filled here, but only from a row that actually looks like a topic
name / date / number; a stray mis-keyed value (e.g. a student ID typed into
the Exam column, or a time typed into Marks Obtained) is rejected rather
than forward-filled or loaded, and logged. Matching then goes through the
same topic canonicalizer as the Offline Exam column above, so a mark row
lands on the SAME exams row that Offline Exam already created, by
(canonical topic, date). marks_obtained and exams.max_marks are filled in
(or updated, with the change logged) rather than duplicated.

LOGGING
--------
This script writes only to its own report (default
exam_marks_load_report.txt) -- member and attendance/library/coaching
loading are handled by the other two scripts in this folder, each with
their own report.

Re-running this script against the same --db will insert exam rows again
where no matching (topic, date) key exists yet, so run it once per fresh
load. --marks-csv is safe to re-run: it only fills/updates rows, never
duplicates them (UNIQUE(student_id, exam_id)).
"""

import argparse
import csv
import re
import sqlite3
import sys
from pathlib import Path

from common import Canonicalizer, collapse_ws, normalize_key, parse_date, relax_marks_schema

COL_DATE = 1
COL_ID = 2
COL_OFFLINE_EXAM = 14

# Exam-topic abbreviations that edit-distance/anagram matching can't bridge
# (e.g. 'Ari & Rea' vs 'Arithmetic & Reasoning' share almost no characters
# despite meaning the same exam). Deliberately NOT exhaustive: ambiguous
# short forms that could mean more than one real subject in this dataset
# (e.g. 'G S' could be General Science or General Studies) are left OUT on
# purpose -- they become their own distinct topic rather than being
# guessed into the wrong one. Keys are pre-normalized with normalize_key
# after stripping a trailing Exam/Test/Grand Test suffix and any trailing
# "(...)" annotation (see canonicalize_exam_topic).
_EXAM_TOPIC_ALIAS_GROUPS = {
    "Arithmetic & Reasoning": [
        "Ari & Rea",
        "Ari&Rea",
        "Ari  & Rea",
        "Ari &Rea",
        "Ar i& Rea",
        "Ari & Reasoning",
        "ARI & REA",
        "A & R",
        "A&R",
        "A& R",
        "A&R Eam",
    ],
    "RRB NTPC": ["RRB NTPC", "RRBNTPC", "RRB NTPC EXAM"],
    "RRB Group D": [
        "RRB Group D",
        "RRB Group-D",
        "RRB Group - D",
        "RRB GRoup D",
        "RRB Group-D Test",
        "RRB Group-D GT",
        "RRBGroup D GT",
        "RRB GROUP D G Test",
    ],
    "SSC GD": ["SSC GD", "SSC G D", "SSC  GD", "SSCGD", "SSC GD  TEST"],
    "Current Affairs": ["Current Affairs", "C A", "C.A", "Current Afairs", "C.Affairs"],
    "Modern History": ["Modern History", "Modren    history"],
    "General Science": [
        "General Science",
        "G Science",
        "G.Science",
        "G. Science",
        "G.Sci",
    ],
    "General Studies": ["General Studies", "G Studies", "Genaral Studies"],
    "Constable Grand Test": [
        "Constable Grand Test",
        "Constable G T",
        "Constable G  T",
        "Contable Grand Test",
        "Constable",
    ],
}
EXAM_TOPIC_ALIASES = {
    normalize_key(
        re.sub(r"(?i)\b(grand\s*test|exam|test)\b\s*$", "", v).strip(" .-")
    ): canon
    for canon, variants in _EXAM_TOPIC_ALIAS_GROUPS.items()
    for v in variants
}


def strip_exam_suffix(cleaned: str) -> str:
    """Drop a trailing '(11)'/'(EM)'-style annotation and a trailing
    Exam/Test/Grand Test word, for matching purposes only -- doesn't
    change what gets displayed if there's no alias/fuzzy match."""
    s = re.sub(r"\s*[\(\[][^)\]]*[\)\]]\s*$", "", cleaned)
    s = re.sub(r"(?i)\b(grand\s*test|exam|test)\b\s*$", "", s).strip(" .-")
    return s or cleaned


class ExamLoader:
    def __init__(self, conn, report):
        self.conn = conn
        self.report = report
        self.exam_cache = {}  # (topic, date) -> exam_id
        self.counts = {"exam_marks": 0}
        self.autocorrections = []
        self.autocorrection_counts = {
            "exam_topic_merged": 0,
            "exam_marks_filled": 0,
            "exam_marks_updated": 0,
            "exam_max_marks_set": 0,
        }
        self.review_notes = []
        self.exam_topic_canon = Canonicalizer(
            self.log_auto, self.log_review, "exam_topic_merged"
        )
        self._name_index = None  # lazily built by _student_name_index()

    def log(self, msg):
        self.report.append(msg)

    def log_auto(self, category, msg):
        self.autocorrection_counts[category] = (
            self.autocorrection_counts.get(category, 0) + 1
        )
        self.autocorrections.append(msg)

    def log_review(self, msg):
        self.review_notes.append(msg)

    def canonicalize_exam_topic(self, raw, context=""):
        """
        Canonicalize an exam topic string so the same real exam (from
        either the daily activity CSV's Offline Exam column or a separate
        --marks-csv register) lands on the same (topic, date) key.

        Tries, in order: (1) the explicit abbreviation alias table (handles
        'Ari & Rea' -> 'Arithmetic & Reasoning', which edit-distance can't
        bridge), (2) the generic fuzzy/anagram/exact canonicalizer (handles
        plain typos like 'Reasoing' -> 'Reasoning').
        """
        cleaned = collapse_ws(raw)
        if not cleaned:
            return cleaned
        stripped = strip_exam_suffix(cleaned)
        key = normalize_key(stripped) or normalize_key(cleaned)
        if key in EXAM_TOPIC_ALIASES:
            return EXAM_TOPIC_ALIASES[key]
        return self.exam_topic_canon.canonicalize(stripped, context=context)

    @staticmethod
    def _normalize_person_name(s: str) -> str:
        """
        Case/whitespace-insensitive, AND treats '.' as equivalent to a
        space -- the students roster consistently writes names like
        'Siva.B' while the marks register writes 'Siva B'. That's a
        systematic punctuation-vs-space convention difference across the
        whole roster, not a content guess, so it's safe to normalize away
        (unlike fuzzy-matching two different-looking names).
        """
        return re.sub(r"\s+", " ", s.replace(".", " ")).strip().lower()

    def _student_name_index(self):
        """Lazily-built name (normalized) -> [student_id, ...] index, used
        only by --marks-csv, which has no student ID column."""
        if self._name_index is None:
            idx = {}
            for sid, name in self.conn.execute("SELECT student_id, name FROM students"):
                idx.setdefault(self._normalize_person_name(name), []).append(sid)
            self._name_index = idx
        return self._name_index

    def match_student_by_name(self, name_raw, context):
        """
        Exact normalized (case/whitespace/'.'-vs-space-insensitive) name
        match only -- deliberately no fuzzy matching here. A wrong guess
        on a person's identity (attributing marks to the wrong student) is
        a worse error than a wrong guess on an exam-topic label, so
        anything not an unambiguous exact match is skipped and logged
        rather than guessed.
        """
        name = collapse_ws(name_raw)
        if not name:
            return None
        ids = self._student_name_index().get(self._normalize_person_name(name))
        if not ids:
            self.log(
                f"{context}: student name {name!r} not found in students -> row SKIPPED"
            )
            return None
        if len(ids) > 1:
            self.log(
                f"{context}: student name {name!r} matches {len(ids)} different "
                f"students {ids} -- ambiguous, can't tell which one this mark "
                f"belongs to -> row SKIPPED"
            )
            return None
        return ids[0]

    def get_or_create_exam(self, topic, date):
        key = (topic, date)
        if key in self.exam_cache:
            return self.exam_cache[key]
        # Checking the DB (not just the in-memory cache) means this also
        # works if --marks-csv is loaded in a later, separate run against
        # an already-populated database, not just in the same pass as the
        # daily activity CSV.
        row = self.conn.execute(
            "SELECT exam_id FROM exams WHERE exam_name = ? AND exam_date = ?",
            (topic, date),
        ).fetchone()
        if row:
            self.exam_cache[key] = row[0]
            return row[0]
        cur = self.conn.execute(
            "INSERT INTO exams (exam_name, exam_date, subject, max_marks) VALUES (?, ?, ?, NULL)",
            (topic, date, topic),
        )
        self.exam_cache[key] = cur.lastrowid
        return cur.lastrowid

    def apply_exam_mark(self, student_id, exam_id, marks_obtained, max_marks, line_no):
        if max_marks is not None:
            row = self.conn.execute(
                "SELECT max_marks FROM exams WHERE exam_id = ?", (exam_id,)
            ).fetchone()
            current_max = row[0] if row else None
            if current_max is None:
                self.conn.execute(
                    "UPDATE exams SET max_marks = ? WHERE exam_id = ?",
                    (max_marks, exam_id),
                )
                self.log_auto(
                    "exam_max_marks_set",
                    f"line {line_no}: exam_id {exam_id} max_marks set to {max_marks}",
                )
            elif current_max != max_marks:
                self.log(
                    f"line {line_no}: exam_id {exam_id} already has max_marks "
                    f"{current_max}, this row says {max_marks} -- kept {current_max}, "
                    f"please review"
                )

        existing = self.conn.execute(
            "SELECT marks_obtained FROM exam_marks WHERE student_id = ? AND exam_id = ?",
            (student_id, exam_id),
        ).fetchone()
        if existing is None:
            self.conn.execute(
                "INSERT INTO exam_marks (student_id, exam_id, marks_obtained) VALUES (?, ?, ?)",
                (student_id, exam_id, marks_obtained),
            )
            self.counts["exam_marks"] += 1
            self.log_auto(
                "exam_marks_filled",
                f"line {line_no}: student {student_id}, exam_id {exam_id} -> "
                f"new exam_marks row, marks_obtained {marks_obtained}",
            )
        elif existing[0] is None:
            self.conn.execute(
                "UPDATE exam_marks SET marks_obtained = ? WHERE student_id = ? AND exam_id = ?",
                (marks_obtained, student_id, exam_id),
            )
            self.log_auto(
                "exam_marks_updated",
                f"line {line_no}: student {student_id}, exam_id {exam_id} -> "
                f"marks_obtained filled in as {marks_obtained}",
            )
        elif existing[0] != marks_obtained:
            self.log(
                f"line {line_no}: student {student_id}, exam_id {exam_id} already "
                f"has marks_obtained {existing[0]}, this row says {marks_obtained} "
                f"-- kept {existing[0]}, please review"
            )
        # else: identical value already recorded, nothing to do

    def load_exam(self, student_id, date, topic_raw, line_no):
        topic = self.canonicalize_exam_topic(
            topic_raw, context=f"line {line_no} (exam)"
        )
        if not topic:
            return
        exam_id = self.get_or_create_exam(topic, date)
        try:
            self.conn.execute(
                "INSERT INTO exam_marks (student_id, exam_id, marks_obtained) VALUES (?, ?, NULL)",
                (student_id, exam_id),
            )
            self.counts["exam_marks"] += 1
        except sqlite3.IntegrityError:
            pass


def load_marks_csv(path: Path, loader: "ExamLoader"):
    """
    Load a separate exam-marks register (e.g. internal_marks.csv): one row
    per (student name, exam), with Name of the Exam / Date / Max. Marks
    only filled on the first row of each block (Excel merged-cell export).
    See the module docstring's --marks-csv section for the matching rules.
    """
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))

    header_idx = None
    for i, row in enumerate(rows):
        if row and collapse_ws(row[0]).lower() == "sl no":
            header_idx = i
            break
    if header_idx is None:
        print(f"--marks-csv {path}: no 'Sl No' header row found -- nothing loaded.")
        return 0, 0

    total_named_rows = 0
    marks_applied = 0
    cur_topic_raw, cur_date, cur_max = None, None, None

    for line_no, row in enumerate(rows[header_idx + 1 :], start=header_idx + 2):
        if len(row) < 6:
            continue
        name = collapse_ws(row[1])
        topic_cell = collapse_ws(row[2])
        date_cell = collapse_ws(row[3])
        max_cell = collapse_ws(row[4])
        marks_cell = collapse_ws(row[5])

        # Forward-fill each of the three block-header fields, but only from
        # a value that actually looks valid for that field -- a stray
        # mis-keyed value (a student ID typed into the Exam column, a date
        # typed where a number was expected, etc.) is rejected rather than
        # forward-filled, so it doesn't silently corrupt every row in the
        # block that follows it.
        if topic_cell:
            if re.search(r"[A-Za-z]", topic_cell):
                cur_topic_raw = topic_cell
            else:
                loader.log(
                    f"line {line_no}: Name of the Exam {topic_cell!r} has no letters "
                    f"-- doesn't look like a topic, ignored (not forward-filled)"
                )
        if date_cell:
            parsed = parse_date(date_cell, min_year=2005, bound_today=True)
            if parsed:
                cur_date = parsed
            else:
                loader.log(
                    f"line {line_no}: Date {date_cell!r} didn't parse -- ignored "
                    f"(not forward-filled)"
                )
        if max_cell:
            if re.match(r"^\d+(\.\d+)?$", max_cell):
                cur_max = float(max_cell)
            else:
                loader.log(
                    f"line {line_no}: Max. Marks {max_cell!r} isn't numeric -- "
                    f"ignored (not forward-filled)"
                )

        if not name:
            continue  # fully blank filler row
        total_named_rows += 1

        if not cur_topic_raw or not cur_date:
            loader.log(
                f"line {line_no} ({name!r}): no valid exam topic/date established "
                f"yet for this block -> row SKIPPED"
            )
            continue
        if not marks_cell:
            continue  # name present but no mark entered -- nothing to add
        if not re.match(r"^\d+(\.\d+)?$", marks_cell):
            loader.log(
                f"line {line_no} ({name!r}): Marks Obtained {marks_cell!r} isn't "
                f"numeric -> row SKIPPED"
            )
            continue

        student_id = loader.match_student_by_name(name, context=f"line {line_no}")
        if student_id is None:
            continue

        topic = loader.canonicalize_exam_topic(
            cur_topic_raw, context=f"line {line_no} (marks)"
        )
        exam_id = loader.get_or_create_exam(topic, cur_date)
        loader.apply_exam_mark(student_id, exam_id, float(marks_cell), cur_max, line_no)
        marks_applied += 1

    return total_named_rows, marks_applied


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--csv", required=True, type=Path, help="students_activity.csv")
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--report", type=Path, default=Path("exam_marks_load_report.txt"))
    ap.add_argument(
        "--marks-csv",
        type=Path,
        default=None,
        help="Optional separate exam-marks register (e.g. internal_marks.csv) "
        "to match against exams/exam_marks and fill in marks_obtained.",
    )
    args = ap.parse_args()

    if not args.db.exists():
        sys.exit(f"--db {args.db} does not exist. Load members into it first.")

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys = ON;")
    relax_marks_schema(conn)
    conn.commit()

    existing_student_ids = {
        r[0] for r in conn.execute("SELECT student_id FROM students")
    }

    report = []
    loader = ExamLoader(conn, report)

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
                continue  # header row / blank filler row / bad id, silently skipped
            total_rows += 1
            student_id = int(id_raw)
            if student_id not in existing_student_ids:
                skipped_id += 1
                report.append(
                    f"line {line_no}: student_id {student_id} not found in students table -> row SKIPPED"
                )
                continue

            date = parse_date(row[COL_DATE], min_year=2005, bound_today=True) if len(row) > COL_DATE else None
            if date is None:
                skipped_date += 1
                report.append(
                    f"line {line_no} (student {student_id}): unparseable date {row[COL_DATE]!r} -> row SKIPPED"
                )
                continue

            if len(row) > COL_OFFLINE_EXAM:
                loader.load_exam(student_id, date, row[COL_OFFLINE_EXAM], line_no)

    marks_named_rows = marks_applied = None
    if args.marks_csv:
        if not args.marks_csv.exists():
            print(f"--marks-csv {args.marks_csv} does not exist -- skipping.")
        else:
            marks_named_rows, marks_applied = load_marks_csv(args.marks_csv, loader)

    conn.commit()

    totals = {}
    for t in ["exams", "exam_marks"]:
        totals[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    conn.close()

    with args.report.open("w") as f:
        f.write(f"CSV data rows processed (Offline Exam column): {total_rows}\n")
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
        if marks_named_rows is not None:
            f.write(
                f"\n--marks-csv: {marks_named_rows} rows with a student name, "
                f"{marks_applied} marks applied\n"
            )
        f.write("\n=== PER-ROW SKIPS (row or part of it was NOT loaded) ===\n")
        f.write("\n".join(report) + "\n")
        f.write(
            "\n=== PER-ROW AUTO-CORRECTIONS (loaded, but adjusted from the raw CSV) ===\n"
        )
        f.write("\n".join(loader.autocorrections) + "\n")
        f.write(
            "\n=== POSSIBLE DUPLICATES NOT MERGED (similar topic, kept separate -- please review) ===\n"
        )
        f.write("\n".join(loader.review_notes) + "\n")

    print(f"Processed {total_rows} CSV rows (Offline Exam column).")
    print(
        f"Skipped: {skipped_id} (unknown student_id), {skipped_date} (unparseable date)"
    )
    print("Inserted this run:", loader.counts)
    print("Auto-corrected this run:", loader.autocorrection_counts)
    print(f"Possible duplicates flagged for review: {len(loader.review_notes)}")
    if marks_named_rows is not None:
        print(
            f"--marks-csv: {marks_named_rows} rows with a student name, "
            f"{marks_applied} marks applied"
        )
    print(f"Full details in {args.report}")


if __name__ == "__main__":
    main()
