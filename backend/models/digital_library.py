"""
models/digital_library.py
--------------------------
Pydantic models for the digital library usage module.

Same two-step shape as attendance (check-in records arrival, check-out
fills in departure later), but with two differences from attendance that
come straight from the schema:

  1. No `session` column and no UNIQUE(student_id, date, session) — a
     student can rack up multiple digital library sessions on the same
     date. What's actually enforced (per your answer) is that they can't
     have two OPEN sessions at once, checked across all dates, same as
     attendance.

  2. account_type / subscription_id are a matched pair, per the schema's
     CHECK constraint:
         'Library Subscription' -> subscription_id required
         'Own Account'          -> subscription_id must be NULL
     Enforced here too (not just left to the DB) so a bad combination
     comes back as a clean 422 instead of a raw sqlite3.IntegrityError.
"""

import re
from pydantic import BaseModel, Field, model_validator, field_validator
from typing import Optional, Literal
from datetime import date as date_type


def validate_hhmm(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value

    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
        raise ValueError("Time must be in HH:MM 24-hour format, e.g. 09:30")

    return value


class DigitalLibraryCheckIn(BaseModel):
    """
    Data required to start a digital library session.

    `date` and `in_time` are optional — default to today / current
    server time in the router, same convention as attendance.
    """

    student_id: int
    account_type: Literal["Library Subscription", "Own Account"]
    subscription_id: Optional[str] = None
    platform_name: str = Field(..., description="e.g. 'JSTOR', 'Britannica Online'")
    purpose: Optional[str] = None
    notes: Optional[str] = None
    date: Optional[date_type] = None
    in_time: Optional[str] = Field(
        None, description="HH:MM 24-hour. Defaults to current server time."
    )

    @field_validator("in_time")
    @classmethod
    def validate_in_time(cls, value: Optional[str]) -> Optional[str]:
        return validate_hhmm(value)

    @model_validator(mode="after")
    def _check_subscription_pairing(self) -> "DigitalLibraryCheckIn":
        if self.account_type == "Library Subscription" and not self.subscription_id:
            raise ValueError(
                "subscription_id is required when account_type is "
                "'Library Subscription'"
            )
        if self.account_type == "Own Account" and self.subscription_id is not None:
            raise ValueError(
                "subscription_id must be omitted when account_type is 'Own Account'"
            )
        return self


class DigitalLibraryCheckOut(BaseModel):
    """
    Data required to close out a digital library session.

    No usage_id needed — since a student can only ever have one open
    session at a time (enforced at check-in), the router just looks up
    that student's open row.
    """

    student_id: int
    out_time: Optional[str] = Field(
        None, description="HH:MM 24-hour. Defaults to current server time."
    )

    @field_validator("out_time")
    @classmethod
    def validate_out_time(cls, value: Optional[str]) -> Optional[str]:
        return validate_hhmm(value)


class DigitalLibraryResponse(BaseModel):
    """Shape of a single digital library usage record returned by the API."""

    usage_id: int
    student_id: int
    date: date_type
    in_time: str
    out_time: Optional[str] = None
    duration_minutes: Optional[int] = None
    account_type: str
    subscription_id: Optional[str] = None
    platform_name: str
    purpose: Optional[str] = None
    notes: Optional[str] = None
