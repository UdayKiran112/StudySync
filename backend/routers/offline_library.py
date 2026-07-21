"""
routers/offline_library.py
-----------------------------
CRUD for the offline_library_usage table.

No check-in/check-out here — unlike attendance and digital_library,
the schema has no in_time/out_time/duration for this table, so a single
POST fully records "this student read this book on this date."

book_id is optional (per your decision): NULL means the student read
their own material, with no further detail captured about what that was.
When book_id IS provided, it must exist in the books catalog — checked
explicitly here for a clean 404, rather than surfacing a raw
sqlite3.IntegrityError from the FK constraint.
"""

import sqlite3
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional

from database import get_db_dependency
from models.offline_library import (
    OfflineLibraryCreate,
    OfflineLibraryUpdate,
    OfflineLibraryResponse,
)
from security import require_api_key

router = APIRouter(
    prefix="/api/offline-library",
    tags=["Offline Library"],
    dependencies=[Depends(require_api_key)],
)


def _ensure_student_exists(db: sqlite3.Connection, student_id: int) -> None:
    row = db.execute(
        "SELECT student_id, status FROM students WHERE student_id = ?", (student_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Student {student_id} not found")
    if row["status"] != "Active":
        raise HTTPException(status_code=400, detail=f"Student {student_id} is inactive")


def _ensure_book_exists(db: sqlite3.Connection, book_id: str) -> None:
    row = db.execute(
        "SELECT book_id FROM books WHERE book_id = ?", (book_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Book '{book_id}' not found")


@router.post("", response_model=OfflineLibraryResponse, status_code=201)
def create_offline_usage(
    payload: OfflineLibraryCreate, db: sqlite3.Connection = Depends(get_db_dependency)
):
    """
    Log an offline library reading entry.

    404 if the student doesn't exist. If book_id is provided, 404 if that
    book isn't in the catalog. book_id may be omitted entirely for
    self-material reading — no further detail is captured in that case.
    
    If this entry has a book_id (not a self-study entry) and auto-created
    self-study records exist for this date, they will be cleaned up.
    """
    _ensure_student_exists(db, payload.student_id)

    if payload.book_id is not None:
        _ensure_book_exists(db, payload.book_id)

    record_date = payload.date or date.today()

    cursor = db.execute(
        """
        INSERT INTO offline_library_usage (student_id, date, book_id)
        VALUES (?, ?, ?)
        """,
        (payload.student_id, record_date, payload.book_id),
    )

    # If this is a manual book entry (not self-study), clean up auto-created records
    if payload.book_id is not None:
        try:
            from routers.attendance import _cleanup_auto_filled_offline_if_needed
            _cleanup_auto_filled_offline_if_needed(db, payload.student_id, record_date)
        except Exception:
            pass  # Cleanup is a side effect; don't let failures block creation

    row = db.execute(
        "SELECT * FROM offline_library_usage WHERE usage_id = ?",
        (cursor.lastrowid,),
    ).fetchone()
    return dict(row)


@router.get("", response_model=List[OfflineLibraryResponse])
def list_offline_usage(
    student_id: Optional[int] = None,
    date_: Optional[date] = None,
    book_id: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    """List offline library usage records, optionally filtered by student, date, book."""
    query = "SELECT * FROM offline_library_usage WHERE 1=1"
    params = []

    if student_id is not None:
        query += " AND student_id = ?"
        params.append(student_id)

    if date_ is not None:
        query += " AND date = ?"
        params.append(date_)

    if book_id is not None:
        query += " AND book_id = ?"
        params.append(book_id)

    query += " ORDER BY date DESC, usage_id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = db.execute(query, params).fetchall()
    return [dict(row) for row in rows]


@router.get("/{usage_id}", response_model=OfflineLibraryResponse)
def get_offline_usage(
    usage_id: int, db: sqlite3.Connection = Depends(get_db_dependency)
):
    """Fetch a single offline library usage record by ID."""
    row = db.execute(
        "SELECT * FROM offline_library_usage WHERE usage_id = ?", (usage_id,)
    ).fetchone()
    if not row:
        raise HTTPException(
            status_code=404, detail=f"Offline library usage record {usage_id} not found"
        )
    return dict(row)


@router.patch("/{usage_id}", response_model=OfflineLibraryResponse)
def update_offline_usage(
    usage_id: int,
    payload: OfflineLibraryUpdate,
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    """
    Correct a mistaken entry (e.g. wrong book_id or date typed in).
    404 if the record doesn't exist, or if a newly-supplied book_id
    isn't in the catalog.
    """
    existing = db.execute(
        "SELECT * FROM offline_library_usage WHERE usage_id = ?", (usage_id,)
    ).fetchone()
    if not existing:
        raise HTTPException(
            status_code=404, detail=f"Offline library usage record {usage_id} not found"
        )

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields provided to update")
    if "date" in updates and updates["date"] is None:
        raise HTTPException(status_code=422, detail="date cannot be null")

    if "book_id" in updates and updates["book_id"] is not None:
        _ensure_book_exists(db, updates["book_id"])

    set_clause = ", ".join(f"{field} = ?" for field in updates.keys())
    values = list(updates.values()) + [usage_id]

    db.execute(
        f"UPDATE offline_library_usage SET {set_clause} WHERE usage_id = ?", values
    )

    row = db.execute(
        "SELECT * FROM offline_library_usage WHERE usage_id = ?", (usage_id,)
    ).fetchone()
    return dict(row)


@router.delete("/{usage_id}", status_code=204)
def delete_offline_usage(
    usage_id: int, db: sqlite3.Connection = Depends(get_db_dependency)
):
    """Delete an offline library usage record — data-entry corrections only."""
    existing = db.execute(
        "SELECT * FROM offline_library_usage WHERE usage_id = ?", (usage_id,)
    ).fetchone()
    if not existing:
        raise HTTPException(
            status_code=404, detail=f"Offline library usage record {usage_id} not found"
        )

    db.execute("DELETE FROM offline_library_usage WHERE usage_id = ?", (usage_id,))
    return None
