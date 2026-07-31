# data_loader

Two-stage pipeline: dedicated cleaning scripts turn each messy raw export
into a validated CSV (with a separate error/correction log per section),
then dedicated loader scripts read those cleaned CSVs into the SQLite
database -- each loader trusts the cleaning step's validation instead of
re-parsing raw, inconsistent data itself.

## Stage 1: Cleaning scripts

| Script                       | Input                   | Output                                                                                                                                    |
| ---------------------------- | ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `clean_student_data.py`      | `students_activity.csv` | `cleaned_output/{digital_library,offline_library,digital_class,attendance}.csv` + `error_log_*.log` / `corrections_log_*.log` per section |
| `organize_internal_marks.py` | `internal_marks.csv`    | `internal_marks_organized.csv` + `internal_marks_organize_report.txt`                                                                     |

`clean_student_data.py` splits the one daily activity-log export into its
four activity types, fixing what it safely can (date/time typos, comma-
separated multi-value cells, misspelled Gender/Online Subscription values)
and logging everything else for manual review -- see the script's own
docstrings for the full list per section.

`organize_internal_marks.py` resolves the exam-marks register's Excel
merged-cell block structure: Name of the Exam / Date / Max. Marks are only
filled in on each block's first row, so every subsequent row needs them
forward-filled (validated, not blind -- a stray mis-keyed value is rejected
rather than corrupting the rest of the block). It also recovers a
colon-for-decimal typo in Marks Obtained (`18:50` -> `18.50`) and captures
the odd blocks that give a numeric Student ID directly instead of relying
on name-matching.

`members_details.csv` has no cleaning step -- `load_members.py` reads it
directly (see Stage 2).

## Stage 2: Loader scripts

| Script                    | Loads                                                                                                      | Reads (cleaned)                                                                                                  | Report (default)                  |
| ------------------------- | ---------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- | --------------------------------- |
| `common.py`               | shared helpers only (not run directly)                                                                     | --                                                                                                               | --                                |
| `load_members.py`         | member/student details -> `students`                                                                       | `members_details.csv` (raw; no cleaning step)                                                                    | `members_load_report.txt`         |
| `load_attendance.py`      | check-in/check-out -> `attendance`                                                                         | `cleaned_output/attendance.csv`                                                                                  | `attendance_load_report.txt`      |
| `load_offline_library.py` | physical book usage -> `books`, `offline_library_usage`                                                    | `cleaned_output/offline_library.csv`                                                                             | `offline_library_load_report.txt` |
| `load_digital_library.py` | online/subscription usage -> `digital_library_usage`, `subscriptions`                                      | `cleaned_output/digital_library.csv`                                                                             | `digital_library_load_report.txt` |
| `load_coaching.py`        | coaching-class enrollment -> `coaching_classes`, `coaching_enrollments`                                    | `cleaned_output/digital_class.csv`                                                                               | `coaching_load_report.txt`        |
| `load_exam_marks.py`      | exams + exam marks (Offline Exam column from the raw activity CSV, plus optional organized marks register) | `students_activity.csv` (raw; Offline Exam column not yet split out by Stage 1) + `internal_marks_organized.csv` | `exam_marks_load_report.txt`      |

`load_coaching.py` wasn't explicitly asked for by name originally, but
it's split out for the same reason as the others: it's a distinct activity
type in the same source data, so it gets its own report instead of being
folded into attendance/library/exams.

`common.py` holds the pieces used by more than one loader: date/time
parsing, the fuzzy-name `Canonicalizer` (used for subscription names, book
titles, and exam topics), and the one-time schema migration that makes
`exams.max_marks` / `quizzes.max_marks` / `exam_marks.marks_obtained` /
`quiz_scores.score` nullable (`relax_marks_schema`, used only by
`load_exam_marks.py`).

`load_exam_marks.py`'s Offline Exam column still comes from the raw
`students_activity.csv`, not a `cleaned_output/*.csv` -- Stage 1 doesn't
currently produce a dedicated exam/quiz section (only digital library,
offline library, digital class, and attendance), so this is the one loader
still doing its own date parsing/validation directly against the raw
export for that half of its input.

## Run order

```bash
cd data_loader

# --- Stage 1: clean ---
python3 clean_student_data.py students_activity.csv cleaned_output/
python3 organize_internal_marks.py --csv internal_marks.csv --out internal_marks_organized.csv

# --- Stage 2: load, in this order against the same --db ---
# (each after the first depends on `students` already being populated)

# 1. Members / students (creates the db from schema.sql if it doesn't exist yet)
python3 load_members.py --csv members_details.csv --db library.db --schema schema.sql

# 2-5. Any order relative to each other -- each touches a different set of tables
python3 load_attendance.py --csv cleaned_output/attendance.csv --db library.db
python3 load_offline_library.py --csv cleaned_output/offline_library.csv --db library.db
python3 load_digital_library.py --csv cleaned_output/digital_library.csv --db library.db
python3 load_coaching.py --csv cleaned_output/digital_class.csv --db library.db

# 6. Exams + exam marks (--marks-csv is optional, and must be the ORGANIZED file)
python3 load_exam_marks.py --csv students_activity.csv --db library.db \
    --marks-csv internal_marks_organized.csv
```

Each Stage 2 script is independent and only needs `students` to already
exist -- `load_attendance.py`, `load_offline_library.py`,
`load_digital_library.py`, and `load_coaching.py` can be run in any order
relative to one another (or skipped individually) since none of them touch
tables the others write to.

**None of the loader scripts de-duplicate rows across runs** -- re-running
against the same `--db` will insert everything again. Run each once per
fresh load. `load_exam_marks.py --marks-csv` is the one exception: it only
fills/updates `exam_marks` rows, never duplicates them
(`UNIQUE(student_id, exam_id)`), so it's safe to re-run.

## Why split this way

- **Cleaning is separated from loading** so every validation/auto-
  correction rule lives in exactly one place (readable independent of any
  database) and every loader can assume its input CSV is already
  well-formed, instead of duplicating messy-data handling in six different
  scripts.
- **Member details** never touch attendance/exam data, so a members-load
  bug can't corrupt activity data and vice versa.
- **Attendance, offline library, digital library, and coaching** all read
  from the same cleaned activity export, but each is functionally
  independent (different tables, different auto-correction rules,
  different canonical-name clusters) -- splitting them means each can be
  re-run, debugged, or extended without touching the others, and each gets
  its own focused report instead of one combined file.
- **Exam marks** is kept separate from the other activity types because it
  also reads a second, separately-cleaned source (the organized marks
  register) and owns the one-time nullable-schema migration -- neither of
  which the other four loaders need.
