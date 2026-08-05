#!/usr/bin/env python3
"""
Flag offline-exam sittings that have no score, for manual review.

The daily activity CSV's "Offline Exam" column (extracted by
clean_student_data.py into marks/offline_exam.csv) records that a student
sat an exam (topic + date) but never a score. exam_marks.marks_obtained is
NOT NULL in the schema, so those sittings cannot be stored -- and the only
source of real scores is the separate marks register (load_exam_marks.py,
fed by internal_marks_organized.csv).

This loader runs AFTER load_exam_marks. For each sitting it checks whether
the student already has a scored mark on the same real exam (matched by the
deterministic exam identity key on the same date). If yes, the register
covered it and there is nothing to do. If no, the sitting has no score -- it
is NOT added to the database and is instead written to the shared
manual-review ledger (data_loader/review/review_items.jsonl) for a human to
resolve.

Usage:
    python3 clean_student_data.py students_activity.csv cleaned_output/
    python3 load_exam_marks.py --csv internal_marks_organized.csv --db library.db
    python3 load_offline_exam.py --csv cleaned_output/marks/offline_exam.csv --db library.db

Requires that library.db already exists and its `students`, `exams` and
`exam_marks` tables are populated (e.g. via load_members.py and
load_exam_marks.py) -- every sitting here is linked to an existing student
purely by "Student ID", never by name.

WHAT GETS SKIPPED (and logged to the report)
--------------------------------------------
  - Rows with no parseable numeric Student ID, or a Student ID not present
    in students.
  - Rows with no parseable Date.
  - Rows with a blank Exam Name, or one that is really a bare date (a
    stray column-shift -- see common.canonicalize_exam_topic).
  - Sittings whose student already has a scored mark on the same real exam
    (the register covered them; nothing to flag).

LOGGING
-------
This script writes only to its own report (default
offline_exam_load_report.txt). Every sitting without a score is also
appended to the shared manual-review ledger
(data_loader/review/review_items.jsonl), which run_pipeline.py renders into
the consolidated review report.

Safe to re-run against the same --db: it never writes to exams or
exam_marks -- it only reads them and flags review items.
"""

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

common_dir = Path(__file__).parent.parent
if str(common_dir) not in sys.path:
    sys.path.insert(0, str(common_dir))

from common import (
    Canonicalizer,
    canonicalize_exam_topic,
    collapse_ws,
    exam_identity_key,
    log_review_item,
    module_report_dir,
    parse_date,
)


class OfflineExamReviewer:
    def __init__(self, conn):
        self.conn = conn
        self.counts = {"unscored_sittings": 0}
        self.autocorrections = []
        self.autocorrection_counts = {"exam_topic_merged": 0}
        self.review_notes = []
        self.skips = []
        self.exam_topic_canon = Canonicalizer(
            self.log_auto, self.log_review, "exam_topic_merged"
        )
        self._scored = None  # lazily built by _scored_keys()

    def log_auto(self, category, msg):
        self.autocorrection_counts[category] = (
            self.autocorrection_counts.get(category, 0) + 1
        )
        self.autocorrections.append(msg)

    def log_review(self, msg):
        self.review_notes.append(msg)

    def _scored_keys(self):
        """(student_id, date) -> set of exam_identity_key(exam_name) for
        marks the register actually recorded."""
        if self._scored is None:
            scored = {}
            for sid, date, name in self.conn.execute(
                """SELECT em.student_id, e.exam_date, e.exam_name
                   FROM exam_marks em
                   JOIN exams e ON e.exam_id = em.exam_id
                   WHERE e.exam_date IS NOT NULL"""
            ):
                scored.setdefault((sid, date), set()).add(exam_identity_key(name))
            self._scored = scored
        return self._scored

    def check_sitting(self, student_id, date, topic_raw, line_no):
        topic = self._canonical_topic(topic_raw, line_no)
        if not topic:
            return  # already logged/skipped inside _canonical_topic
        key = exam_identity_key(topic)
        if key in self._scored_keys().get((student_id, date), set()):
            return  # the register already holds a score for this sitting
        self.counts["unscored_sittings"] += 1
        self.skips.append(
            f"line {line_no}: student {student_id} sat {topic!r} on {date} "
            f"but no score exists in the register -> NOT added, manual review"
        )
        log_review_item(
            {
                "table": "exam_marks",
                "row": line_no,
                "student_id": student_id,
                "date": date,
                "problem": "sitting_without_marks",
                "detail": f"offline exam {topic!r} on {date}: sitting has no score",
            }
        )

    def _canonical_topic(self, topic_raw, line_no):
        topic = collapse_ws(topic_raw)
        if not topic:
            self.skips.append(
                f"line {line_no}: blank Offline Exam topic -> row SKIPPED"
            )
            log_review_item(
                {
                    "table": "exam_marks",
                    "row": line_no,
                    "problem": "missing_topic",
                    "detail": "blank Offline Exam topic",
                }
            )
            return ""

        def log_date_reject(_cleaned):
            self.skips.append(
                f"line {line_no}: Offline Exam {topic!r} looks like a date, "
                f"not a topic -> row SKIPPED"
            )
            log_review_item(
                {
                    "table": "exam_marks",
                    "row": line_no,
                    "problem": "date_like_topic",
                    "detail": f"Offline Exam {topic!r}",
                }
            )

        return canonicalize_exam_topic(
            topic,
            self.exam_topic_canon,
            context=f"line {line_no} (offline exam)",
            log_date_reject=log_date_reject,
        )


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--csv", required=True, type=Path, help="cleaned offline_exam.csv")
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument(
        "--report",
        type=Path,
        default=module_report_dir("marks") / "offline_exam_load_report.txt",
    )
    args = ap.parse_args()

    if not args.db.exists():
        sys.exit(f"--db {args.db} does not exist. Load members into it first.")

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys = ON;")

    existing_student_ids = {
        r[0] for r in conn.execute("SELECT student_id FROM students")
    }

    loader = OfflineExamReviewer(conn)
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
                        "table": "exam_marks",
                        "row": line_no,
                        "student_id": id_raw,
                        "date": row.get("Date", ""),
                        "problem": "student_id_not_found",
                        "detail": f"offline exam row {line_no}",
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
                        "table": "exam_marks",
                        "row": line_no,
                        "student_id": id_raw,
                        "date": row.get("Date", ""),
                        "problem": "unparseable_date",
                        "detail": f"offline exam row {line_no}",
                    }
                )
                continue

            loader.check_sitting(
                student_id, date, row.get("Exam Name", ""), line_no
            )

    conn.close()

    with args.report.open("w") as f:
        f.write(f"CSV data rows processed: {total_rows}\n")
        f.write(f"Rows skipped (student_id not found): {skipped_id}\n")
        f.write(f"Rows skipped (unparseable date): {skipped_date}\n\n")
        f.write(
            "Offline-exam sittings with no score in the register "
            "(NOT added -- flagged for manual review): "
            f"{loader.counts['unscored_sittings']}\n"
        )
        f.write(
            "\nAuto-corrections this run (canonical topic merges, informational):\n"
        )
        for k, v in loader.autocorrection_counts.items():
            f.write(f"  {k}: {v}\n")
        f.write(f"\n=== PER-ROW SKIPS (row was NOT loaded) ===\n")
        f.write("\n".join(loader.skips) + "\n")
        f.write(
            "\n=== PER-ROW AUTO-CORRECTIONS (loaded, but adjusted from the cleaned CSV) ===\n"
        )
        f.write("\n".join(loader.autocorrections) + "\n")

    print(f"Processed {total_rows} CSV rows.")
    print(
        f"Skipped: {skipped_id} (unknown student_id), {skipped_date} (unparseable date)"
    )
    print(
        f"Sittings without a score flagged for manual review: "
        f"{loader.counts['unscored_sittings']}"
    )
    print(f"Full details in {args.report}")


if __name__ == "__main__":
    main()
