"""
routers/digital_library.py
----------------------------
Check-in / check-out endpoints for the digital_library_usage table.

Same open-session pattern as attendance: check-in fails with 409 if the
student already has an unresolved session (out_time IS NULL) anywhere,
on any date. Unlike attendance there's no per-session UNIQUE constraint
to fall back on, so this open-session check IS the concurrency guard —
it's not a secondary safeguard on top of a DB constraint.

Subscription validation, per your answer: for account_type =
'Library Subscription', subscription_id must both exist in
`subscriptions` AND have status = 'Active'. Checked explicitly here
(clean 404 / 400) rather than relying on the FK, which would only catch
"doesn't exist" and say nothing about "expired".
"""

import sqlite3
from datetime import date, datetime
from fastapi import APIRouter, Depends, HTTPException
from typing import List, Optional

from database import get_db_dependency
from models.digital_library import (
    DigitalLibraryCheckIn,
    DigitalLibraryCheckOut,
    DigitalLibraryResponse,
)

router = APIRouter(prefix="/api/digital-library", tags=["Digital Library"])


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
    Any digital_library_usage row for this student where out_time is
    still NULL, across all dates — mirrors attendance's open-session
    check. A student can only ever have one open session at a time.
    """
    return db.execute(
        """
        SELECT * FROM digital_library_usage
        WHERE student_id = ? AND out_time IS NULL
        ORDER BY date DESC, in_time DESC
        LIMIT 1
        """,
        (student_id,),
    ).fetchone()


def _ensure_active_subscription(db: sqlite3.Connection, subscription_id: str) -> None:
    """
    404 if the subscription doesn't exist at all, 400 if it exists but
    has expired. Called only when account_type = 'Library Subscription'.
    """
    row = db.execute(
        "SELECT status FROM subscriptions WHERE subscription_id = ?",
        (subscription_id,),
    ).fetchone()
    if not row:
        raise HTTPException(
            status_code=404, detail=f"Subscription {subscription_id} not found"
        )
    if row["status"] != "Active":
        raise HTTPException(
            status_code=400,
            detail=f"Subscription {subscription_id} is {row['status']}, not Active",
        )


@router.post("/check-in", response_model=DigitalLibraryResponse, status_code=201)
def check_in(
    payload: DigitalLibraryCheckIn,
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    """
    Start a digital library session.

    404 if the student doesn't exist. 409 if the student already has an
    open session (any date). For 'Library Subscription' accounts, 404 if
    the subscription_id doesn't exist, 400 if it's Expired.
    """
    _ensure_student_exists(db, payload.student_id)

    open_session = _find_open_session(db, payload.student_id)
    if open_session:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Student {payload.student_id} already has an open digital "
                f"library session from {open_session['date']} "
                f"(in_time {open_session['in_time']}, "
                f"platform {open_session['platform_name']}). "
                "Check out first before starting a new session."
            ),
        )

    if payload.account_type == "Library Subscription":
        _ensure_active_subscription(db, payload.subscription_id)

    record_date = payload.date or date.today()
    in_time = payload.in_time or _current_time_hhmm()

    try:
        cursor = db.execute(
            """
            INSERT INTO digital_library_usage
                (student_id, date, in_time, account_type, subscription_id,
                 platform_name, purpose, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.student_id,
                record_date,
                in_time,
                payload.account_type,
                payload.subscription_id,
                payload.platform_name,
                payload.purpose,
                payload.notes,
            ),
        )
    except sqlite3.IntegrityError as e:
        raise HTTPException(status_code=400, detail=str(e))

    row = db.execute(
        "SELECT * FROM digital_library_usage WHERE usage_id = ?",
        (cursor.lastrowid,),
    ).fetchone()
    return dict(row)


@router.post("/check-out", response_model=DigitalLibraryResponse)
def check_out(
    payload: DigitalLibraryCheckOut,
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    """
    Close out a student's open digital library session.

    404 if the student has no open session. 400 if out_time isn't later
    than in_time (schema's CHECK constraint, translated to a clean error).
    """
    open_session = _find_open_session(db, payload.student_id)
    if not open_session:
        raise HTTPException(
            status_code=404,
            detail=f"No open digital library session found for student {payload.student_id}",
        )

    out_time = payload.out_time or _current_time_hhmm()

    try:
        db.execute(
            "UPDATE digital_library_usage SET out_time = ? WHERE usage_id = ?",
            (out_time, open_session["usage_id"]),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=400,
            detail=(
                f"out_time ({out_time}) must be later than "
                f"in_time ({open_session['in_time']})"
            ),
        )

    row = db.execute(
        "SELECT * FROM digital_library_usage WHERE usage_id = ?",
        (open_session["usage_id"],),
    ).fetchone()
    return dict(row)


@router.get("", response_model=List[DigitalLibraryResponse])
def list_digital_library_usage(
    student_id: Optional[int] = None,
    date_: Optional[date] = None,
    account_type: Optional[str] = None,
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    """List digital library sessions, optionally filtered by student, date, account type."""
    query = "SELECT * FROM digital_library_usage WHERE 1=1"
    params = []

    if student_id is not None:
        query += " AND student_id = ?"
        params.append(student_id)

    if date_ is not None:
        query += " AND date = ?"
        params.append(date_)

    if account_type:
        query += " AND account_type = ?"
        params.append(account_type)

    query += " ORDER BY date DESC, in_time DESC"

    rows = db.execute(query, params).fetchall()
    return [dict(row) for row in rows]


@router.get("/{usage_id}", response_model=DigitalLibraryResponse)
def get_digital_library_usage(
    usage_id: int, db: sqlite3.Connection = Depends(get_db_dependency)
):
    """Fetch a single digital library usage record by ID."""
    row = db.execute(
        "SELECT * FROM digital_library_usage WHERE usage_id = ?", (usage_id,)
    ).fetchone()
    if not row:
        raise HTTPException(
            status_code=404, detail=f"Digital library usage record {usage_id} not found"
        )
    return dict(row)


@router.delete("/{usage_id}", status_code=204)
def delete_digital_library_usage(
    usage_id: int, db: sqlite3.Connection = Depends(get_db_dependency)
):
    """Delete a digital library usage record — data-entry corrections only."""
    existing = db.execute(
        "SELECT * FROM digital_library_usage WHERE usage_id = ?", (usage_id,)
    ).fetchone()
    if not existing:
        raise HTTPException(
            status_code=404, detail=f"Digital library usage record {usage_id} not found"
        )

    db.execute("DELETE FROM digital_library_usage WHERE usage_id = ?", (usage_id,))
    return None
