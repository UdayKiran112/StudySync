"""
models/offline_library.py
---------------------------
Pydantic models for the offline_library_usage module.

Per the schema, this table has no in_time/out_time or duration —
it's a simple log: "this student read this book on this date."
book_id is nullable (per your decision): when a student reads their
own material rather than a catalogued book, book_id is just left NULL
with no further detail captured.

Unlike attendance and digital_library, there's no check-in/check-out
flow here — a single POST creates the complete record.
"""

from pydantic import BaseModel
from typing import Optional
from datetime import date as date_type


class OfflineLibraryCreate(BaseModel):
    """
    Data required to log an offline library usage entry.

    `date` is optional — defaults to today in the router, same
    convention as digital_library.
    """

    student_id: int
    book_id: Optional[str] = None
    date: Optional[date_type] = None


class OfflineLibraryUpdate(BaseModel):
    """Correction of a mistaken entry — e.g. wrong book_id or date typed in."""

    book_id: Optional[str] = None
    date: Optional[date_type] = None


class OfflineLibraryResponse(BaseModel):
    usage_id: int
    student_id: int
    date: date_type
    book_id: Optional[str] = None
