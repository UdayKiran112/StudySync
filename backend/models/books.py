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


class BookCreate(BaseModel):
    book_id: str = Field(..., description="Staff-assigned code, e.g. '1556' or 'P247'")
    title: str
    category: Optional[str] = Field(
        None, description="e.g. 'Polity', 'Arithmetic', 'S&T'"
    )
    author: Optional[str] = None
    added_date: Optional[date_type] = None


class BookUpdate(BaseModel):
    """All fields optional — only supplied fields are changed."""

    title: Optional[str] = None
    category: Optional[str] = None
    author: Optional[str] = None
    added_date: Optional[date_type] = None


class BookResponse(BaseModel):
    book_id: str
    title: str
    category: Optional[str] = None
    author: Optional[str] = None
    added_date: Optional[date_type] = None
