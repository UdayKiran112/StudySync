#!/usr/bin/env python3
"""
Load quiz sittings (the daily activity CSV's "Quiz" column, extracted by
clean_student_data.py into marks/quiz.csv) into the library SQLite database
as quizzes + quiz_scores rows with score NULL.

The daily activity log records that a student sat a quiz (topic + date) but
never a score. There is currently no separate quiz-marks register, so scores
stay NULL -- the schema allows that (see common.relax_marks_schema).

Usage:
    python3 clean_student_data.py students_activity.csv cleaned_output/
    python3 load_quiz.py --csv cleaned_output/marks/quiz.csv --db library.db

Requires that library.db already exists and its `students` table is
already populated (e.g. via load_members.py) -- every row here is linked
to an existing student purely by "Student ID", never by name.

WHAT GETS SKIPPED (and logged to the report)
---------------------------------------------
  - Rows with no parseable numeric Student ID, or a Student ID not present
    in students.
  - Rows with no parseable Date.
  - Rows with a blank Quiz Name, or one that is really a bare date (a
    stray column-shift -- see common.canonicalize_exam_topic, shared with
    the offline-exam loader so both sides agree on a (topic, date) key).

LOGGING
-------
This script writes only to its own report (default quiz_load_report.txt).
Everything it cannot safely auto-correct is also appended to the shared
manual-review ledger (data_loader/review/review_items.jsonl), which
run_pipeline.py renders into the consolidated review report.

Safe to re-run against the same --db: it only creates/keeps (student, quiz)
rows, never duplicates them (UNIQUE(student_id, quiz_id)).
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
    canonicalize_exam_topic,
    collapse_ws,
    log_review_item,
    module_report_dir,
    parse_date,
    relax_marks_schema,
)


class QuizLoader:
    def __init__(self, conn):
        self.conn = conn
        self.quiz_cache = {}  # (topic, date) -> quiz_id
        self.counts = {"quiz_scores": 0, "quizzes_created": 0}
        self.autocorrections = []
        self.autocorrection_counts = {"quiz_topic_merged": 0}
        self.review_notes = []
        self.skips = []
        self.quiz_topic_canon = Canonicalizer(
            self.log_auto, self.log_review, "quiz_topic_merged"
        )

    def log_auto(self, category, msg):
        self.autocorrection_counts[category] = (
            self.autocorrection_counts.get(category, 0) + 1
        )
        self.autocorrections.append(msg)

    def log_review(self, msg):
        self.review_notes.append(msg)

    def get_or_create_quiz(self, topic, date):
        key = (topic, date)
        if key in self.quiz_cache:
            return self.quiz_cache[key]
        row = self.conn.execute(
            "SELECT quiz_id FROM quizzes WHERE quiz_name = ? AND quiz_date = ?",
            (topic, date),
        ).fetchone()
        if row:
            self.quiz_cache[key] = row[0]
            return row[0]
        cur = self.conn.execute(
            "INSERT INTO quizzes (quiz_name, quiz_date, subject, max_marks) VALUES (?, ?, ?, NULL)",
            (topic, date, topic),
        )
        self.quiz_cache[key] = cur.lastrowid
        self.counts["quizzes_created"] += 1
        return cur.lastrowid

    def load_sitting(self, student_id, date, topic_raw, line_no):
        topic = self._canonical_topic(topic_raw, line_no)
        if not topic:
            return  # already logged/skipped inside _canonical_topic
        quiz_id = self.get_or_create_quiz(topic, date)
        exists = self.conn.execute(
            "SELECT 1 FROM quiz_scores WHERE student_id = ? AND quiz_id = ?",
            (student_id, quiz_id),
        ).fetchone()
        if exists:
            return
        try:
            self.conn.execute(
                "INSERT INTO quiz_scores (student_id, quiz_id, score) VALUES (?, ?, NULL)",
                (student_id, quiz_id),
            )
            self.counts["quiz_scores"] += 1
        except sqlite3.IntegrityError as e:
            self.skips.append(
                f"line {line_no}: quiz_scores insert failed ({e}) -> SKIPPED"
            )
            log_review_item(
                {
                    "table": "quiz_scores",
                    "row": line_no,
                    "student_id": student_id,
                    "date": date,
                    "problem": "insert_failed",
                    "detail": f"topic {topic}, {e}",
                }
            )

    def _canonical_topic(self, topic_raw, line_no):
        topic = collapse_ws(topic_raw)
        if not topic:
            self.skips.append(f"line {line_no}: blank Quiz topic -> row SKIPPED")
            log_review_item(
                {
                    "table": "quiz_scores",
                    "row": line_no,
                    "problem": "missing_topic",
                    "detail": "blank Quiz topic",
                }
            )
            return ""

        def log_date_reject(_cleaned):
            self.skips.append(
                f"line {line_no}: Quiz {topic!r} looks like a date, not a topic "
                f"-> row SKIPPED"
            )
            log_review_item(
                {
                    "table": "quiz_scores",
                    "row": line_no,
                    "problem": "date_like_topic",
                    "detail": f"Quiz {topic!r}",
                }
            )

        return canonicalize_exam_topic(
            topic,
            self.quiz_topic_canon,
            context=f"line {line_no} (quiz)",
            log_date_reject=log_date_reject,
        )


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--csv", required=True, type=Path, help="cleaned quiz.csv")
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument(
        "--report",
        type=Path,
        default=module_report_dir("marks") / "quiz_load_report.txt",
    )
    args = ap.parse_args()

    if not args.db.exists():
        sys.exit(f"--db {args.db} does not exist. Load members into it first.")

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys = ON;")
    relax_marks_schema(conn)

    existing_student_ids = {
        r[0] for r in conn.execute("SELECT student_id FROM students")
    }

    loader = QuizLoader(conn)
    skipped_id = 0
    skipped_date = 0
    total_rows = 0

    with args.csv.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for line_no, row in enumerate(reader, start=2):  # +1 for header row
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
                        "table": "quiz_scores",
                        "row": line_no,
                        "student_id": id_raw,
                        "date": row.get("Date", ""),
                        "problem": "student_id_not_found",
                        "detail": f"quiz row {line_no}",
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
                        "table": "quiz_scores",
                        "row": line_no,
                        "student_id": id_raw,
                        "date": row.get("Date", ""),
                        "problem": "unparseable_date",
                        "detail": f"quiz row {line_no}",
                    }
                )
                continue

            loader.load_sitting(student_id, date, row.get("Quiz Name", ""), line_no)

    conn.commit()

    totals = {}
    for t in ["quizzes", "quiz_scores"]:
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
        f.write("\n=== PER-ROW SKIPS (row was NOT loaded) ===\n")
        f.write("\n".join(loader.skips) + "\n")
        f.write(
            "\n=== PER-ROW AUTO-CORRECTIONS (loaded, but adjusted from the cleaned CSV) ===\n"
        )
        f.write("\n".join(loader.autocorrections) + "\n")
        f.write(
            "\n=== POSSIBLE DUPLICATES NOT MERGED (similar topic, kept separate -- please review) ===\n"
        )
        f.write("\n".join(loader.review_notes) + "\n")

    print(f"Processed {total_rows} CSV rows.")
    print(
        f"Skipped: {skipped_id} (unknown student_id), {skipped_date} (unparseable date)"
    )
    print("Inserted this run:", loader.counts)
    print(f"Full details in {args.report}")


if __name__ == "__main__":
    main()
