"""
routers/attendance.py
----------------------
Attendance is modeled as two actions (check-in, check-out) rather than
generic CRUD, because that's how the front desk actually uses it:
a student's departure time isn't known at arrival time.

duration_minutes is never written by this code — SQLite computes it
automatically (GENERATED ALWAYS AS ...) the moment check_in AND
check_out both exist.

date/check_in/check_out are all optional on input (see models/attendance.py)
-- when omitted, they default to today's date / the current server time,
resolved here in the router so the default reflects the moment the
request is actually handled, not whenever the model was defined.
"""

import sqlite3
from datetime import date, datetime
from fastapi import APIRouter, Depends, HTTPException
from typing import List, Optional

from database import get_db_dependency
from models.attendance import (
    AttendanceCheckIn,
    AttendanceCheckOut,
    AttendanceUpdate,
    AttendanceResponse,
)

router = APIRouter(prefix="/api/attendance", tags=["Attendance"])


def _current_time_hhmm() -> str:
    """Server's current time as HH:MM, used when a request omits it."""
    return datetime.now().strftime("%H:%M")


def _auto_fill_offline_if_needed(
    db: sqlite3.Connection, student_id: int, session_date
) -> None:
    """
    Called after a successful check-out. If this student has NO digital
    library usage recorded for this date, and NO offline library entry
    for it either, assume by elimination that their attendance time was
    spent in the offline library and auto-log a self-study entry
    (book_id=NULL) -- so staff don't have to manually log "just
    studying" every single day a student doesn't touch the digital
    library. This mirrors the same elimination logic already used for
    the dashboard's estimated offline-time metric, but at the point of
    data entry rather than just in analytics.

    Edge case, by design: if digital library usage gets logged LATER
    the same day (e.g. an afternoon digital session after a morning-only
    checkout already triggered this), the already-created offline row
    is NOT retroactively removed. Staff can delete it manually via
    DELETE /api/offline-library/{usage_id} if it turns out to be wrong.

    Wrapped so a failure here never blocks the check-out itself from
    succeeding -- this is a helpful side effect, not the primary action.
    """
    try:
        has_digital = db.execute(
            "SELECT 1 FROM digital_library_usage WHERE student_id = ? AND date = ? LIMIT 1",
            (student_id, session_date),
        ).fetchone()
        if has_digital:
            return

        has_offline = db.execute(
            "SELECT 1 FROM offline_library_usage WHERE student_id = ? AND date = ? LIMIT 1",
            (student_id, session_date),
        ).fetchone()
        if has_offline:
            return

        db.execute(
            "INSERT INTO offline_library_usage (student_id, date, book_id) VALUES (?, ?, NULL)",
            (student_id, session_date),
        )
    except sqlite3.Error:
        # Auto-fill is a convenience, not the point of this request --
        # never let it fail the check-out response.
        pass


@router.post("/check-in", response_model=AttendanceResponse, status_code=201)
def check_in(
    payload: AttendanceCheckIn, db: sqlite3.Connection = Depends(get_db_dependency)
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

    record_date = payload.date or date.today()
    check_in_time = payload.check_in or _current_time_hhmm()

    try:
        cursor = db.execute(
            """
            INSERT INTO attendance (student_id, date, session, check_in)
            VALUES (?, ?, ?, ?)
            """,
            (payload.student_id, record_date, payload.session, check_in_time),
        )
    except sqlite3.IntegrityError as e:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Student {payload.student_id} already has a {payload.session} "
                f"attendance record for {record_date}"
            ),
        ) from e

    row = db.execute(
        "SELECT * FROM attendance WHERE attendance_id = ?", (cursor.lastrowid,)
    ).fetchone()
    return dict(row)


@router.patch("/check-out", response_model=AttendanceResponse)
def check_out(
    payload: AttendanceCheckOut, db: sqlite3.Connection = Depends(get_db_dependency)
):
    """
    Record a student's departure. Finds the open (not yet checked-out)
    session for this student+date+session and fills in check_out.
    duration_minutes is then computed automatically by SQLite.
    """
    record_date = payload.date or date.today()

    existing = db.execute(
        """
        SELECT * FROM attendance
        WHERE student_id = ? AND date = ? AND session = ?
        """,
        (payload.student_id, record_date, payload.session),
    ).fetchone()

    if not existing:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No check-in found for student {payload.student_id} on "
                f"{record_date} ({payload.session}). Check in first."
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

    check_out_time = payload.check_out or _current_time_hhmm()

    try:
        db.execute(
            "UPDATE attendance SET check_out = ? WHERE attendance_id = ?",
            (check_out_time, existing["attendance_id"]),
        )
    except sqlite3.IntegrityError as e:
        # Catches the CHECK(check_out > check_in) constraint —
        # e.g. staff typed a check-out time earlier than check-in
        raise HTTPException(
            status_code=422,
            detail="check_out time must be later than check_in time",
        ) from e

    _auto_fill_offline_if_needed(db, payload.student_id, record_date)

    row = db.execute(
        "SELECT * FROM attendance WHERE attendance_id = ?", (existing["attendance_id"],)
    ).fetchone()
    return dict(row)


@router.get("", response_model=List[AttendanceResponse])
def list_attendance(
    student_id: Optional[int] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
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

    # If this correction is what FIRST set check_out (was previously
    # NULL), treat it the same as a normal check-out for auto-fill
    # purposes -- staff sometimes fix a missed checkout via PATCH
    # instead of the dedicated check-out action.
    if existing["check_out"] is None and updates.get("check_out") is not None:
        _auto_fill_offline_if_needed(db, existing["student_id"], existing["date"])

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
