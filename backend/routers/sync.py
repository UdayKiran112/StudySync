"""
routers/sync.py
------------------
Pushes the SQLite database to Google Sheets, one tab per module, so a copy
of every module lives in the cloud as a safety net:
    1. Attendance
    2. Digital Library (digital library usage)
    3. Offline Library (offline library / book usage)
    4. Exams (with per-student marks)
    5. Quizzes (with per-student scores)
    6. Students
    7. Coaching (class rosters, library + external participants)
    8. Other Activities (speaker/faculty sessions and attendees)

Every activity sheet carries the Student ID AND Student Name for each row
(lookup joined from the students table); non-activity sheets naturally
include them too, so no sheet is ever a pile of opaque foreign keys.

Each sheet is a FULL REWRITE on every sync (clear + rewrite all current
rows), not an incremental append -- see sheets_client.py for why.

Each sheet is attempted independently -- one sheet failing (e.g. a
transient API hiccup) doesn't block the others. The overall result is
"Success" if all wrote cleanly, "Partial" if some did, "Failed" if none
did. Every attempt is recorded in sync_log regardless of outcome, so sync
history is never lost even on failure.
"""

import sqlite3
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends

from database import get_db_dependency
from models.sync import SheetSyncResult, SyncResponse, SyncLogEntry
from security import require_api_key
from sheets_client import write_sheet, SheetsConfigError

router = APIRouter(
    prefix="/api/sync",
    tags=["Sync"],
    dependencies=[Depends(require_api_key)],
)


def _minutes_between(in_time: Optional[str], out_time: Optional[str]):
    """Whole minutes between two HH:MM times, or '' when not computable."""
    if not in_time or not out_time:
        return ""
    try:
        ih, im = (int(p) for p in in_time.split(":"))
        oh, om = (int(p) for p in out_time.split(":"))
        return (oh * 60 + om) - (ih * 60 + im)
    except (TypeError, ValueError):
        return ""


def _percentage(obtained: Optional[float], max_marks: Optional[float]):
    """Percentage rounded to 2dp, or '' when either value is missing/zero."""
    if obtained is None or not max_marks:
        return ""
    return round(obtained / max_marks * 100, 2)


def _sync_attendance(db: sqlite3.Connection) -> SheetSyncResult:
    rows = db.execute("""
        SELECT attendance.date, students.student_id, students.name,
               attendance.session, attendance.check_in, attendance.check_out,
               attendance.duration_minutes
        FROM attendance
        JOIN students ON students.student_id = attendance.student_id
        ORDER BY attendance.date, students.student_id, attendance.session
        """).fetchall()
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


def _sync_digital_library(db: sqlite3.Connection) -> SheetSyncResult:
    rows = db.execute("""
        SELECT digital_library_usage.date, students.student_id, students.name,
               digital_library_usage.in_time, digital_library_usage.out_time,
               digital_library_usage.account_type,
               digital_library_usage.subscription_id,
               digital_library_usage.platform_name, digital_library_usage.purpose,
               digital_library_usage.notes
        FROM digital_library_usage
        JOIN students ON students.student_id = digital_library_usage.student_id
        ORDER BY digital_library_usage.date, students.student_id
        """).fetchall()
    headers = [
        "Date",
        "Student ID",
        "Student Name",
        "In Time",
        "Out Time",
        "Duration (min)",
        "Account Type",
        "Subscription ID",
        "Platform",
        "Purpose",
        "Notes",
    ]
    data = [
        [
            r["date"],
            r["student_id"],
            r["name"],
            r["in_time"] or "",
            r["out_time"] or "",
            _minutes_between(r["in_time"], r["out_time"]),
            r["account_type"] or "",
            r["subscription_id"] or "",
            r["platform_name"] or "",
            r["purpose"] or "",
            r["notes"] or "",
        ]
        for r in rows
    ]
    synced = write_sheet("Digital Library", headers, data)
    return SheetSyncResult(
        sheet_name="Digital Library", status="Success", rows_synced=synced
    )


def _sync_offline_library(db: sqlite3.Connection) -> SheetSyncResult:
    rows = db.execute("""
        SELECT offline_library_usage.date, students.student_id, students.name,
               offline_library_usage.book_id, books.title AS book_title
        FROM offline_library_usage
        JOIN students ON students.student_id = offline_library_usage.student_id
        LEFT JOIN books ON books.book_id = offline_library_usage.book_id
        ORDER BY offline_library_usage.date, students.student_id
        """).fetchall()
    headers = [
        "Date",
        "Student ID",
        "Student Name",
        "Book ID",
        "Book Title",
    ]
    data = [
        [
            r["date"],
            r["student_id"],
            r["name"],
            r["book_id"] or "",
            r["book_title"] or "Self-study",
        ]
        for r in rows
    ]
    synced = write_sheet("Offline Library", headers, data)
    return SheetSyncResult(
        sheet_name="Offline Library", status="Success", rows_synced=synced
    )


def _sync_exams(db: sqlite3.Connection) -> SheetSyncResult:
    rows = db.execute("""
        SELECT exams.exam_date, students.student_id, students.name,
               exams.exam_name, exams.subject, exam_marks.marks_obtained,
               exams.max_marks, exam_marks.remarks
        FROM exam_marks
        JOIN exams ON exams.exam_id = exam_marks.exam_id
        JOIN students ON students.student_id = exam_marks.student_id
        ORDER BY exams.exam_date, students.student_id
        """).fetchall()
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
            r["max_marks"] if r["max_marks"] is not None else "",
            _percentage(r["marks_obtained"], r["max_marks"]),
            r["remarks"] or "",
        ]
        for r in rows
    ]
    synced = write_sheet("Exams", headers, data)
    return SheetSyncResult(sheet_name="Exams", status="Success", rows_synced=synced)


def _sync_quizzes(db: sqlite3.Connection) -> SheetSyncResult:
    rows = db.execute("""
        SELECT quizzes.quiz_date, students.student_id, students.name,
               quizzes.quiz_name, quizzes.subject, quiz_scores.score,
               quizzes.max_marks, quiz_scores.remarks
        FROM quiz_scores
        JOIN quizzes ON quizzes.quiz_id = quiz_scores.quiz_id
        JOIN students ON students.student_id = quiz_scores.student_id
        ORDER BY quizzes.quiz_date, students.student_id
        """).fetchall()
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
            r["score"] if r["score"] is not None else "",
            r["max_marks"] if r["max_marks"] is not None else "",
            _percentage(r["score"], r["max_marks"]),
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
        "Father Name",
        "Qualification",
        "Goal",
        "Preparing For",
        "Address",
        "Join Date",
        "Photo Path",
        "Status",
        "Renewal Count",
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
            r["father_name"] or "",
            r["qualification"] or "",
            r["goal"] or "",
            r["preparing_for"] or "",
            r["address"] or "",
            r["join_date"],
            r["photo_path"] or "",
            r["status"],
            r["renewal_count"],
            r["created_at"],
            r["updated_at"],
        ]
        for r in rows
    ]
    synced = write_sheet("Students", headers, data)
    return SheetSyncResult(sheet_name="Students", status="Success", rows_synced=synced)


def _sync_coaching(db: sqlite3.Connection) -> SheetSyncResult:
    rows = db.execute("""
        SELECT coaching_classes.class_date, coaching_classes.title,
               coaching_classes.start_time, coaching_classes.end_time,
               coaching_classes.subject, instructors.name AS instructor_name,
               coaching_enrollments.participant_type,
               students.student_id, students.name AS student_name,
               external_participants.name AS external_name,
               coaching_enrollments.enrolled_at
        FROM coaching_enrollments
        JOIN coaching_classes
             ON coaching_classes.class_id = coaching_enrollments.class_id
        LEFT JOIN instructors
             ON instructors.instructor_id = coaching_classes.instructor_id
        LEFT JOIN students
             ON students.student_id = coaching_enrollments.student_id
        LEFT JOIN external_participants
             ON external_participants.external_participant_id
              = coaching_enrollments.external_participant_id
        ORDER BY coaching_classes.class_date,
                 coaching_classes.title,
                 coaching_enrollments.enrollment_id
        """).fetchall()
    headers = [
        "Class Date",
        "Class Title",
        "Start Time",
        "End Time",
        "Subject",
        "Instructor",
        "Participant Type",
        "Student ID",
        "Student Name",
        "Enrolled At",
    ]
    data = [
        [
            r["class_date"] or "",
            r["title"],
            r["start_time"] or "",
            r["end_time"] or "",
            r["subject"] or "",
            r["instructor_name"] or "",
            r["participant_type"],
            r["student_id"] if r["student_id"] is not None else "",
            r["student_name"] or r["external_name"] or "",
            r["enrolled_at"],
        ]
        for r in rows
    ]
    synced = write_sheet("Coaching", headers, data)
    return SheetSyncResult(sheet_name="Coaching", status="Success", rows_synced=synced)


def _sync_other_activities(db: sqlite3.Connection) -> SheetSyncResult:
    rows = db.execute("""
        SELECT other_activities.session_name, other_activities.speaker_name,
               other_activities.session_date, other_activities.session_type,
               other_activities.notes, other_activities_attendance.participant_type,
               students.student_id, students.name AS student_name,
               external_participants.name AS external_name,
               other_activities_attendance.attended_at
        FROM other_activities_attendance
        JOIN other_activities
             ON other_activities.activity_id
              = other_activities_attendance.activity_id
        LEFT JOIN students
             ON students.student_id = other_activities_attendance.student_id
        LEFT JOIN external_participants
             ON external_participants.external_participant_id
              = other_activities_attendance.external_participant_id
        ORDER BY other_activities.session_date,
                 other_activities.session_name,
                 other_activities_attendance.attendance_id
        """).fetchall()
    headers = [
        "Session Name",
        "Speaker Name",
        "Session Date",
        "Session Type",
        "Notes",
        "Participant Type",
        "Student ID",
        "Student Name",
        "Attended At",
    ]
    data = [
        [
            r["session_name"],
            r["speaker_name"],
            r["session_date"] or "",
            r["session_type"],
            r["notes"] or "",
            r["participant_type"],
            r["student_id"] if r["student_id"] is not None else "",
            r["student_name"] or r["external_name"] or "",
            r["attended_at"],
        ]
        for r in rows
    ]
    synced = write_sheet("Other Activities", headers, data)
    return SheetSyncResult(
        sheet_name="Other Activities", status="Success", rows_synced=synced
    )


SYNC_TASKS = [
    ("Attendance", _sync_attendance),
    ("Digital Library", _sync_digital_library),
    ("Offline Library", _sync_offline_library),
    ("Exams", _sync_exams),
    ("Quizzes", _sync_quizzes),
    ("Students", _sync_students),
    ("Coaching", _sync_coaching),
    ("Other Activities", _sync_other_activities),
]


@router.post("", response_model=SyncResponse)
def sync_to_sheets(db: sqlite3.Connection = Depends(get_db_dependency)):
    """
    Push the full current database to Google Sheets, one tab per module.
    Each sheet is attempted independently so one failure doesn't block
    the rest.
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
