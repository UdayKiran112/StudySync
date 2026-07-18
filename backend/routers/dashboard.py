"""Aggregated student profile data for the staff dashboard."""

import sqlite3
from statistics import mean
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query

from database import get_db_dependency
from models.dashboard import (
    AssessmentAttempt,
    PerformanceAnalytics,
    StudentDashboardResponse,
)
from security import require_api_key


router = APIRouter(
    prefix="/api/dashboard", tags=["Dashboard"], dependencies=[Depends(require_api_key)]
)


def _student_or_404(db: sqlite3.Connection, student_id: int) -> sqlite3.Row:
    student = db.execute(
        "SELECT * FROM students WHERE student_id = ?", (student_id,)
    ).fetchone()
    if not student:
        raise HTTPException(status_code=404, detail=f"Student {student_id} not found")
    return student


def _assessment_rows(
    rows: List[sqlite3.Row], assessment_type: str, id_field: str, name_field: str
) -> List[dict]:
    return [
        {
            "assessment_id": row[id_field],
            "assessment_name": row[name_field],
            "assessment_type": assessment_type,
            "date": row["assessment_date"],
            "subject": row["subject"],
            "marks_obtained": row["marks_obtained"],
            "max_marks": row["max_marks"],
            "percentage": round((row["marks_obtained"] / row["max_marks"]) * 100, 2),
            "remarks": row["remarks"],
        }
        for row in rows
    ]


@router.get("/students/{student_id}", response_model=StudentDashboardResponse)
def get_student_dashboard(
    student_id: int,
    history_limit: int = Query(100, ge=1, le=500),
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    """Return a student's complete dashboard profile and performance summary."""
    student = _student_or_404(db, student_id)

    attendance = db.execute(
        """
        SELECT * FROM attendance WHERE student_id = ?
        ORDER BY date DESC, check_in DESC LIMIT ?
        """,
        (student_id, history_limit),
    ).fetchall()
    digital_usage = db.execute(
        """
        SELECT * FROM digital_library_usage WHERE student_id = ?
        ORDER BY date DESC, in_time DESC LIMIT ?
        """,
        (student_id, history_limit),
    ).fetchall()
    offline_usage = db.execute(
        """
        SELECT usage.*, books.title AS book_title
        FROM offline_library_usage AS usage
        LEFT JOIN books ON books.book_id = usage.book_id
        WHERE usage.student_id = ?
        ORDER BY usage.date DESC, usage.usage_id DESC LIMIT ?
        """,
        (student_id, history_limit),
    ).fetchall()
    subscriptions = db.execute(
        """
        SELECT subscriptions.*,
               EXISTS(
                   SELECT 1 FROM digital_library_usage
                   WHERE digital_library_usage.student_id = ?
                     AND digital_library_usage.subscription_id = subscriptions.subscription_id
               ) AS used_by_student
        FROM subscriptions
        WHERE subscriptions.status = 'Active'
           OR EXISTS(
               SELECT 1 FROM digital_library_usage
               WHERE digital_library_usage.student_id = ?
                 AND digital_library_usage.subscription_id = subscriptions.subscription_id
           )
        ORDER BY subscriptions.status = 'Active' DESC, subscriptions.name
        """,
        (student_id, student_id),
    ).fetchall()
    exam_rows = db.execute(
        """
        SELECT exams.exam_id, exams.exam_name, exams.exam_date AS assessment_date,
               exams.subject, exams.max_marks, exam_marks.marks_obtained, exam_marks.remarks
        FROM exam_marks JOIN exams ON exams.exam_id = exam_marks.exam_id
        WHERE exam_marks.student_id = ?
        ORDER BY exams.exam_date ASC, exams.exam_id ASC
        """,
        (student_id,),
    ).fetchall()
    quiz_rows = db.execute(
        """
        SELECT quizzes.quiz_id, quizzes.quiz_name, quizzes.quiz_date AS assessment_date,
               quizzes.subject, quizzes.max_marks, quiz_scores.score AS marks_obtained,
               quiz_scores.remarks
        FROM quiz_scores JOIN quizzes ON quizzes.quiz_id = quiz_scores.quiz_id
        WHERE quiz_scores.student_id = ?
        ORDER BY quizzes.quiz_date ASC, quizzes.quiz_id ASC
        """,
        (student_id,),
    ).fetchall()

    exams = _assessment_rows(exam_rows, "Exam", "exam_id", "exam_name")
    quizzes = _assessment_rows(quiz_rows, "Quiz", "quiz_id", "quiz_name")
    score_trend = sorted(
        [*exams, *quizzes],
        key=lambda item: (
            item["date"] or "0001-01-01",
            item["assessment_type"],
            item["assessment_id"],
        ),
    )
    percentages = [item["percentage"] for item in score_trend]
    exam_percentages = [item["percentage"] for item in exams]
    quiz_percentages = [item["percentage"] for item in quizzes]

    if len(percentages) < 2:
        trend, delta = "Insufficient data", None
    else:
        delta = round(percentages[-1] - percentages[0], 2)
        trend = (
            "Improving" if delta > 5 else "Declining" if delta < -5 else "Consistent"
        )

    durations = [
        row["duration_minutes"]
        for row in attendance
        if row["duration_minutes"] is not None
    ]
    analytics = {
        "total_assessments": len(percentages),
        "overall_average_percentage": round(mean(percentages), 2)
        if percentages
        else None,
        "exam_average_percentage": round(mean(exam_percentages), 2)
        if exam_percentages
        else None,
        "quiz_average_percentage": round(mean(quiz_percentages), 2)
        if quiz_percentages
        else None,
        "trend": trend,
        "trend_delta_percentage_points": delta,
        "attendance_sessions": len(attendance),
        "completed_attendance_sessions": sum(
            row["check_out"] is not None for row in attendance
        ),
        "average_attendance_duration_minutes": round(mean(durations), 2)
        if durations
        else None,
        "digital_library_sessions": len(digital_usage),
        "offline_library_sessions": len(offline_usage),
    }

    return {
        "student": dict(student),
        "attendance_history": [dict(row) for row in attendance],
        "digital_library_usage": [dict(row) for row in digital_usage],
        "offline_library_usage": [dict(row) for row in offline_usage],
        "subscriptions": [dict(row) for row in subscriptions],
        "exams_attempted": exams,
        "quizzes_attempted": quizzes,
        "score_trend": score_trend,
        "analytics": analytics,
    }
