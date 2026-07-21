"""
routers/attendance.py
----------------------
Attendance is modeled as two actions (check-in, check-out) rather than
generic CRUD, because that's how the front desk actually uses it:
a student's departure time isn't known at arrival time.

SESSION AUTO-DETECTION:
session is no longer chosen by staff -- it's derived from the actual
times entered:
  - At check-in: "Morning" if check_in is before 1 PM, else "Afternoon".
    This is provisional; it can be reclassified at check-out.
  - At check-out: if the stay genuinely spans the 1-2 PM lunch break
    (checked in before 1 PM AND checked out after 2 PM), it's
    reclassified to "Full Day" and the lunch hour is excluded from
    duration_minutes entirely. Otherwise the provisional session and a
    normal elapsed-time duration stand.

Because of this, checkout no longer needs `session` (or even `date`) as
input -- it finds THE one currently-open session for that student
(check_out IS NULL), relying on the schema's partial unique index that
guarantees a student can have at most one open session at a time. This
mirrors the same open-session pattern already used for digital_library.
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

# The lunch break window. Time spent here is never counted as study time,
# and a stay that spans across it entirely gets reclassified as "Full Day".
LUNCH_START = "13:00"
LUNCH_END = "14:00"


def _current_time_hhmm() -> str:
    """Server's current time as HH:MM, used when a request omits it."""
    return datetime.now().strftime("%H:%M")


def _minutes_between(start: str, end: str) -> int:
    """Whole minutes between two 'HH:MM' 24-hour strings."""
    sh, sm = (int(x) for x in start.split(":"))
    eh, em = (int(x) for x in end.split(":"))
    return (eh * 60 + em) - (sh * 60 + sm)


def _determine_provisional_session(check_in: str) -> str:
    """Session label at check-in time, based on time of day alone."""
    return "Morning" if check_in < LUNCH_START else "Afternoon"


def _compute_session_and_duration(check_in: str, check_out: str) -> tuple:
    """
    Auto-detect the final session label and compute duration in minutes,
    excluding the 1-2 PM lunch break for stays that genuinely span it.

    Three cases:
    1. Spans the whole lunch break (check_in < 13:00 and check_out > 14:00):
       "Full Day" -- duration = (check_in to 13:00) + (14:00 to check_out).
       e.g. 09:30 in, 14:55 out -> 210 + 55 = 265 minutes.
    2. Checked in before lunch but left DURING lunch (13:00 <= check_out <= 14:00):
       stays "Morning" -- duration counted only up to 13:00, since lunch
       has already started and no further study time should be credited.
    3. Everything else (entirely on one side of lunch): normal elapsed
       time, session determined purely by check_in's time of day.
    """
    if check_in < LUNCH_START and check_out > LUNCH_END:
        morning_minutes = _minutes_between(check_in, LUNCH_START)
        afternoon_minutes = _minutes_between(LUNCH_END, check_out)
        return "Full Day", morning_minutes + afternoon_minutes

    if check_in < LUNCH_START and LUNCH_START <= check_out <= LUNCH_END:
        return "Morning", _minutes_between(check_in, LUNCH_START)

    session = "Morning" if check_in < LUNCH_START else "Afternoon"
    return session, _minutes_between(check_in, check_out)


def _has_other_activity_on_date(
    db: sqlite3.Connection, student_id: int, session_date
) -> bool:
    """
    Check if student has any recorded activity on the given date from:
    - Digital library usage
    - Coaching class enrollment (on that date)
    - Other activities attendance (on that date)
    - Offline library with book (not auto-created self-study)
    
    Returns True if any of these exist, False if only attendance or nothing.
    """
    # Check digital library
    has_digital = db.execute(
        "SELECT 1 FROM digital_library_usage WHERE student_id = ? AND date = ? LIMIT 1",
        (student_id, session_date),
    ).fetchone()
    if has_digital:
        return True

    # Check coaching classes on that date
    has_coaching = db.execute(
        """SELECT 1 FROM coaching_enrollments ce
           JOIN coaching_classes cc ON ce.class_id = cc.class_id
           WHERE (ce.student_id = ? OR 
                  (ce.external_participant_id IS NOT NULL AND 
                   ce.external_participant_id IN (
                     SELECT external_participant_id FROM external_participants 
                     WHERE student_id IS NULL
                   )))
           AND cc.class_date = ? LIMIT 1""",
        (student_id, session_date),
    ).fetchone()
    if has_coaching:
        return True

    # Check other activities attendance on that date
    has_other_activities = db.execute(
        """SELECT 1 FROM other_activities_attendance oa
           JOIN other_activities o ON oa.activity_id = o.activity_id
           WHERE (oa.student_id = ? OR 
                  (oa.external_participant_id IS NOT NULL AND 
                   oa.external_participant_id IN (
                     SELECT external_participant_id FROM external_participants 
                     WHERE student_id IS NULL
                   )))
           AND o.session_date = ? LIMIT 1""",
        (student_id, session_date),
    ).fetchone()
    if has_other_activities:
        return True

    # Check offline library with actual book (not auto-created NULL entries)
    has_offline_book = db.execute(
        "SELECT 1 FROM offline_library_usage WHERE student_id = ? AND date = ? AND book_id IS NOT NULL LIMIT 1",
        (student_id, session_date),
    ).fetchone()
    if has_offline_book:
        return True

    return False


def _auto_fill_offline_if_needed(
    db: sqlite3.Connection, student_id: int, session_date
) -> None:
    """
    Called after a successful check-out. If this student has NO other
    activity recorded for this date (digital library, coaching, other activities,
    or offline book), assume by elimination that their attendance time was
    spent in the offline library with self-study and auto-log an entry
    (book_id=NULL).

    If any other activity is added later for the same date, the auto-created
    self-study record should be deleted by _cleanup_auto_filled_offline_if_needed()
    which is called when those entries are created.

    Wrapped so a failure here never blocks the check-out itself from
    succeeding -- this is a helpful side effect, not the primary action.
    """
    try:
        # Check if student has any other activity on this date
        if _has_other_activity_on_date(db, student_id, session_date):
            return

        # Check if there's already an offline entry (manual or auto-created)
        has_offline = db.execute(
            "SELECT 1 FROM offline_library_usage WHERE student_id = ? AND date = ? LIMIT 1",
            (student_id, session_date),
        ).fetchone()
        if has_offline:
            return

        # Create auto-filled self-study entry
        db.execute(
            "INSERT INTO offline_library_usage (student_id, date, book_id) VALUES (?, ?, NULL)",
            (student_id, session_date),
        )
    except sqlite3.Error:
        pass


def _cleanup_auto_filled_offline_if_needed(
    db: sqlite3.Connection, student_id: int, session_date
) -> None:
    """
    Called when a new entry is added to digital library, coaching classes,
    or other activities for a specific date. If an auto-created self-study
    record (book_id=NULL) exists for that date and there's now another activity,
    delete the auto-created entry as it's no longer valid.

    This ensures consistency: if a student has both attendance AND digital library
    on the same day, they don't also get a "self-study with own material" record.
    """
    try:
        # Find auto-created offline entries (book_id is NULL)
        auto_entries = db.execute(
            "SELECT usage_id FROM offline_library_usage WHERE student_id = ? AND date = ? AND book_id IS NULL",
            (student_id, session_date),
        ).fetchall()

        if not auto_entries:
            return

        # Check if there are now multiple activities for this date
        # (i.e., more than just the offline records)
        has_other_activity = db.execute(
            """SELECT 1 FROM (
                 SELECT 1 FROM digital_library_usage WHERE student_id = ? AND date = ?
                 UNION ALL
                 SELECT 1 FROM coaching_enrollments ce
                 JOIN coaching_classes cc ON ce.class_id = cc.class_id
                 WHERE ce.student_id = ? AND cc.class_date = ?
                 UNION ALL
                 SELECT 1 FROM other_activities_attendance oa
                 JOIN other_activities o ON oa.activity_id = o.activity_id
                 WHERE oa.student_id = ? AND o.session_date = ?
                 UNION ALL
                 SELECT 1 FROM offline_library_usage 
                 WHERE student_id = ? AND date = ? AND book_id IS NOT NULL
               ) LIMIT 1""",
            (student_id, session_date, student_id, session_date, student_id, session_date, student_id, session_date),
        ).fetchone()

        if has_other_activity:
            # Delete all auto-created self-study entries for this date
            for entry in auto_entries:
                db.execute(
                    "DELETE FROM offline_library_usage WHERE usage_id = ?",
                    (entry["usage_id"],),
                )
    except sqlite3.Error:
        pass


@router.post("/check-in", response_model=AttendanceResponse, status_code=201)
def check_in(
    payload: AttendanceCheckIn, db: sqlite3.Connection = Depends(get_db_dependency)
):
    """
    Record a student's arrival. session is auto-detected from check_in
    time (provisional -- may be reclassified to "Full Day" at check-out).
    Fails with 409 if this student already has an open session (the
    partial unique index on check_out IS NULL), matching the
    digital_library check-in convention.
    """
    student = db.execute(
        "SELECT student_id FROM students WHERE student_id = ?", (payload.student_id,)
    ).fetchone()
    if not student:
        raise HTTPException(
            status_code=404, detail=f"Student {payload.student_id} not found"
        )

    open_session = db.execute(
        "SELECT * FROM attendance WHERE student_id = ? AND check_out IS NULL",
        (payload.student_id,),
    ).fetchone()
    if open_session:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Student {payload.student_id} already has an open attendance session "
                f"from {open_session['date']} (checked in at {open_session['check_in']}). "
                "Check out first before checking in again."
            ),
        )

    record_date = payload.date or date.today()
    check_in_time = payload.check_in or _current_time_hhmm()
    session = _determine_provisional_session(check_in_time)

    try:
        cursor = db.execute(
            """
            INSERT INTO attendance (student_id, date, session, check_in)
            VALUES (?, ?, ?, ?)
            """,
            (payload.student_id, record_date, session, check_in_time),
        )
    except sqlite3.IntegrityError as e:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Student {payload.student_id} already has a {session} "
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
    Record a student's departure. Finds this student's one open session
    (check_out IS NULL) directly -- no session or date needed as input,
    since the schema guarantees at most one can be open at a time.

    session is finalized here (possibly reclassified to "Full Day") and
    duration_minutes is computed with the lunch-break exclusion applied.
    """
    existing = db.execute(
        "SELECT * FROM attendance WHERE student_id = ? AND check_out IS NULL",
        (payload.student_id,),
    ).fetchone()

    if not existing:
        raise HTTPException(
            status_code=404,
            detail=f"No open attendance session found for student {payload.student_id}. Check in first.",
        )

    check_out_time = payload.check_out or _current_time_hhmm()

    if check_out_time <= existing["check_in"]:
        raise HTTPException(
            status_code=422,
            detail="check_out time must be later than check_in time",
        )

    final_session, duration = _compute_session_and_duration(
        existing["check_in"], check_out_time
    )

    db.execute(
        "UPDATE attendance SET check_out = ?, session = ?, duration_minutes = ? WHERE attendance_id = ?",
        (check_out_time, final_session, duration, existing["attendance_id"]),
    )

    _auto_fill_offline_if_needed(db, payload.student_id, existing["date"])

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
    and/or session ("Morning" / "Afternoon" / "Full Day").
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
    If both check_in and check_out end up set after this correction,
    session and duration_minutes are recomputed with the same
    auto-detection + lunch-exclusion logic as a normal check-out.
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
    db.execute(f"UPDATE attendance SET {set_clause} WHERE attendance_id = ?", values)

    new_check_in = updates.get("check_in", existing["check_in"])
    new_check_out = updates.get("check_out", existing["check_out"])

    if new_check_in and new_check_out:
        if new_check_out <= new_check_in:
            raise HTTPException(
                status_code=422,
                detail="check_out time must be later than check_in time",
            )
        final_session, duration = _compute_session_and_duration(
            new_check_in, new_check_out
        )
        db.execute(
            "UPDATE attendance SET session = ?, duration_minutes = ? WHERE attendance_id = ?",
            (final_session, duration, attendance_id),
        )
        if existing["check_out"] is None:
            _auto_fill_offline_if_needed(db, existing["student_id"], existing["date"])
    elif new_check_in and not new_check_out:
        # Only check_in known so far -- provisional session, no duration yet.
        db.execute(
            "UPDATE attendance SET session = ?, duration_minutes = NULL WHERE attendance_id = ?",
            (_determine_provisional_session(new_check_in), attendance_id),
        )

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
