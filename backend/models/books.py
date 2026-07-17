"""
models/books.py
----------------
Pydantic models for the books catalog.

books is a reference catalog only — no borrowing, no availability
tracking (per your earlier decision to remove take-home borrowing).
book_id is staff-assigned (e.g. "1556", "P247"), matching the schema's
plain TEXT PRIMARY KEY with no sequence — same convention as
subscription_id.
"""

from pydantic import BaseModel, Field
from typing import Optional
from datetime import date as date_type
from models.common import RequestModel


class BookCreate(RequestModel):
    book_id: str = Field(..., min_length=1, description="Staff-assigned code, e.g. '1556' or 'P247'")
    title: str = Field(..., min_length=1)
    category: Optional[str] = Field(
        None, description="e.g. 'Polity', 'Arithmetic', 'S&T'"
    )
    author: Optional[str] = None
    added_date: Optional[date_type] = None


class BookUpdate(RequestModel):
    """All fields optional — only supplied fields are changed."""

    title: Optional[str] = Field(None, min_length=1)
    category: Optional[str] = None
    author: Optional[str] = None
    added_date: Optional[date_type] = None


class BookResponse(BaseModel):
    book_id: str
    title: str
    category: Optional[str] = None
    author: Optional[str] = None
    added_date: Optional[date_type] = None
