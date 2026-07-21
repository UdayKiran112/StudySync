"""Request and response models for occasional coaching classes."""

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import Field, model_validator

from models.common import RequestModel
from models.validators import validate_hhmm


class CoachingClassCreate(RequestModel):
    title: str = Field(..., min_length=1)
    class_date: date
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    subject: Optional[str] = None
    instructor: Optional[str] = None
    venue: Optional[str] = None
    capacity: Optional[int] = Field(None, gt=0)
    notes: Optional[str] = None


class CoachingClassUpdate(RequestModel):
    title: Optional[str] = Field(None, min_length=1)
    class_date: Optional[date] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    subject: Optional[str] = None
    instructor: Optional[str] = None
    venue: Optional[str] = None
    capacity: Optional[int] = Field(None, gt=0)
    notes: Optional[str] = None


class CoachingClassResponse(CoachingClassCreate):
    class_id: int
    created_at: datetime


class CoachingEnrollmentCreate(RequestModel):
    participant_type: Literal["Library Student", "External Student"]
    student_id: Optional[int] = None
    external_name: Optional[str] = Field(None, min_length=1)
    village: Optional[str] = None
    phone: Optional[str] = None
    gender: Optional[Literal["Male", "Female", "Other"]] = None
    guardian_name: Optional[str] = None
    notes: Optional[str] = None

    @model_validator(mode="after")
    def validate_participant(self):
        if self.participant_type == "Library Student" and self.student_id is None:
            raise ValueError("student_id is required for a Library Student")
        if self.participant_type == "External Student" and not self.external_name:
            raise ValueError("external_name is required for an External Student")
        return self


class CoachingEnrollmentUpdate(RequestModel):
    attendance_status: Optional[Literal["Registered", "Present", "Absent", "Cancelled"]] = None
    village: Optional[str] = None
    phone: Optional[str] = None
    gender: Optional[Literal["Male", "Female", "Other"]] = None
    guardian_name: Optional[str] = None
    notes: Optional[str] = None


class CoachingEnrollmentResponse(RequestModel):
    enrollment_id: int
    class_id: int
    participant_type: str
    student_id: Optional[int] = None
    external_name: Optional[str] = None
    village: Optional[str] = None
    phone: Optional[str] = None
    gender: Optional[str] = None
    guardian_name: Optional[str] = None
    notes: Optional[str] = None
    attendance_status: str
    enrolled_at: datetime
    participant_name: str
