"""Aggregated student profile data for the staff dashboard."""

import io
import sqlite3
from datetime import date, timedelta
from statistics import mean
from typing import List

import matplotlib

matplotlib.use("Agg")  # headless -- no display available on the server
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image,
)

from database import get_db_dependency
from models.dashboard import StudentDashboardResponse
from security import require_api_key


router = APIRouter(
    prefix="/api/dashboard", tags=["Dashboard"], dependencies=[Depends(require_api_key)]
)


def _trend_and_delta(
    chronological_values: List[float],
    improve_threshold: float,
    decline_threshold: float,
):
    """
    Compare the first and last value in a chronologically-ordered list to
    classify a trend. Used independently for exams, quizzes, subjects,
    and attendance duration, each with its own list and its own
    threshold (percentage points for marks, minutes for attendance).

    Needs at least 2 data points -- with fewer, there's nothing to trend.
    """
    if len(chronological_values) < 2:
        return "Insufficient data", None

    delta = round(chronological_values[-1] - chronological_values[0], 2)
    if delta > improve_threshold:
        trend = "Improving"
    elif delta < decline_threshold:
        trend = "Declining"
    else:
        trend = "Consistent"
    return trend, delta


def _student_or_404(db: sqlite3.Connection, student_id: int) -> sqlite3.Row:
    student = db.execute(
        "SELECT * FROM students WHERE student_id = ?", (student_id,)
    ).fetchone()
    if not student:
        raise HTTPException(status_code=404, detail=f"Student {student_id} not found")
    return student


def _assessment_rows(
    rows: List[sqlite3.Row],
    assessment_type: str,
    id_field: str,
    name_field: str,
    batch_averages: dict,
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
            "batch_average_percentage": batch_averages.get(row[id_field]),
        }
        for row in rows
    ]


def _batch_averages(
    db: sqlite3.Connection,
    marks_table: str,
    id_col: str,
    score_col: str,
    catalog_table: str,
    catalog_id_col: str,
) -> dict:
    """
    Average percentage across ALL students for each exam_id/quiz_id --
    computed once per request, independent of which student is being
    viewed, then looked up per-assessment when building that student's
    exam/quiz list.
    """
    rows = db.execute(
        f"""
        SELECT {marks_table}.{id_col} AS assessment_id,
               AVG({marks_table}.{score_col} * 100.0 / {catalog_table}.max_marks) AS batch_avg
        FROM {marks_table}
        JOIN {catalog_table} ON {catalog_table}.{catalog_id_col} = {marks_table}.{id_col}
        GROUP BY {marks_table}.{id_col}
        """
    ).fetchall()
    return {row["assessment_id"]: round(row["batch_avg"], 2) for row in rows}


def _build_dashboard_data(db: sqlite3.Connection, student_id: int) -> dict:
    """
    Build a student's complete dashboard profile and performance summary.

    Shared by both the JSON dashboard endpoint and the PDF report endpoint,
    so the two can never disagree -- one function computes the numbers,
    each endpoint just renders them differently.

    No row limit anywhere here, by design -- every list and every derived
    metric (trends, averages, streaks, elimination-based offline time) is
    computed from the student's FULL history, not a recent slice.
    """
    student = _student_or_404(db, student_id)

    attendance = db.execute(
        """
        SELECT * FROM attendance WHERE student_id = ?
        ORDER BY date DESC, check_in DESC
        """,
        (student_id,),
    ).fetchall()
    digital_usage = db.execute(
        """
        SELECT * FROM digital_library_usage WHERE student_id = ?
        ORDER BY date DESC, in_time DESC
        """,
        (student_id,),
    ).fetchall()
    offline_usage = db.execute(
        """
        SELECT usage.*, books.title AS book_title, books.category AS book_category
        FROM offline_library_usage AS usage
        LEFT JOIN books ON books.book_id = usage.book_id
        WHERE usage.student_id = ?
        ORDER BY usage.date DESC, usage.usage_id DESC
        """,
        (student_id,),
    ).fetchall()
    coaching_usage = db.execute(
        """SELECT c.class_date AS date, c.duration_minutes, c.subject, i.name AS instructor_name
        FROM coaching_enrollments e JOIN coaching_classes c ON c.class_id = e.class_id
        LEFT JOIN instructors i ON i.instructor_id = c.instructor_id
        WHERE e.student_id = ? ORDER BY c.class_date DESC""",
        (student_id,),
    ).fetchall()

    exam_batch_avgs = _batch_averages(
        db, "exam_marks", "exam_id", "marks_obtained", "exams", "exam_id"
    )
    quiz_batch_avgs = _batch_averages(
        db, "quiz_scores", "quiz_id", "score", "quizzes", "quiz_id"
    )

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

    exams = _assessment_rows(exam_rows, "Exam", "exam_id", "exam_name", exam_batch_avgs)
    quizzes = _assessment_rows(
        quiz_rows, "Quiz", "quiz_id", "quiz_name", quiz_batch_avgs
    )
    score_trend = sorted(
        [*exams, *quizzes],
        key=lambda item: (
            item["date"] or "0001-01-01",
            item["assessment_type"],
            item["assessment_id"],
        ),
    )

    # exams and quizzes are each already chronological ascending (from the
    # ORDER BY ...date ASC in their SQL queries above).
    percentages = [item["percentage"] for item in score_trend]
    exam_percentages = [item["percentage"] for item in exams]
    quiz_percentages = [item["percentage"] for item in quizzes]

    overall_trend, overall_delta = _trend_and_delta(
        percentages, improve_threshold=5, decline_threshold=-5
    )
    exam_trend, exam_delta = _trend_and_delta(
        exam_percentages, improve_threshold=5, decline_threshold=-5
    )
    quiz_trend, quiz_delta = _trend_and_delta(
        quiz_percentages, improve_threshold=5, decline_threshold=-5
    )

    # ---- Subject-wise performance (exams + quizzes combined per subject) ----
    subject_groups: dict = {}
    for item in score_trend:
        subject = item["subject"]
        if not subject:
            continue  # entries with no subject can't be grouped meaningfully
        subject_groups.setdefault(subject, []).append(item)

    subjects_performance = []
    for subject, items in subject_groups.items():
        subj_percentages = [
            i["percentage"] for i in items
        ]  # already chronological (score_trend was sorted)
        subj_trend, subj_delta = _trend_and_delta(
            subj_percentages, improve_threshold=5, decline_threshold=-5
        )
        subjects_performance.append(
            {
                "subject": subject,
                "total_assessments": len(items),
                "average_percentage": round(mean(subj_percentages), 2),
                "trend": subj_trend,
                "trend_delta_percentage_points": subj_delta,
            }
        )
    subjects_performance.sort(key=lambda s: s["subject"])

    # ---- Attendance: duration trend + regularity (rate/streak/recency) ----
    attendance_chronological = sorted(
        attendance, key=lambda row: (row["date"], row["check_in"] or "")
    )
    attendance_durations_chrono = [
        row["duration_minutes"]
        for row in attendance_chronological
        if row["duration_minutes"] is not None
    ]
    attendance_trend, attendance_delta = _trend_and_delta(
        attendance_durations_chrono, improve_threshold=15, decline_threshold=-15
    )
    all_durations = [
        row["duration_minutes"]
        for row in attendance
        if row["duration_minutes"] is not None
    ]

    all_attendance_dates = sorted(
        {date.fromisoformat(row["date"]) for row in attendance}, reverse=True
    )
    today = date.today()
    if all_attendance_dates:
        last_visit = all_attendance_dates[0]
        days_since_last_visit = (today - last_visit).days
    else:
        last_visit, days_since_last_visit = None, None

    window_start = today - timedelta(days=29)
    days_in_window = {d for d in all_attendance_dates if window_start <= d <= today}
    attendance_rate_30d = round(len(days_in_window) / 30 * 100, 2)

    current_streak = 0
    if days_since_last_visit is not None and days_since_last_visit <= 1:
        date_set = set(all_attendance_dates)
        cursor_date = last_visit
        while cursor_date in date_set:
            current_streak += 1
            cursor_date -= timedelta(days=1)

    # ---- Offline library: self-study count, category breakdown, and
    #      time estimated by elimination (attendance minus digital, per day) ----
    self_study_sessions = sum(1 for row in offline_usage if row["book_id"] is None)
    category_counts: dict = {}
    for row in offline_usage:
        if row["book_category"]:
            category_counts[row["book_category"]] = (
                category_counts.get(row["book_category"], 0) + 1
            )
    by_category = [
        {"category": cat, "count": count}
        for cat, count in sorted(category_counts.items(), key=lambda x: -x[1])
    ]

    attendance_minutes_by_date: dict = {}
    for row in attendance:
        if row["duration_minutes"] is not None:
            attendance_minutes_by_date[row["date"]] = (
                attendance_minutes_by_date.get(row["date"], 0) + row["duration_minutes"]
            )
    digital_minutes_by_date: dict = {}
    for row in digital_usage:
        if row["duration_minutes"] is not None:
            digital_minutes_by_date[row["date"]] = (
                digital_minutes_by_date.get(row["date"], 0) + row["duration_minutes"]
            )
    coaching_minutes_by_date: dict = {}
    for row in coaching_usage:
        if row["duration_minutes"] is not None:
            coaching_minutes_by_date[row["date"]] = coaching_minutes_by_date.get(row["date"], 0) + row["duration_minutes"]
    estimated_offline_minutes = sum(
        max(att_minutes - digital_minutes_by_date.get(d, 0) - coaching_minutes_by_date.get(d, 0), 0)
        for d, att_minutes in attendance_minutes_by_date.items()
    )

    digital_durations = [
        row["duration_minutes"]
        for row in digital_usage
        if row["duration_minutes"] is not None
    ]

    analytics = {
        "overall": {
            "total_assessments": len(percentages),
            "average_percentage": round(mean(percentages), 2) if percentages else None,
            "trend": overall_trend,
            "trend_delta_percentage_points": overall_delta,
        },
        "attendance": {
            "total_sessions": len(attendance),
            "completed_sessions": sum(
                row["check_out"] is not None for row in attendance
            ),
            "average_duration_minutes": round(mean(all_durations), 2)
            if all_durations
            else None,
            "trend": attendance_trend,
            "trend_delta_minutes": attendance_delta,
            "attendance_rate_last_30_days_percent": attendance_rate_30d,
            "current_streak_days": current_streak,
            "days_since_last_visit": days_since_last_visit,
        },
        "exams": {
            "total_exams": len(exam_percentages),
            "average_percentage": round(mean(exam_percentages), 2)
            if exam_percentages
            else None,
            "trend": exam_trend,
            "trend_delta_percentage_points": exam_delta,
        },
        "quizzes": {
            "total_quizzes": len(quiz_percentages),
            "average_percentage": round(mean(quiz_percentages), 2)
            if quiz_percentages
            else None,
            "trend": quiz_trend,
            "trend_delta_percentage_points": quiz_delta,
        },
        "subjects": subjects_performance,
        "digital_library": {
            "total_sessions": len(digital_usage),
            "total_duration_minutes": sum(digital_durations),
            "average_duration_minutes": round(mean(digital_durations), 2)
            if digital_durations
            else None,
        },
        "offline_library": {
            "total_sessions": len(offline_usage),
            "self_study_sessions": self_study_sessions,
            "by_category": by_category,
            "estimated_total_minutes": estimated_offline_minutes,
        },
        "coaching": {
            "total_sessions": len(coaching_usage),
            "total_duration_minutes": sum(row["duration_minutes"] or 0 for row in coaching_usage),
            "average_duration_minutes": round(mean([row["duration_minutes"] for row in coaching_usage if row["duration_minutes"] is not None]), 2) if any(row["duration_minutes"] is not None for row in coaching_usage) else None,
        },
    }

    return {
        "student": dict(student),
        "attendance_history": [dict(row) for row in attendance],
        "digital_library_usage": [dict(row) for row in digital_usage],
        "offline_library_usage": [
            {
                "usage_id": row["usage_id"],
                "student_id": row["student_id"],
                "date": row["date"],
                "book_id": row["book_id"],
                "book_title": row["book_title"],
            }
            for row in offline_usage
        ],
        "coaching_usage": [dict(row) for row in coaching_usage],
        "exams_attempted": exams,
        "quizzes_attempted": quizzes,
        "score_trend": score_trend,
        "analytics": analytics,
    }


def _fmt(value, suffix: str = "") -> str:
    """None-safe formatter for the PDF -- shows 'N/A' instead of 'None'."""
    if value is None:
        return "N/A"
    return f"{value}{suffix}"


def _fig_to_image(fig, width_inch: float = 6.4, height_inch: float = 2.6) -> Image:
    """Render a matplotlib figure to a reportlab Image flowable, in-memory (no temp files)."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return Image(buf, width=width_inch * inch, height=height_inch * inch)


def _chart_score_trend(exams: list, quizzes: list):
    """
    Line chart: exam percentages and quiz percentages plotted separately
    over time, on real date axes with matplotlib's adaptive date locator
    (so tick density scales sanely whether there are 3 points or 300).
    Markers are shown only for small point counts -- past ~20 points,
    markers just clutter a line that's already clear on its own.
    Entries with no date can't be placed on a timeline and are skipped.
    Returns None if there isn't enough data for a meaningful trend.
    """
    exams_dated = [e for e in exams if e["date"]]
    quizzes_dated = [q for q in quizzes if q["date"]]
    if len(exams_dated) < 2 and len(quizzes_dated) < 2:
        return None

    fig, ax = plt.subplots(figsize=(6.4, 2.6))
    if exams_dated:
        x = [date.fromisoformat(e["date"]) for e in exams_dated]
        y = [e["percentage"] for e in exams_dated]
        ax.plot(
            x, y, marker="o" if len(x) <= 20 else None, label="Exams", color="#2c3e50"
        )
    if quizzes_dated:
        x = [date.fromisoformat(q["date"]) for q in quizzes_dated]
        y = [q["percentage"] for q in quizzes_dated]
        ax.plot(
            x, y, marker="s" if len(x) <= 20 else None, label="Quizzes", color="#e67e22"
        )
    ax.set_ylabel("Percentage")
    ax.set_ylim(0, 100)
    ax.set_title("Score Trend Over Time")
    ax.legend(fontsize=8)
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.xaxis.set_major_formatter(
        mdates.ConciseDateFormatter(ax.xaxis.get_major_locator())
    )
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    return _fig_to_image(fig)


def _chart_batch_comparison(items: list, title: str):
    """
    Grouped horizontal bar chart: this student's percentage vs the batch
    average, per assessment. Turns the batch_average_percentage column
    already in the report's tables into a direct visual comparison --
    a bar pair reads faster than scanning two number columns.
    Skips assessments with no batch average yet (nobody else has a score
    recorded for it), and returns None entirely if nothing qualifies.
    """
    usable = [i for i in items if i["batch_average_percentage"] is not None]
    if not usable:
        return None

    names = [i["assessment_name"] for i in usable]
    student_vals = [i["percentage"] for i in usable]
    batch_vals = [i["batch_average_percentage"] for i in usable]

    y_pos = list(range(len(names)))
    bar_height = 0.35
    fig_height = max(1.8, 0.5 * len(names) + 0.8)
    fig, ax = plt.subplots(figsize=(6.4, fig_height))
    ax.barh(
        [i + bar_height / 2 for i in y_pos],
        student_vals,
        height=bar_height,
        label="Student",
        color="#2980b9",
    )
    ax.barh(
        [i - bar_height / 2 for i in y_pos],
        batch_vals,
        height=bar_height,
        label="Batch Average",
        color="#95a5a6",
    )
    ax.set_yticks(y_pos)
    ax.set_yticklabels(names, fontsize=8)
    ax.set_xlabel("Percentage")
    ax.set_xlim(0, 100)
    ax.set_title(title)
    ax.legend(fontsize=8)
    ax.invert_yaxis()  # first assessment at top, reading order
    fig.tight_layout()
    return _fig_to_image(fig, height_inch=fig_height)


def _chart_subject_performance(subjects: list):
    """Bar chart: average percentage per subject. None if no subjects tagged."""
    if not subjects:
        return None

    fig, ax = plt.subplots(figsize=(6.4, 2.6))
    names = [s["subject"] for s in subjects]
    values = [s["average_percentage"] or 0 for s in subjects]
    bars = ax.bar(names, values, color="#2980b9")
    ax.set_ylabel("Average %")
    ax.set_ylim(0, 100)
    ax.set_title("Subject-wise Average Performance")
    for bar, val in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            val + 2,
            f"{val:g}%",
            ha="center",
            fontsize=8,
        )
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    return _fig_to_image(fig)


def _chart_attendance_duration(attendance_history: list):
    """
    Line chart: session duration over time. Two modes depending on
    volume, both chosen for readability rather than always doing the
    same thing regardless of scale:

    - <=60 sessions: plot every session on a real date axis, with
      matplotlib's adaptive date locator handling tick spacing.
    - >60 sessions: plot WEEKLY AVERAGES instead. A raw line across
      hundreds of individual points renders as a dense, unreadable
      block in a report-sized chart -- a weekly average reveals the
      actual trend shape, which is what this chart is for.

    None if fewer than 2 completed sessions exist (nothing to trend).
    """
    dated = [
        (row["date"], row["duration_minutes"])
        for row in attendance_history
        if row["duration_minutes"] is not None
    ]
    dated.sort(key=lambda x: x[0])
    if len(dated) < 2:
        return None

    fig, ax = plt.subplots(figsize=(6.4, 2.6))

    if len(dated) > 60:
        weekly: dict = {}
        for d_str, minutes in dated:
            d = date.fromisoformat(d_str)
            week_start = d - timedelta(days=d.weekday())
            weekly.setdefault(week_start, []).append(minutes)
        points = sorted(weekly.items())
        x = [w for w, _ in points]
        y = [mean(v) for _, v in points]
        ax.plot(x, y, color="#27ae60", linewidth=1.5)
        ax.set_ylabel("Avg minutes/session (weekly)")
    else:
        x = [date.fromisoformat(d) for d, _ in dated]
        y = [m for _, m in dated]
        ax.plot(
            x, y, marker="o" if len(x) <= 20 else None, color="#27ae60", linewidth=1.5
        )
        ax.set_ylabel("Minutes")

    ax.set_title("Attendance Duration Trend")
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.xaxis.set_major_formatter(
        mdates.ConciseDateFormatter(ax.xaxis.get_major_locator())
    )
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    return _fig_to_image(fig)


def _chart_library_time_split(digital_usage: list, offline_estimated_minutes: int):
    """
    Horizontal bar chart (NOT a pie): digital vs offline library time.
    This comparison is frequently lopsided (a student barely touching
    digital resources vs. spending most of their time reading) -- a pie
    slice for the smaller side can shrink to the point of being
    invisible or unreadable, while a bar's length and printed value stay
    legible regardless of how skewed the split is.
    None if there's no time recorded on either side.
    """
    digital_total = sum(
        row["duration_minutes"]
        for row in digital_usage
        if row["duration_minutes"] is not None
    )
    if digital_total == 0 and offline_estimated_minutes == 0:
        return None

    categories = ["Digital Library", "Offline Library (est.)"]
    values = [digital_total, offline_estimated_minutes]
    max_val = max(values) if max(values) > 0 else 1

    fig, ax = plt.subplots(figsize=(6.4, 2.0))
    bars = ax.barh(categories, values, color=["#8e44ad", "#16a085"])
    ax.set_xlabel("Minutes")
    ax.set_xlim(0, max_val * 1.3)  # headroom so value labels don't clip
    ax.set_title("Time Split: Digital vs Offline Library")
    for bar, val in zip(bars, values):
        hours = val / 60
        ax.text(
            bar.get_width() + max_val * 0.02,
            bar.get_y() + bar.get_height() / 2,
            f"{val} min ({hours:.1f} hrs)",
            va="center",
            fontsize=8,
        )
    fig.tight_layout()
    return _fig_to_image(fig, height_inch=2.0)


def _chart_book_categories(by_category: list):
    """
    Horizontal bar chart: offline reading count per book category.
    Horizontal bars scale cleanly to any number of categories (unlike a
    pie, which gets unreadable past a handful of slices) and keep
    category names fully legible instead of rotated/truncated.
    None if there's no categorised offline reading yet.
    """
    if not by_category:
        return None

    fig_height = max(1.6, 0.4 * len(by_category) + 0.6)
    fig, ax = plt.subplots(figsize=(6.4, fig_height))
    categories = [c["category"] for c in by_category]
    counts = [c["count"] for c in by_category]
    ax.barh(categories, counts, color="#d35400")
    ax.set_xlabel("Times Read")
    ax.set_title("Offline Reading by Book Category")
    ax.invert_yaxis()  # most-read category at top
    for i, v in enumerate(counts):
        ax.text(v + max(counts) * 0.02, i, str(v), va="center", fontsize=8)
    fig.tight_layout()
    return _fig_to_image(fig, height_inch=fig_height)


def _render_report_pdf(data: dict) -> bytes:
    """
    Render the dashboard data dict (same shape as the JSON endpoint)
    into a printable PDF using reportlab's Platypus layer -- tables and
    paragraphs rather than manual coordinate drawing, so sections flow
    and paginate automatically as content grows.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
    )
    styles = getSampleStyleSheet()
    section_style = ParagraphStyle(
        "SectionHeading", parent=styles["Heading2"], spaceBefore=14, spaceAfter=6
    )
    table_header_style = TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            (
                "ROWBACKGROUNDS",
                (0, 1),
                (-1, -1),
                [colors.white, colors.HexColor("#f4f6f7")],
            ),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
    )

    story = []
    student = data["student"]
    analytics = data["analytics"]

    # ---- Header ----
    story.append(Paragraph("Student Performance Report", styles["Title"]))
    story.append(
        Paragraph(
            f"Generated on {date.today().isoformat()}",
            ParagraphStyle("Sub", parent=styles["Normal"], textColor=colors.grey),
        )
    )
    story.append(Spacer(1, 12))

    # ---- Student details ----
    story.append(Paragraph("Student Details", section_style))
    details_table = Table(
        [
            ["Student ID", str(student["student_id"]), "Name", student["name"]],
            [
                "Gender",
                _fmt(student.get("gender")),
                "Status",
                student.get("status", "N/A"),
            ],
            [
                "Join Date",
                _fmt(student.get("join_date")),
                "Phone",
                _fmt(student.get("phone")),
            ],
        ],
        colWidths=[1.1 * inch, 2.1 * inch, 1.1 * inch, 2.1 * inch],
    )
    details_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(details_table)

    # ---- Overall performance summary ----
    story.append(Paragraph("Overall Performance Summary", section_style))
    overall = analytics["overall"]
    summary_rows = [["Metric", "Value"]]
    summary_rows.append(
        ["Total assessments (exams + quizzes)", str(overall["total_assessments"])]
    )
    summary_rows.append(
        ["Average percentage", _fmt(overall["average_percentage"], "%")]
    )
    summary_rows.append(["Trend", overall["trend"]])
    summary_rows.append(
        [
            "Change (first to latest)",
            _fmt(overall["trend_delta_percentage_points"], " pts"),
        ]
    )
    summary_table = Table(summary_rows, colWidths=[3.4 * inch, 3.0 * inch])
    summary_table.setStyle(table_header_style)
    story.append(summary_table)

    # ---- Attendance ----
    story.append(Paragraph("Attendance", section_style))
    att = analytics["attendance"]
    att_rows = [["Metric", "Value"]]
    att_rows.append(["Total sessions", str(att["total_sessions"])])
    att_rows.append(["Completed sessions", str(att["completed_sessions"])])
    att_rows.append(["Average duration", _fmt(att["average_duration_minutes"], " min")])
    att_rows.append(["Duration trend", att["trend"]])
    att_rows.append(
        ["Duration change (first to latest)", _fmt(att["trend_delta_minutes"], " min")]
    )
    att_rows.append(
        [
            "Attendance rate (last 30 days)",
            _fmt(att["attendance_rate_last_30_days_percent"], "%"),
        ]
    )
    att_rows.append(["Current streak", f"{att['current_streak_days']} day(s)"])
    att_rows.append(["Days since last visit", _fmt(att["days_since_last_visit"])])
    att_table = Table(att_rows, colWidths=[3.4 * inch, 3.0 * inch])
    att_table.setStyle(table_header_style)
    story.append(att_table)

    attendance_chart = _chart_attendance_duration(data["attendance_history"])
    if attendance_chart:
        story.append(Spacer(1, 8))
        story.append(attendance_chart)
    else:
        story.append(Spacer(1, 4))
        story.append(
            Paragraph(
                "Not enough completed sessions yet for a duration trend chart.",
                styles["Normal"],
            )
        )

    # ---- Exams ----
    story.append(Paragraph("Exams", section_style))
    exams_summary = analytics["exams"]
    story.append(
        Paragraph(
            f"Total exams: {exams_summary['total_exams']}  |  "
            f"Average: {_fmt(exams_summary['average_percentage'], '%')}  |  "
            f"Trend: {exams_summary['trend']}",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 6))
    if data["exams_attempted"]:
        exam_rows = [["Exam", "Date", "Subject", "Marks", "%", "Batch Avg %"]]
        for e in data["exams_attempted"]:
            exam_rows.append(
                [
                    e["assessment_name"],
                    _fmt(e["date"]),
                    _fmt(e["subject"]),
                    f"{e['marks_obtained']:g}/{e['max_marks']:g}",
                    f"{e['percentage']:g}%",
                    _fmt(e["batch_average_percentage"], "%"),
                ]
            )
        exam_table = Table(
            exam_rows,
            colWidths=[
                1.6 * inch,
                0.9 * inch,
                1.1 * inch,
                0.9 * inch,
                0.7 * inch,
                1.0 * inch,
            ],
        )
        exam_table.setStyle(table_header_style)
        story.append(exam_table)

        exam_batch_chart = _chart_batch_comparison(
            data["exams_attempted"], "Exams: Student vs Batch Average"
        )
        if exam_batch_chart:
            story.append(Spacer(1, 8))
            story.append(exam_batch_chart)
    else:
        story.append(Paragraph("No exams attempted yet.", styles["Normal"]))

    # ---- Quizzes ----
    story.append(Paragraph("Quizzes", section_style))
    quizzes_summary = analytics["quizzes"]
    story.append(
        Paragraph(
            f"Total quizzes: {quizzes_summary['total_quizzes']}  |  "
            f"Average: {_fmt(quizzes_summary['average_percentage'], '%')}  |  "
            f"Trend: {quizzes_summary['trend']}",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 6))
    if data["quizzes_attempted"]:
        quiz_rows = [["Quiz", "Date", "Subject", "Marks", "%", "Batch Avg %"]]
        for q in data["quizzes_attempted"]:
            quiz_rows.append(
                [
                    q["assessment_name"],
                    _fmt(q["date"]),
                    _fmt(q["subject"]),
                    f"{q['marks_obtained']:g}/{q['max_marks']:g}",
                    f"{q['percentage']:g}%",
                    _fmt(q["batch_average_percentage"], "%"),
                ]
            )
        quiz_table = Table(
            quiz_rows,
            colWidths=[
                1.6 * inch,
                0.9 * inch,
                1.1 * inch,
                0.9 * inch,
                0.7 * inch,
                1.0 * inch,
            ],
        )
        quiz_table.setStyle(table_header_style)
        story.append(quiz_table)

        quiz_batch_chart = _chart_batch_comparison(
            data["quizzes_attempted"], "Quizzes: Student vs Batch Average"
        )
        if quiz_batch_chart:
            story.append(Spacer(1, 8))
            story.append(quiz_batch_chart)
    else:
        story.append(Paragraph("No quizzes attempted yet.", styles["Normal"]))

    score_chart = _chart_score_trend(data["exams_attempted"], data["quizzes_attempted"])
    if score_chart:
        story.append(Spacer(1, 8))
        story.append(score_chart)
    else:
        story.append(Spacer(1, 4))
        story.append(
            Paragraph(
                "Not enough exam/quiz data yet for a score trend chart.",
                styles["Normal"],
            )
        )

    # ---- Subject-wise performance ----
    story.append(Paragraph("Subject-wise Performance", section_style))
    if analytics["subjects"]:
        subj_rows = [["Subject", "Assessments", "Average %", "Trend", "Change (pts)"]]
        for s in analytics["subjects"]:
            subj_rows.append(
                [
                    s["subject"],
                    str(s["total_assessments"]),
                    _fmt(s["average_percentage"], "%"),
                    s["trend"],
                    _fmt(s["trend_delta_percentage_points"]),
                ]
            )
        subj_table = Table(
            subj_rows,
            colWidths=[1.6 * inch, 1.1 * inch, 1.1 * inch, 1.3 * inch, 1.1 * inch],
        )
        subj_table.setStyle(table_header_style)
        story.append(subj_table)

        subject_chart = _chart_subject_performance(analytics["subjects"])
        if subject_chart:
            story.append(Spacer(1, 8))
            story.append(subject_chart)
    else:
        story.append(Paragraph("No subject-tagged assessments yet.", styles["Normal"]))

    # ---- Library usage ----
    story.append(Paragraph("Library Usage", section_style))
    dig = analytics["digital_library"]
    off = analytics["offline_library"]
    lib_rows = [["Metric", "Value"]]
    lib_rows.append(["Digital library sessions", str(dig["total_sessions"])])
    lib_rows.append(
        ["Digital library avg. duration", _fmt(dig["average_duration_minutes"], " min")]
    )
    lib_rows.append(["Offline library sessions", str(off["total_sessions"])])
    lib_rows.append(
        ["Self-study sessions (no book logged)", str(off["self_study_sessions"])]
    )
    lib_rows.append(
        [
            "Offline time (estimated by elimination)",
            f"{off['estimated_total_minutes']} min",
        ]
    )
    lib_table = Table(lib_rows, colWidths=[3.4 * inch, 3.0 * inch])
    lib_table.setStyle(table_header_style)
    story.append(lib_table)

    lib_chart = _chart_library_time_split(
        data["digital_library_usage"], off["estimated_total_minutes"]
    )
    if lib_chart:
        story.append(Spacer(1, 8))
        story.append(lib_chart)

    if off["by_category"]:
        story.append(Spacer(1, 8))
        story.append(Paragraph("Offline reading by book category:", styles["Normal"]))
        cat_rows = [["Category", "Count"]] + [
            [c["category"], str(c["count"])] for c in off["by_category"]
        ]
        cat_table = Table(cat_rows, colWidths=[3.4 * inch, 3.0 * inch])
        cat_table.setStyle(table_header_style)
        story.append(cat_table)

        category_chart = _chart_book_categories(off["by_category"])
        if category_chart:
            story.append(Spacer(1, 8))
            story.append(category_chart)

    doc.build(story)
    return buffer.getvalue()


@router.get("/students/{student_id}", response_model=StudentDashboardResponse)
def get_student_dashboard(
    student_id: int,
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    """Return a student's complete dashboard profile and performance summary as JSON."""
    return _build_dashboard_data(db, student_id)


@router.get("/students/{student_id}/report")
def get_student_report_pdf(
    student_id: int,
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    """
    Generate and download a PDF performance report for a student --
    same underlying data and numbers as the JSON dashboard, rendered as
    a printable document staff can save or hand to a student/parent.
    """
    data = _build_dashboard_data(db, student_id)
    pdf_bytes = _render_report_pdf(data)

    filename = f"student_{student_id}_performance_report.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
