"""Response models for the staff-facing student profile dashboard."""

from datetime import date as date_type
from typing import List, Optional

from pydantic import BaseModel

from models.attendance import AttendanceResponse
from models.digital_library import DigitalLibraryResponse
from models.students import StudentResponse


class OfflineLibraryProfileItem(BaseModel):
    usage_id: int
    student_id: int
    date: date_type
    book_id: Optional[str] = None
    book_title: Optional[str] = None


class AssessmentAttempt(BaseModel):
    assessment_id: int
    assessment_name: str
    assessment_type: str
    date: Optional[date_type] = None
    subject: Optional[str] = None
    marks_obtained: float
    max_marks: float
    percentage: float
    remarks: Optional[str] = None
    # Average percentage across ALL students who attempted this same
    # exam/quiz -- lets staff see "scored 80%, batch averaged 65%"
    # instead of a percentage in isolation. None if no one else has
    # a score recorded for it yet.
    batch_average_percentage: Optional[float] = None


class SubjectPerformance(BaseModel):
    """Exams + quizzes for one subject, combined and trended independently."""

    subject: str
    total_assessments: int
    average_percentage: Optional[float] = None
    trend: str
    trend_delta_percentage_points: Optional[float] = None


class BookCategoryCount(BaseModel):
    category: str
    count: int


class AttendanceAnalytics(BaseModel):
    total_sessions: int
    completed_sessions: int
    average_duration_minutes: Optional[float] = None
    trend: str
    trend_delta_minutes: Optional[float] = None
    # Regularity signals -- how consistently the student is showing up,
    # not just how long they stay once they do.
    attendance_rate_last_30_days_percent: Optional[float] = None
    current_streak_days: int
    days_since_last_visit: Optional[int] = None


class ExamAnalytics(BaseModel):
    total_exams: int
    average_percentage: Optional[float] = None
    trend: str
    trend_delta_percentage_points: Optional[float] = None


class QuizAnalytics(BaseModel):
    total_quizzes: int
    average_percentage: Optional[float] = None
    trend: str
    trend_delta_percentage_points: Optional[float] = None


class OverallAnalytics(BaseModel):
    """Combined exams+quizzes view, for an at-a-glance summary."""

    total_assessments: int
    average_percentage: Optional[float] = None
    trend: str
    trend_delta_percentage_points: Optional[float] = None


class DigitalLibraryAnalytics(BaseModel):
    total_sessions: int
    total_duration_minutes: int
    average_duration_minutes: Optional[float] = None


class OfflineLibraryAnalytics(BaseModel):
    total_sessions: int
    self_study_sessions: int  # entries with no book_id (own material)
    by_category: List[BookCategoryCount]
    # No stored duration for offline sessions (by design). Instead this
    # is inferred by elimination, per day: attendance time minus digital
    # library time = time assumed spent in the offline library, on the
    # premise that a student present at the centre is in exactly one of
    # the two at any moment. Clamped at 0 for any day where digital time
    # happens to exceed logged attendance time (data entry mismatch).
    estimated_total_minutes: int


class CoachingAnalytics(BaseModel):
    total_sessions: int
    total_duration_minutes: int
    average_duration_minutes: Optional[float] = None


class PerformanceAnalytics(BaseModel):
    overall: OverallAnalytics
    attendance: AttendanceAnalytics
    exams: ExamAnalytics
    quizzes: QuizAnalytics
    subjects: List[SubjectPerformance]
    digital_library: DigitalLibraryAnalytics
    offline_library: OfflineLibraryAnalytics
    coaching: CoachingAnalytics


class StudentDashboardResponse(BaseModel):
    student: StudentResponse
    attendance_history: List[AttendanceResponse]
    digital_library_usage: List[DigitalLibraryResponse]
    offline_library_usage: List[OfflineLibraryProfileItem]
    exams_attempted: List[AssessmentAttempt]
    quizzes_attempted: List[AssessmentAttempt]
    score_trend: List[AssessmentAttempt]
    analytics: PerformanceAnalytics
