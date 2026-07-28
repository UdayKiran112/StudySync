"""Pydantic models for exams and the marks recorded against each exam."""

from datetime import date as date_type
from typing import Optional

from pydantic import BaseModel, Field

from models.common import RequestModel


class ExamCreate(RequestModel):
    exam_name: str = Field(..., min_length=1)
    exam_date: Optional[date_type] = None
    subject: Optional[str] = None
    max_marks: float = Field(..., gt=0)


class ExamUpdate(RequestModel):
    exam_name: Optional[str] = Field(None, min_length=1)
    exam_date: Optional[date_type] = None
    subject: Optional[str] = None
    max_marks: Optional[float] = Field(None, gt=0)


class ExamResponse(BaseModel):
    exam_id: int
    exam_name: str
    exam_date: Optional[date_type] = None
    subject: Optional[str] = None
    # FIX: was `float` (required). The daily activity-log loader creates
    # exams without a max_marks value (the CSV only ever supplies a topic,
    # never a numeric max), and the database schema was migrated to allow
    # NULL here to accommodate that -- but this response model still
    # demanded a non-null float, so FastAPI's response validation rejected
    # every exam row where max_marks was NULL (ResponseValidationError,
    # 500). Optional[float] lets those rows serialize as `null` instead.
    max_marks: Optional[float] = None


class ExamMarkCreate(RequestModel):
    student_id: int
    marks_obtained: float = Field(..., ge=0)
    remarks: Optional[str] = None


class ExamMarkUpdate(RequestModel):
    marks_obtained: Optional[float] = Field(None, ge=0)
    remarks: Optional[str] = None


class ExamMarkResponse(BaseModel):
    mark_id: int
    student_id: int
    exam_id: int
    # FIX: was `float` (required). Same root cause as ExamResponse.max_marks
    # above -- the activity-log loader inserts exam_marks rows with
    # marks_obtained left NULL (no score in that source), so this must be
    # Optional or the API 500s the instant it returns one of those rows.
    marks_obtained: Optional[float] = None
    remarks: Optional[str] = None
