"""
models/students.py
-------------------
Pydantic models define what shape of data the API accepts (requests) and
returns (responses). FastAPI uses these to auto-validate input and to
generate the /docs page.
"""

from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import date, datetime


class StudentCreate(BaseModel):
    """Shape of data required to create a new student."""

    student_id: int = Field(..., description="Unique student ID, e.g. 4351")
    name: str
    gender: Optional[Literal["Male", "Female", "Other"]] = None
    date_of_birth: Optional[date] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    join_date: date
    photo_path: Optional[str] = None
    status: Literal["Active", "Inactive"] = "Active"


class StudentUpdate(BaseModel):
    """
    Shape of data for updating a student. All fields optional since an
    update might only change one or two fields (e.g. just status).
    """

    name: Optional[str] = None
    gender: Optional[Literal["Male", "Female", "Other"]] = None
    date_of_birth: Optional[date] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    photo_path: Optional[str] = None
    status: Optional[Literal["Active", "Inactive"]] = None


class StudentResponse(BaseModel):
    """Shape of data returned by the API for a single student."""

    student_id: int
    name: str
    gender: Optional[str] = None
    date_of_birth: Optional[date] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    join_date: date
    photo_path: Optional[str] = None
    status: str
    created_at: datetime
    updated_at: datetime
