"""
models/attendance.py
---------------------
Pydantic models for the attendance module.

Attendance isn't a single CRUD resource — it's a two-step workflow:
check-in (arrival) and check-out (departure), recorded separately because
staff don't know the departure time when a student walks in.

`duration_minutes` is a SQLite GENERATED column (see schema.sql), so it's
never accepted on input — it's computed by the database and only ever
appears in responses.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, Literal
from datetime import date as date_type
from models.common import RequestModel
from models.validators import validate_hhmm


class AttendanceCheckIn(RequestModel):
    """
    Data required to check a student in.

    `date` and `check_in` are optional — if omitted, they default to
    today's date / the current server time (applied in the router, not
    here, so the default reflects the moment the request is handled).
    Explicit values are there for corrections or backdating a missed
    entry, not for everyday use.
    """

    student_id: int
    session: Literal["Morning", "Afternoon"]
    date: Optional[date_type] = None
    check_in: Optional[str] = Field(
        None, description="HH:MM 24-hour. Defaults to current server time."
    )

    @field_validator("check_in")
    @classmethod
    def validate_check_in(cls, value: Optional[str]) -> Optional[str]:
        return validate_hhmm(value)


class AttendanceCheckOut(RequestModel):
    """
    Data required to check a student out.

    This does NOT create a new row — it finds the matching open session
    (same student_id + date + session, check_out still NULL) and fills
    in check_out. If no such open session exists, the router returns 404.
    """

    student_id: int
    session: Literal["Morning", "Afternoon"]
    date: Optional[date_type] = None
    check_out: Optional[str] = Field(
        None, description="HH:MM 24-hour. Defaults to current server time."
    )

    @field_validator("check_out")
    @classmethod
    def validate_check_out(cls, value: Optional[str]) -> Optional[str]:
        return validate_hhmm(value)


class AttendanceResponse(BaseModel):
    """Shape of a single attendance record returned by the API."""

    attendance_id: int
    student_id: int
    date: date_type
    session: str
    check_in: Optional[str] = None
    check_out: Optional[str] = None
    duration_minutes: Optional[int] = None


class AttendanceUpdate(RequestModel):
    """
    Manual correction of a mistaken entry (e.g. staff typo'd a time).
    All fields optional — only supplied fields are changed.
    """

    check_in: Optional[str] = Field(None, description="HH:MM 24-hour.")
    check_out: Optional[str] = Field(None, description="HH:MM 24-hour.")

    @field_validator("check_in")
    @classmethod
    def validate_check_in(cls, value: Optional[str]) -> Optional[str]:
        return validate_hhmm(value)

    @field_validator("check_out")
    @classmethod
    def validate_check_out(cls, value: Optional[str]) -> Optional[str]:
        return validate_hhmm(value)
