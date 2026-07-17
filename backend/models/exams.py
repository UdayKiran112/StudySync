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
    max_marks: float


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
    marks_obtained: float
    remarks: Optional[str] = None
