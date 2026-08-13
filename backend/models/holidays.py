"""
models/holidays.py
------------------
One-off days the library is closed, on top of the standing rule
(Sundays and the 2nd/4th/5th Saturday of each month -- see
frontend/src/lib/holidays.ts). Staff add a holiday here (a festival, a
power cut) so the analytics and attendance calendar treat that day as
closed for every student.

holiday_date is UNIQUE in the table, so a single day can only be
recorded as closed once -- the frontend renders one-off holidays
alongside the standing rule, and the two can't double-count.
"""

from datetime import date as date_type

from pydantic import BaseModel, Field

from models.common import RequestModel


class HolidayCreate(RequestModel):
    holiday_date: date_type = Field(
        ...,
        description="The date the library is closed, YYYY-MM-DD",
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="e.g. 'Pongal', 'Deepavali', 'Staff development day'",
    )
    notes: str | None = Field(default=None, max_length=500)


class HolidayUpdate(RequestModel):
    """All fields optional -- only supplied fields are changed."""

    holiday_date: date_type | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=500)


class HolidayResponse(BaseModel):
    holiday_id: int
    holiday_date: date_type
    name: str
    notes: str | None = None
    created_at: str
