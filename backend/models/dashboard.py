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


class SubscriptionProfileItem(BaseModel):
    subscription_id: str
    name: str
    type: Optional[str] = None
    cost: Optional[float] = None
    validity_days: Optional[int] = None
    status: str
    used_by_student: bool


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


class PerformanceAnalytics(BaseModel):
    total_assessments: int
    overall_average_percentage: Optional[float] = None
    exam_average_percentage: Optional[float] = None
    quiz_average_percentage: Optional[float] = None
    trend: str
    trend_delta_percentage_points: Optional[float] = None
    attendance_sessions: int
    completed_attendance_sessions: int
    average_attendance_duration_minutes: Optional[float] = None
    digital_library_sessions: int
    offline_library_sessions: int


class StudentDashboardResponse(BaseModel):
    student: StudentResponse
    attendance_history: List[AttendanceResponse]
    digital_library_usage: List[DigitalLibraryResponse]
    offline_library_usage: List[OfflineLibraryProfileItem]
    subscriptions: List[SubscriptionProfileItem]
    exams_attempted: List[AssessmentAttempt]
    quizzes_attempted: List[AssessmentAttempt]
    score_trend: List[AssessmentAttempt]
    analytics: PerformanceAnalytics
