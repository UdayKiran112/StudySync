# data_loader

Split, independently-runnable versions of the two original combined loader
scripts (`data_loader.py` and `load_activity.py`). Each script now loads one
kind of data and writes its own separate report -- no shared logging.

## Files

| File                      | Loads                                                                   | Source CSV                                       | Report (default)                  |
| ------------------------- | ----------------------------------------------------------------------- | ------------------------------------------------ | --------------------------------- |
| `common.py`               | shared helpers only (not run directly)                                  | --                                               | --                                |
| `load_members.py`         | member/student details -> `students`                                    | `members_details.csv`                            | `members_load_report.txt`         |
| `load_attendance.py`      | check-in/check-out -> `attendance`                                      | `students_activity.csv`                          | `attendance_load_report.txt`      |
| `load_offline_library.py` | physical book usage -> `books`, `offline_library_usage`                 | `students_activity.csv`                          | `offline_library_load_report.txt` |
| `load_digital_library.py` | online/subscription usage -> `digital_library_usage`, `subscriptions`   | `students_activity.csv`                          | `digital_library_load_report.txt` |
| `load_coaching.py`        | coaching-class enrollment -> `coaching_classes`, `coaching_enrollments` | `students_activity.csv`                          | `coaching_load_report.txt`        |
| `load_exam_marks.py`      | exams + exam marks (Offline Exam column, plus optional marks register)  | `students_activity.csv` (+ `internal_marks.csv`) | `exam_marks_load_report.txt`      |

`load_coaching.py` wasn't explicitly asked for by name, but it's split out
for the same reason as the other three: it's a distinct activity type in
the same source CSV, so it gets its own report instead of being folded into
attendance/library/exams.

`common.py` holds the pieces used by more than one script: date/time
parsing, the fuzzy-name `Canonicalizer` (used for subscription names, book
titles, and exam topics), and the one-time schema migration that makes
`exams.max_marks` / `quizzes.max_marks` / `exam_marks.marks_obtained` /
`quiz_scores.score` nullable (`relax_marks_schema`, used only by
`load_exam_marks.py`).

Behavior, column layout, parsing rules, and auto-correction/skip logic are
otherwise unchanged from the original two scripts -- this was a structural
split, not a rewrite. A full test run of all six scripts against a fresh
database reproduced the original combined database's row counts in every
table exactly.

## Load order

Run in this order against the same `--db` (each after the first depends on
`students` already being populated):

```bash
cd data_loader

# 1. Members / students (creates the db from schema.sql if it doesn't exist yet)
python3 load_members.py --csv members_details.csv --db library.db --schema schema.sql

# 2-5. Any order relative to each other -- each touches a different set of tables
python3 load_attendance.py --csv students_activity.csv --db library.db
python3 load_offline_library.py --csv students_activity.csv --db library.db
python3 load_digital_library.py --csv students_activity.csv --db library.db
python3 load_coaching.py --csv students_activity.csv --db library.db

# 6. Exams + exam marks (marks-csv is optional)
python3 load_exam_marks.py --csv students_activity.csv --db library.db --marks-csv internal_marks.csv
```

Each script is independent and only needs `students` to already exist --
`load_attendance.py`, `load_offline_library.py`, `load_digital_library.py`,
and `load_coaching.py` can be run in any order relative to one another (or
skipped individually) since none of them touch tables the others write to.

**None of the six scripts de-duplicate rows across runs** (same as the
originals) -- re-running against the same `--db` will insert everything
again. Run each once per fresh load. `load_exam_marks.py --marks-csv` is
the one exception: it only fills/updates `exam_marks` rows, never
duplicates them (`UNIQUE(student_id, exam_id)`), so it's safe to re-run.

## Why split this way

- **Member details** never touch attendance/exam data, so a members-load
  bug can't corrupt activity data and vice versa.
- **Attendance, offline library, digital library, and coaching** all read
  the same `students_activity.csv`, but each is functionally independent
  (different tables, different auto-correction rules, different canonical-
  name clusters) -- splitting them means each can be re-run, debugged, or
  extended without touching the others, and each gets its own focused
  report instead of one combined 15,000-line file.
- **Exam marks** is kept separate from the other activity types because it
  also reads a second source (the marks register) and owns the one-time
  nullable-schema migration -- neither of which the other four scripts
  need.
