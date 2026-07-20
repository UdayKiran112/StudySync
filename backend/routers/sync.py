"""
routers/sync.py
------------------
Pushes the SQLite database to Google Sheets, one tab per dataset:
    1. Attendance
    2. Library Usage   (digital + offline combined, matching how the
                         original spreadsheet tracked both side by side)
    3. Exams
    4. Quizzes
    5. Students

Each sheet is a FULL REWRITE on every sync (clear + rewrite all current
rows), not an incremental append -- see sheets_client.py for why.

Each of the 5 sheets is attempted independently -- one sheet failing
(e.g. a transient API hiccup) doesn't block the other four. The overall
result is "Success" if all five wrote cleanly, "Partial" if some did,
"Failed" if none did. Every attempt is recorded in sync_log regardless
of outcome, so sync history is never lost even on failure.
"""

import sqlite3
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends

from database import get_db_dependency
from models.sync import SheetSyncResult, SyncResponse, SyncLogEntry
from sheets_client import write_sheet, SheetsConfigError

router = APIRouter(prefix="/api/sync", tags=["Sync"])


def _sync_attendance(db: sqlite3.Connection) -> SheetSyncResult:
    rows = db.execute(
        """
        SELECT attendance.date, students.student_id, students.name,
               attendance.session, attendance.check_in, attendance.check_out,
               attendance.duration_minutes
        FROM attendance
        JOIN students ON students.student_id = attendance.student_id
        ORDER BY attendance.date, students.student_id, attendance.session
        """
    ).fetchall()
    headers = [
        "Date",
        "Student ID",
        "Student Name",
        "Session",
        "Check In",
        "Check Out",
        "Duration (min)",
    ]
    data = [
        [
            r["date"],
            r["student_id"],
            r["name"],
            r["session"],
            r["check_in"] or "",
            r["check_out"] or "",
            r["duration_minutes"] if r["duration_minutes"] is not None else "",
        ]
        for r in rows
    ]
    synced = write_sheet("Attendance", headers, data)
    return SheetSyncResult(
        sheet_name="Attendance", status="Success", rows_synced=synced
    )


def _sync_library_usage(db: sqlite3.Connection) -> SheetSyncResult:
    digital_rows = db.execute(
        """
        SELECT digital_library_usage.date, students.student_id, students.name,
               digital_library_usage.in_time, digital_library_usage.out_time,
               digital_library_usage.duration_minutes, digital_library_usage.account_type,
               digital_library_usage.platform_name, digital_library_usage.purpose,
               digital_library_usage.notes
        FROM digital_library_usage
        JOIN students ON students.student_id = digital_library_usage.student_id
        """
    ).fetchall()
    offline_rows = db.execute(
        """
        SELECT offline_library_usage.date, students.student_id, students.name,
               offline_library_usage.book_id, books.title AS book_title
        FROM offline_library_usage
        JOIN students ON students.student_id = offline_library_usage.student_id
        LEFT JOIN books ON books.book_id = offline_library_usage.book_id
        """
    ).fetchall()

    headers = [
        "Date",
        "Student ID",
        "Student Name",
        "Usage Type",
        "In Time",
        "Out Time",
        "Duration (min)",
        "Account Type / Platform",
        "Purpose",
        "Book ID",
        "Book Title",
        "Notes",
    ]
    data = []
    for r in digital_rows:
        data.append(
            [
                r["date"],
                r["student_id"],
                r["name"],
                "Digital",
                r["in_time"] or "",
                r["out_time"] or "",
                r["duration_minutes"] if r["duration_minutes"] is not None else "",
                f"{r['account_type']} - {r['platform_name']}",
                r["purpose"] or "",
                "",
                "",
                r["notes"] or "",
            ]
        )
    for r in offline_rows:
        data.append(
            [
                r["date"],
                r["student_id"],
                r["name"],
                "Offline",
                "",
                "",
                "",
                "",
                "",
                r["book_id"] or "",
                r["book_title"] or "Self-study",
                "",
            ]
        )

    data.sort(key=lambda row: (row[0], row[1]))  # chronological, then student

    synced = write_sheet("Library Usage", headers, data)
    return SheetSyncResult(
        sheet_name="Library Usage", status="Success", rows_synced=synced
    )


def _sync_exams(db: sqlite3.Connection) -> SheetSyncResult:
    rows = db.execute(
        """
        SELECT exams.exam_date, students.student_id, students.name,
               exams.exam_name, exams.subject, exam_marks.marks_obtained,
               exams.max_marks, exam_marks.remarks
        FROM exam_marks
        JOIN exams ON exams.exam_id = exam_marks.exam_id
        JOIN students ON students.student_id = exam_marks.student_id
        ORDER BY exams.exam_date, students.student_id
        """
    ).fetchall()
    headers = [
        "Date",
        "Student ID",
        "Student Name",
        "Exam Name",
        "Subject",
        "Marks Obtained",
        "Max Marks",
        "Percentage",
        "Remarks",
    ]
    data = [
        [
            r["exam_date"] or "",
            r["student_id"],
            r["name"],
            r["exam_name"],
            r["subject"] or "",
            r["marks_obtained"],
            r["max_marks"],
            round(r["marks_obtained"] / r["max_marks"] * 100, 2),
            r["remarks"] or "",
        ]
        for r in rows
    ]
    synced = write_sheet("Exams", headers, data)
    return SheetSyncResult(sheet_name="Exams", status="Success", rows_synced=synced)


def _sync_quizzes(db: sqlite3.Connection) -> SheetSyncResult:
    rows = db.execute(
        """
        SELECT quizzes.quiz_date, students.student_id, students.name,
               quizzes.quiz_name, quizzes.subject, quiz_scores.score,
               quizzes.max_marks, quiz_scores.remarks
        FROM quiz_scores
        JOIN quizzes ON quizzes.quiz_id = quiz_scores.quiz_id
        JOIN students ON students.student_id = quiz_scores.student_id
        ORDER BY quizzes.quiz_date, students.student_id
        """
    ).fetchall()
    headers = [
        "Date",
        "Student ID",
        "Student Name",
        "Quiz Name",
        "Subject",
        "Score",
        "Max Marks",
        "Percentage",
        "Remarks",
    ]
    data = [
        [
            r["quiz_date"] or "",
            r["student_id"],
            r["name"],
            r["quiz_name"],
            r["subject"] or "",
            r["score"],
            r["max_marks"],
            round(r["score"] / r["max_marks"] * 100, 2),
            r["remarks"] or "",
        ]
        for r in rows
    ]
    synced = write_sheet("Quizzes", headers, data)
    return SheetSyncResult(sheet_name="Quizzes", status="Success", rows_synced=synced)


def _sync_students(db: sqlite3.Connection) -> SheetSyncResult:
    rows = db.execute("SELECT * FROM students ORDER BY student_id").fetchall()
    headers = [
        "Student ID",
        "Name",
        "Gender",
        "Date of Birth",
        "Phone",
        "Email",
        "Address",
        "Join Date",
        "Status",
        "Created At",
        "Updated At",
    ]
    data = [
        [
            r["student_id"],
            r["name"],
            r["gender"] or "",
            r["date_of_birth"] or "",
            r["phone"] or "",
            r["email"] or "",
            r["address"] or "",
            r["join_date"],
            r["status"],
            r["created_at"],
            r["updated_at"],
        ]
        for r in rows
    ]
    synced = write_sheet("Students", headers, data)
    return SheetSyncResult(sheet_name="Students", status="Success", rows_synced=synced)


SYNC_TASKS = [
    ("Attendance", _sync_attendance),
    ("Library Usage", _sync_library_usage),
    ("Exams", _sync_exams),
    ("Quizzes", _sync_quizzes),
    ("Students", _sync_students),
]


@router.post("", response_model=SyncResponse)
def sync_to_sheets(db: sqlite3.Connection = Depends(get_db_dependency)):
    """
    Push the full current database to Google Sheets, one tab per
    dataset. Each of the 5 sheets is attempted independently so one
    failure doesn't block the rest.
    """
    results: List[SheetSyncResult] = []

    for name, task in SYNC_TASKS:
        try:
            results.append(task(db))
        except SheetsConfigError as e:
            results.append(
                SheetSyncResult(sheet_name=name, status="Failed", error=str(e))
            )
        except Exception as e:
            results.append(
                SheetSyncResult(
                    sheet_name=name, status="Failed", error=f"{type(e).__name__}: {e}"
                )
            )

    succeeded = sum(1 for r in results if r.status == "Success")
    if succeeded == len(results):
        overall = "Success"
    elif succeeded == 0:
        overall = "Failed"
    else:
        overall = "Partial"

    details = "; ".join(
        f"{r.sheet_name}: {r.status}"
        + (f" ({r.rows_synced} rows)" if r.rows_synced is not None else f" - {r.error}")
        for r in results
    )
    db.execute(
        "INSERT INTO sync_log (status, details) VALUES (?, ?)", (overall, details)
    )

    return SyncResponse(status=overall, synced_at=datetime.now(), sheets=results)


@router.get("/history", response_model=List[SyncLogEntry])
def get_sync_history(
    limit: int = 20, db: sqlite3.Connection = Depends(get_db_dependency)
):
    """Recent sync attempts, most recent first -- lets staff check sync status without re-triggering one."""
    rows = db.execute(
        "SELECT * FROM sync_log ORDER BY synced_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(row) for row in rows]
