"""
routers/attendance.py
----------------------
Check-in / check-out endpoints for the attendance table.

Design decision: two explicit actions instead of a generic CRUD create.
Staff record arrival first, departure later — they don't know check_out
at check-in time. So:

  POST /api/attendance/check-in   -> creates a row, check_out left NULL
  POST /api/attendance/check-out  -> finds that row (same student/date/
                                      session, check_out still NULL) and
                                      fills it in

Both actions lean on constraints already in schema.sql rather than
re-implementing them in Python:
  - UNIQUE(student_id, date, session)   -> caught as 409 on check-in
  - CHECK(check_out > check_in)         -> caught as 400 on check-out
"""

import sqlite3
from datetime import date, datetime
from fastapi import APIRouter, Depends, HTTPException
from typing import List, Optional

from database import get_db_dependency
from models.attendance import AttendanceCheckIn, AttendanceCheckOut, AttendanceResponse

router = APIRouter(prefix="/api/attendance", tags=["Attendance"])


def _current_time_hhmm() -> str:
    """Server's current time as HH:MM, used when a request omits it."""
    return datetime.now().strftime("%H:%M")


def _ensure_student_exists(db: sqlite3.Connection, student_id: int) -> None:
    row = db.execute(
        "SELECT student_id FROM students WHERE student_id = ?", (student_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Student {student_id} not found")


def _find_open_session(
    db: sqlite3.Connection, student_id: int
) -> Optional[sqlite3.Row]:
    """
    Look for any attendance row for this student where check_out is still
    NULL — i.e. checked in but not yet checked out, on ANY date.

    Scoped to the whole student rather than just today's date on purpose:
    the sequence is Morning check-in -> Morning check-out -> Afternoon
    check-in -> Afternoon check-out, and a student should never be able to
    open a second session while an earlier one (today's or an unresolved
    one from a previous day) is still open. This is what actually enforces
    the ordering — there's no separate "is it Afternoon's turn yet" check;
    an open session simply blocks the next check-in until it's closed.
    """
    return db.execute(
        """
        SELECT * FROM attendance
        WHERE student_id = ? AND check_out IS NULL
        ORDER BY date DESC, check_in DESC
        LIMIT 1
        """,
        (student_id,),
    ).fetchone()


@router.post("/check-in", response_model=AttendanceResponse, status_code=201)
def check_in(
    payload: AttendanceCheckIn, db: sqlite3.Connection = Depends(get_db_dependency)
):
    """
    Record a student's arrival.

    404 if the student doesn't exist. 409 if this student already has an
    open session (checked in, not yet checked out) — that has to be
    closed with /check-out before a new one can start. This is what
    enforces Morning check-in -> Morning check-out -> Afternoon check-in
    -> Afternoon check-out as a strict sequence rather than something the
    client has to get right on its own. Also 409 if a row for this exact
    date + session already exists (e.g. a completed session re-run).
    """
    _ensure_student_exists(db, payload.student_id)

    open_session = _find_open_session(db, payload.student_id)
    if open_session:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Student {payload.student_id} is still checked in to the "
                f"{open_session['session']} session on {open_session['date']} "
                f"(check_in {open_session['check_in']}). Check out first "
                "before checking in again."
            ),
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
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Student {payload.student_id} already has a {payload.session} "
                f"attendance record for {record_date}"
            ),
        )

    row = db.execute(
        "SELECT * FROM attendance WHERE attendance_id = ?", (cursor.lastrowid,)
    ).fetchone()
    return dict(row)


@router.post("/check-out", response_model=AttendanceResponse)
def check_out(
    payload: AttendanceCheckOut, db: sqlite3.Connection = Depends(get_db_dependency)
):
    """
    Record a student's departure.

    Finds the open session (same student_id + date + session, check_out
    still NULL) and fills in check_out. 404 if no such open session
    exists — either the student never checked in, or already checked out.
    """
    record_date = payload.date or date.today()

    existing = db.execute(
        """
        SELECT * FROM attendance
        WHERE student_id = ? AND date = ? AND session = ? AND check_out IS NULL
        """,
        (payload.student_id, record_date, payload.session),
    ).fetchone()

    if not existing:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No open {payload.session} check-in found for student "
                f"{payload.student_id} on {record_date}"
            ),
        )

    check_out_time = payload.check_out or _current_time_hhmm()

    try:
        db.execute(
            "UPDATE attendance SET check_out = ? WHERE attendance_id = ?",
            (check_out_time, existing["attendance_id"]),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=400,
            detail=(
                f"check_out ({check_out_time}) must be later than "
                f"check_in ({existing['check_in']})"
            ),
        )

    row = db.execute(
        "SELECT * FROM attendance WHERE attendance_id = ?",
        (existing["attendance_id"],),
    ).fetchone()
    return dict(row)


@router.get("", response_model=List[AttendanceResponse])
def list_attendance(
    student_id: Optional[int] = None,
    date_: Optional[date] = None,
    session: Optional[str] = None,
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    """List attendance records, optionally filtered by student, date, session."""
    query = "SELECT * FROM attendance WHERE 1=1"
    params = []

    if student_id is not None:
        query += " AND student_id = ?"
        params.append(student_id)

    if date_ is not None:
        query += " AND date = ?"
        params.append(date_)

    if session:
        query += " AND session = ?"
        params.append(session)

    query += " ORDER BY date DESC, check_in"

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


@router.delete("/{attendance_id}", status_code=204)
def delete_attendance(
    attendance_id: int, db: sqlite3.Connection = Depends(get_db_dependency)
):
    """
    Delete an attendance record. Mainly for correcting data-entry
    mistakes (wrong session logged, duplicate row, etc.) — attendance has
    no downstream FK dependents, so this is a plain hard delete rather
    than the soft-delete pattern used for students.
    """
    existing = db.execute(
        "SELECT * FROM attendance WHERE attendance_id = ?", (attendance_id,)
    ).fetchone()
    if not existing:
        raise HTTPException(
            status_code=404, detail=f"Attendance record {attendance_id} not found"
        )

    db.execute("DELETE FROM attendance WHERE attendance_id = ?", (attendance_id,))
    return None
