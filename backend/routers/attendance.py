"""
routers/attendance.py
----------------------
Attendance is modeled as two actions (check-in, check-out) rather than
generic CRUD, because that's how the front desk actually uses it:
a student's departure time isn't known at arrival time.

duration_minutes is never written by this code — SQLite computes it
automatically (GENERATED ALWAYS AS ...) the moment check_in AND
check_out both exist.
"""

import sqlite3
from fastapi import APIRouter, Depends, HTTPException
from typing import List, Optional
from datetime import date as date_type

from database import get_db_dependency
from models.attendance import (
    CheckInRequest,
    CheckOutRequest,
    AttendanceUpdate,
    AttendanceResponse,
)

router = APIRouter(prefix="/api/attendance", tags=["Attendance"])


@router.post("/check-in", response_model=AttendanceResponse, status_code=201)
def check_in(
    payload: CheckInRequest, db: sqlite3.Connection = Depends(get_db_dependency)
):
    """
    Record a student's arrival for a given date + session.
    Fails with 409 if this student already has a check-in for that
    exact date+session (the UNIQUE(student_id, date, session) constraint).
    """
    student = db.execute(
        "SELECT student_id FROM students WHERE student_id = ?", (payload.student_id,)
    ).fetchone()
    if not student:
        raise HTTPException(
            status_code=404, detail=f"Student {payload.student_id} not found"
        )

    try:
        cursor = db.execute(
            """
            INSERT INTO attendance (student_id, date, session, check_in)
            VALUES (?, ?, ?, ?)
            """,
            (payload.student_id, payload.date, payload.session, payload.check_in),
        )
    except sqlite3.IntegrityError as e:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Student {payload.student_id} already has a {payload.session} "
                f"attendance record for {payload.date}"
            ),
        ) from e

    row = db.execute(
        "SELECT * FROM attendance WHERE attendance_id = ?", (cursor.lastrowid,)
    ).fetchone()
    return dict(row)


@router.patch("/check-out", response_model=AttendanceResponse)
def check_out(
    payload: CheckOutRequest, db: sqlite3.Connection = Depends(get_db_dependency)
):
    """
    Record a student's departure. Finds the open (not yet checked-out)
    session for this student+date+session and fills in check_out.
    duration_minutes is then computed automatically by SQLite.
    """
    existing = db.execute(
        """
        SELECT * FROM attendance
        WHERE student_id = ? AND date = ? AND session = ?
        """,
        (payload.student_id, payload.date, payload.session),
    ).fetchone()

    if not existing:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No check-in found for student {payload.student_id} on "
                f"{payload.date} ({payload.session}). Check in first."
            ),
        )

    if existing["check_out"] is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Student {payload.student_id} was already checked out at "
                f"{existing['check_out']} for this session"
            ),
        )

    try:
        db.execute(
            "UPDATE attendance SET check_out = ? WHERE attendance_id = ?",
            (payload.check_out, existing["attendance_id"]),
        )
    except sqlite3.IntegrityError as e:
        # Catches the CHECK(check_out > check_in) constraint —
        # e.g. staff typed a check-out time earlier than check-in
        raise HTTPException(
            status_code=422,
            detail="check_out time must be later than check_in time",
        ) from e

    row = db.execute(
        "SELECT * FROM attendance WHERE attendance_id = ?", (existing["attendance_id"],)
    ).fetchone()
    return dict(row)


@router.get("", response_model=List[AttendanceResponse])
def list_attendance(
    student_id: Optional[int] = None,
    date_from: Optional[date_type] = None,
    date_to: Optional[date_type] = None,
    session: Optional[str] = None,
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    """
    List attendance records, optionally filtered by student, date range,
    and/or session. Used by both the dashboard and general reporting.
    """
    query = "SELECT * FROM attendance WHERE 1=1"
    params = []

    if student_id is not None:
        query += " AND student_id = ?"
        params.append(student_id)

    if date_from is not None:
        query += " AND date >= ?"
        params.append(date_from)

    if date_to is not None:
        query += " AND date <= ?"
        params.append(date_to)

    if session is not None:
        query += " AND session = ?"
        params.append(session)

    query += " ORDER BY date DESC, session"

    rows = db.execute(query, params).fetchall()
    return [dict(row) for row in rows]


@router.get("/{attendance_id}", response_model=AttendanceResponse)
def get_attendance(
    attendance_id: int, db: sqlite3.Connection = Depends(get_db_dependency)
):
    """Fetch a single attendance record by ID."""
    row = db.execute(
        "SELECT * FROM attendance WHERE attendance_id = ?", (attendance_id,)
    ).fetchone()
    if not row:
        raise HTTPException(
            status_code=404, detail=f"Attendance record {attendance_id} not found"
        )
    return dict(row)


@router.patch("/{attendance_id}", response_model=AttendanceResponse)
def correct_attendance(
    attendance_id: int,
    payload: AttendanceUpdate,
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    """
    Manual correction of check_in/check_out (e.g. staff typo'd a time).
    duration_minutes recalculates automatically since it's a generated
    column — no need to touch it here.
    """
    existing = db.execute(
        "SELECT * FROM attendance WHERE attendance_id = ?", (attendance_id,)
    ).fetchone()
    if not existing:
        raise HTTPException(
            status_code=404, detail=f"Attendance record {attendance_id} not found"
        )

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields provided to update")

    set_clause = ", ".join(f"{field} = ?" for field in updates.keys())
    values = list(updates.values()) + [attendance_id]

    try:
        db.execute(
            f"UPDATE attendance SET {set_clause} WHERE attendance_id = ?", values
        )
    except sqlite3.IntegrityError as e:
        raise HTTPException(
            status_code=422,
            detail="check_out time must be later than check_in time",
        ) from e

    row = db.execute(
        "SELECT * FROM attendance WHERE attendance_id = ?", (attendance_id,)
    ).fetchone()
    return dict(row)


@router.delete("/{attendance_id}", status_code=204)
def delete_attendance(
    attendance_id: int, db: sqlite3.Connection = Depends(get_db_dependency)
):
    """Delete a mistaken attendance entry entirely."""
    existing = db.execute(
        "SELECT * FROM attendance WHERE attendance_id = ?", (attendance_id,)
    ).fetchone()
    if not existing:
        raise HTTPException(
            status_code=404, detail=f"Attendance record {attendance_id} not found"
        )

    db.execute("DELETE FROM attendance WHERE attendance_id = ?", (attendance_id,))
    return None
