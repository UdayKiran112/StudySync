"""Pydantic models for quizzes and student quiz scores."""

from datetime import date as date_type
from typing import Optional

from pydantic import BaseModel, Field

from models.common import RequestModel


class QuizCreate(RequestModel):
    quiz_name: str = Field(..., min_length=1)
    quiz_date: Optional[date_type] = None
    subject: Optional[str] = None
    max_marks: float = Field(..., gt=0)


class QuizUpdate(RequestModel):
    quiz_name: Optional[str] = Field(None, min_length=1)
    quiz_date: Optional[date_type] = None
    subject: Optional[str] = None
    max_marks: Optional[float] = Field(None, gt=0)


class QuizResponse(BaseModel):
    quiz_id: int
    quiz_name: str
    quiz_date: Optional[date_type] = None
    subject: Optional[str] = None
    max_marks: float


class QuizScoreCreate(RequestModel):
    student_id: int
    score: float = Field(..., ge=0)
    remarks: Optional[str] = None


class QuizScoreUpdate(RequestModel):
    score: Optional[float] = Field(None, ge=0)
    remarks: Optional[str] = None


class QuizScoreResponse(BaseModel):
    score_id: int
    student_id: int
    quiz_id: int
    score: float
    remarks: Optional[str] = None
