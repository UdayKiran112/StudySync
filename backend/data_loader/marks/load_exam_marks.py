#!/usr/bin/env python3
"""
Load exam data into the library SQLite database from either or both of:

  1. The daily activity-log CSV's "Offline Exam" column (one row per
     student/date who sat an exam that day -- marks_obtained left NULL,
     since the daily activity CSV never records a numeric score, only a
     topic). Optional -- see --csv below.
  2. An exam-marks register (--marks-csv) -- one row per (student name,
     exam) -- which fills in marks_obtained (and exams.max_marks) for
     exams that already exist (creating them if they don't). This must be
     the ORGANIZED file produced by organize_internal_marks.py, not the
     raw internal_marks.csv directly -- see that script's docstring for
     why (an Excel merged-cell export needs validated forward-filling
     before it's one resolved fact per row) and run it first:

        python3 organize_internal_marks.py

--csv and --marks-csv are each optional, but at least one is required.
Run with --marks-csv alone if all you have is the organized register and
there's no daily activity CSV to load first -- exams get created directly
from --marks-csv in that case (get_or_create_exam), rather than relying on
the Offline Exam column to have created them already.

Usage:
    python3 load_exam_marks.py --db library.db \\
        --marks-csv internal_marks_organized.csv

    # or, with the daily activity log too:
    python3 load_exam_marks.py --csv students_activity.csv --db library.db \\
        --marks-csv internal_marks_organized.csv

Requires that library.db already exists and its `students` table is already
populated (e.g. via load_members.py). Attendance/library/coaching data is
NOT touched by this script -- see load_student_activity.py, which reads the
same --csv for those columns and keeps its own separate report.

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
General Studies, 'RRB' alone could be NTPC or Group D, 'SSC' alone could be
GD or MTS, 'G K' could be General Awareness or something else) are left OUT
on purpose -- they become their own distinct topic rather than being
guessed into the wrong one. A stray leading/trailing '"' left over from an
unescaped quote in the source spreadsheet is also stripped before matching.
A raw topic that's actually a bare date (a stray column-shift in the
source) is rejected rather than turned into a fake exam.

--marks-csv MATCHING ("maximum compatibility" name search)
-------------------------------------------------------------
A few blocks of the register carry a numeric Student ID
(organize_internal_marks.py captures this in its own "Student ID" column)
-- that's used directly whenever it's present and valid, since it's more
reliable than a name. Whenever the ID is missing, or given but not found in
students, the row falls back to searching the database for the student's
name, tried in increasing order of looseness so a real match isn't missed
over a formatting quirk, but never guessed into an ambiguous one:

  1. Exact match, case/whitespace/'.'-vs-space-insensitive.
  2. Same words in a different order (e.g. 'B Siva' vs 'Siva B') --
     that's a pure word-order difference, not a spelling guess.
  3. Close-spelling fuzzy match against the roster (e.g. a typo'd
     surname) -- only applied when exactly one roster name is a close
     match; logged as an autocorrection either way so it's auditable,
     not silent.

At every tier, 2+ roster students matching the same name is treated as
ambiguous and the row is skipped and logged rather than guessed --
misattributing someone's marks is a worse error than leaving them unfilled.

The organized file has already resolved Name of the Exam / Date / Max.
Marks for every row (organize_internal_marks.py forward-filled them from
each block's header row and validated each candidate value), so this
script just canonicalizes the topic through the same canonicalizer as the
Offline Exam column above, so a mark row lands on the SAME exams row that
Offline Exam already created (or creates one directly if --csv wasn't
run), by (canonical topic, date). marks_obtained and exams.max_marks are
filled in (or updated, with the change logged) rather than duplicated.

LOGGING
--------
This script writes only to its own report (default
exam_marks_load_report.txt) -- member and attendance/library/coaching
loading are handled by the other two scripts in this folder, each with
their own report.

Re-running this script against the same --db will insert exam rows again
where no matching (topic, date) key exists yet, so run --csv once per
fresh load. --marks-csv is safe to re-run: it only fills/updates rows,
never duplicates them (UNIQUE(student_id, exam_id)).
"""

import argparse
import csv
import difflib
import re
import sqlite3
import sys
from pathlib import Path

from common import (
    Canonicalizer,
    collapse_ws,
    normalize_key,
    parse_date,
    relax_marks_schema,
)

COL_DATE = 1
COL_ID = 2
COL_OFFLINE_EXAM = 14

# A raw "Name of Exam" that is actually just a date (a stray column-shift
# surviving from the source spreadsheet -- see organize_internal_marks.py)
# should never become its own fake exam topic.
BARE_DATE_RE = re.compile(r"^\d{1,2}[.\-/ ]\d{1,2}[.\-/ ]\d{2,4}$")

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
    "General Studies": [
        "General Studies",
        "G Studies",
        # NOTE: 'G S' is deliberately NOT included here -- see the
        # ambiguity note above (could be General Science or General
        # Studies). Only the unambiguous misspelling is aliased.
        "Genaral Studies",
    ],
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
            "student_name_reordered": 0,
            "student_name_fuzzy_matched": 0,
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

        Tries, in order: (0) strip a stray leading/trailing '"' left by an
        unescaped quote in the source spreadsheet, and reject a bare date
        (a stray column-shift, not a real topic); (1) the explicit
        abbreviation alias table (handles 'Ari & Rea' -> 'Arithmetic &
        Reasoning', which edit-distance can't bridge); (2) the generic
        fuzzy/anagram/exact canonicalizer (handles plain typos like
        'Reasoing' -> 'Reasoning').
        """
        cleaned = collapse_ws(raw).strip('"').strip()
        if not cleaned:
            return cleaned
        if BARE_DATE_RE.match(cleaned):
            self.log(
                f"{context}: Name of Exam {raw!r} looks like a date, not a "
                f"topic -> row SKIPPED"
            )
            return ""
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

    def _resolve_ids(self, ids, name, context):
        """Shared ambiguity check for whichever tier found candidate(s)."""
        if len(ids) > 1:
            self.log(
                f"{context}: student name {name!r} matches {len(ids)} different "
                f"students {ids} -- ambiguous, can't tell which one this mark "
                f"belongs to -> row SKIPPED"
            )
            return None
        return ids[0]

    def match_student_by_name(self, name_raw, context):
        """
        "Maximum compatibility" name search against the students roster,
        tried in increasing order of looseness so a real match isn't missed
        over a formatting quirk, but never guessed into an ambiguous one:

          1. Exact match, case/whitespace/'.'-vs-space-insensitive.
          2. Same words in a different order (e.g. 'B Siva' vs 'Siva B') --
             a pure word-order difference, not a spelling guess, so it's
             applied silently just like tier 1.
          3. Close-spelling fuzzy match against the roster -- only applied
             when exactly one roster name is a close match. Logged as an
             autocorrection so it's auditable, not silent (unlike tiers 1
             and 2, this one really is a guess, just a well-supported one).

        At every tier, 2+ roster students matching is ambiguous and the row
        is skipped and logged -- misattributing someone's marks is a worse
        error than leaving them unfilled. A name that matches nothing at
        any tier is also skipped and logged.
        """
        name = collapse_ws(name_raw)
        if not name:
            return None
        idx = self._student_name_index()
        norm = self._normalize_person_name(name)

        # Tier 1: exact normalized match.
        ids = idx.get(norm)
        if ids:
            return self._resolve_ids(ids, name, context)

        # Tier 2: same words, different order.
        my_tokens = frozenset(norm.split())
        if my_tokens:
            token_matches = [
                (key, cand_ids)
                for key, cand_ids in idx.items()
                if frozenset(key.split()) == my_tokens
            ]
            if len(token_matches) == 1:
                key, cand_ids = token_matches[0]
                result = self._resolve_ids(cand_ids, name, context)
                if result is not None:
                    self.log_auto(
                        "student_name_reordered",
                        f"{context}: name {name!r} matched roster entry "
                        f"{key!r} by word order only -> used that match",
                    )
                return result
            elif len(token_matches) > 1:
                self.log(
                    f"{context}: student name {name!r} matches "
                    f"{len(token_matches)} different roster entries by word "
                    f"order alone ({[k for k, _ in token_matches]}) -- "
                    f"ambiguous -> row SKIPPED"
                )
                return None

        # Tier 3: close-spelling fuzzy match, only when unambiguous.
        close = difflib.get_close_matches(norm, idx.keys(), n=3, cutoff=0.84)
        if len(close) == 1:
            key = close[0]
            result = self._resolve_ids(idx[key], name, context)
            if result is not None:
                self.log_auto(
                    "student_name_fuzzy_matched",
                    f"{context}: name {name!r} had no exact or word-order "
                    f"match, fuzzy-matched to roster entry {key!r} -> used "
                    f"that match, please spot-check",
                )
            return result
        elif len(close) > 1:
            self.log(
                f"{context}: student name {name!r} has no exact/word-order "
                f"match and {len(close)} close roster candidates {close} -- "
                f"ambiguous -> row SKIPPED"
            )
            return None

        self.log(
            f"{context}: student name {name!r} not found in students "
            f"(tried exact, word-order, and fuzzy matching) -> row SKIPPED"
        )
        return None

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


def load_marks_csv(path: Path, loader: "ExamLoader", existing_student_ids: set):
    """
    Load internal_marks_organized.csv, produced by running
    organize_internal_marks.py against the raw register first. That step
    already resolved the Excel merged-cell block structure (forward-filling
    Name of the Exam / Date / Max. Marks, validating each candidate value,
    and recovering the odd colon-for-decimal marks typo) -- every row here
    is already one fully-resolved (student, exam, marks) fact, so this
    function only has to do the DB-side work: match the student,
    canonicalize the topic, and apply the mark. See the module docstring's
    --marks-csv section for the name-matching rule, and
    organize_internal_marks.py's own docstring for what "Student ID" (a
    few blocks of this register give one directly, more reliable than
    name-matching) means below.
    """
    total_named_rows = 0
    marks_applied = 0

    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for line_no, row in enumerate(reader, start=2):  # +1 for header row
            name = collapse_ws(row.get("Name of Student", ""))
            if not name:
                continue
            total_named_rows += 1

            date = parse_date(
                row.get("Date of Exam", ""), min_year=2005, bound_today=True
            )
            if date is None:
                loader.log(
                    f"line {line_no} ({name!r}): unparseable Date of Exam "
                    f"{row.get('Date of Exam')!r} -> row SKIPPED"
                )
                continue

            topic_raw = collapse_ws(row.get("Name of Exam", ""))
            if not topic_raw:
                loader.log(
                    f"line {line_no} ({name!r}): missing Name of Exam -> row SKIPPED"
                )
                continue

            marks_raw = collapse_ws(row.get("Marks Obtained", ""))
            if not marks_raw:
                continue  # nothing to add for this row
            try:
                marks_obtained = float(marks_raw)
            except ValueError:
                loader.log(
                    f"line {line_no} ({name!r}): Marks Obtained {marks_raw!r} isn't "
                    f"numeric -> row SKIPPED"
                )
                continue

            max_raw = collapse_ws(row.get("Max Marks", ""))
            try:
                max_marks = float(max_raw) if max_raw else None
            except ValueError:
                max_marks = None

            student_id = None
            id_override = collapse_ws(row.get("Student ID", ""))
            if id_override:
                if id_override.isdigit() and int(id_override) in existing_student_ids:
                    student_id = int(id_override)
                else:
                    loader.log(
                        f"line {line_no} ({name!r}): Student ID override {id_override!r} "
                        f"not found in students table -- falling back to name match"
                    )
            if student_id is None:
                student_id = loader.match_student_by_name(
                    name, context=f"line {line_no}"
                )
                if student_id is None:
                    continue

            topic = loader.canonicalize_exam_topic(
                topic_raw, context=f"line {line_no} (marks)"
            )
            if not topic:
                continue  # already logged inside canonicalize_exam_topic
            exam_id = loader.get_or_create_exam(topic, date)
            loader.apply_exam_mark(
                student_id, exam_id, marks_obtained, max_marks, line_no
            )
            marks_applied += 1

    return total_named_rows, marks_applied


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="Optional students_activity.csv (daily activity log's Offline "
        "Exam column). Not required if you only want to load --marks-csv.",
    )
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--report", type=Path, default=Path("exam_marks_load_report.txt"))
    ap.add_argument(
        "--marks-csv",
        type=Path,
        default=None,
        help="Optional ORGANIZED exam-marks register (run organize_internal_marks.py "
        "on the raw internal_marks.csv first) to match against exams/exam_marks "
        "and fill in marks_obtained. Can be used with or without --csv.",
    )
    args = ap.parse_args()

    if not args.csv and not args.marks_csv:
        ap.error("at least one of --csv or --marks-csv is required")

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

    if args.csv:
        if not args.csv.exists():
            sys.exit(f"--csv {args.csv} does not exist.")
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

                date = (
                    parse_date(row[COL_DATE], min_year=2005, bound_today=True)
                    if len(row) > COL_DATE
                    else None
                )
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
            marks_named_rows, marks_applied = load_marks_csv(
                args.marks_csv, loader, existing_student_ids
            )

    conn.commit()

    totals = {}
    for t in ["exams", "exam_marks"]:
        totals[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    conn.close()

    with args.report.open("w") as f:
        if args.csv:
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

    if args.csv:
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
