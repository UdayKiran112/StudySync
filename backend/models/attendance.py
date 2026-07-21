"""
models/attendance.py
---------------------
Pydantic models for the attendance module.

Attendance isn't a single CRUD resource — it's a two-step workflow:
check-in (arrival) and check-out (departure), recorded separately because
staff don't know the departure time when a student walks in.

`session` is no longer client-supplied on check-in/check-out — it's
auto-detected by the router from the actual times entered (see
routers/attendance.py): "Morning" if check-in is before 1 PM,
"Afternoon" otherwise, reclassified to "Full Day" at check-out if the
stay genuinely spans the 1-2 PM lunch break.

`duration_minutes` is likewise computed by the router, not the database
-- it needs to exclude the lunch hour for Full Day stays, which isn't
expressible as a pure SQL generated-column formula.
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

    No `session` field: it's auto-detected from `check_in` in the router.
    """

    student_id: int
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

    This does NOT create a new row — it finds this student's single
    currently-open session (check_out still NULL; the schema's partial
    unique index guarantees there's at most one) and fills in check_out.
    If there's no open session, the router returns 404.

    No `session` field needed: the open session is found directly, and
    its final session label ("Morning" / "Afternoon" / "Full Day") is
    determined here based on check_in vs check_out, not chosen by staff.
    """

    student_id: int
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

    session and duration_minutes are NOT accepted here -- they are
    recomputed automatically by the router whenever a correction changes
    check_in or check_out, using the same auto-detection logic as a
    normal check-out.
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
