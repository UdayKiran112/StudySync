"""
attendance_punch.py
--------------------
Single source of truth for turning ONE device punch (a device user_id, a
calendar day, and an HH:MM time) into an attendance table write.

This file deliberately lives at the TOP LEVEL of the project, as a
sibling to database.py and security.py, rather than inside zkteco/ or
adms/. It belongs to neither integration:

  * zkteco/sync.py   - pyzk periodic poll (pull model)
  * zkteco/live.py   - pyzk live_capture() (push model, real-time)
  * adms/ingest.py   - ZKTeco ADMS HTTP push (push model, real-time)

...all three import apply_punch()/student_id_for_user_id() from HERE, so
a swipe produces an identical attendance row no matter which transport
caught it. If it lived inside zkteco/ (as it briefly did), deleting that
package to run ADMS alone would have taken this logic down with it even
though ADMS has nothing to do with pyzk -- that's exactly the situation
this file's placement is designed to avoid. You can delete zkteco/ OR
adms/ independently and whichever one you kept will still import fine.

The rules mirror the front-desk flow in routers/attendance.py exactly:
first punch of a day opens a session (check_in set), the next punch
closes it (check_out set, session/duration recomputed with the 1-2 PM
lunch-break rule). See routers/attendance.py's module docstring for the
session/duration logic itself -- it is not duplicated here. This module
DOES depend on routers/attendance.py (for those helpers) and on
database.py's schema, but not on either zkteco or adms, so it's safe
either way.
"""

import os
import sqlite3
from typing import Optional

from routers.attendance import (
    _auto_fill_offline_if_needed,
    _compute_session_and_duration,
    _determine_provisional_session,
    _minutes_between,
)
from realtime import publish


def punch_debounce_minutes() -> int:
    """
    Minutes of anti double-tap debounce (env var ZK_PUNCH_DEBOUNCE_MINUTES,
    default 5, clamped to >= 0). Lives here rather than in zkteco/config.py
    because it parameterizes apply_punch() below directly and is read by
    all three transports (poll, pyzk live, ADMS) -- it isn't a pyzk- or
    ADMS-specific setting, it's a punch-application setting. Same env var
    name either way, so nothing changes for anyone already using it.
    """
    try:
        return max(0, int(os.getenv("ZK_PUNCH_DEBOUNCE_MINUTES", "5")))
    except ValueError:
        return 5


def student_id_for_user_id(db: sqlite3.Connection, user_id) -> Optional[int]:
    """Map a device user_id (str/int, e.g. "4351") to a students.student_id."""
    try:
        student_id = int(str(user_id).strip())
    except (TypeError, ValueError):
        return None
    row = db.execute(
        "SELECT student_id FROM students WHERE student_id = ?", (student_id,)
    ).fetchone()
    return row["student_id"] if row else None


def latest_punch_time(
    db: sqlite3.Connection, student_id: int, day: str
) -> Optional[str]:
    """
    Latest punch already recorded for this student on this day (the later
    of any row's check_in/check_out, HH:MM strings compare chronologically).
    Used by the double-tap debounce below, and works across polls, pyzk
    live events, and ADMS pushes alike since it always reads from the
    database, not from any transport's in-memory state.
    """
    row = db.execute(
        """SELECT MAX(CASE WHEN check_out IS NOT NULL THEN check_out ELSE check_in END) AS latest
           FROM attendance WHERE student_id = ? AND date = ?""",
        (student_id, day),
    ).fetchone()
    return row["latest"] if row and row["latest"] else None


def close_stale_open_session(
    db: sqlite3.Connection, student_id: int, day: str
) -> None:
    """
    If the student still has an open session from a PREVIOUS day (punched
    in and never out), close it at 23:59 of its own day. Runs just before
    a new check-in so the schema's "one open session per student" unique
    index can't block the insert.
    """
    stale = db.execute(
        "SELECT * FROM attendance WHERE student_id = ? AND date != ? AND check_out IS NULL",
        (student_id, day),
    ).fetchone()
    if stale is None:
        return
    final_session, duration = _compute_session_and_duration(stale["check_in"], "23:59")
    db.execute(
        "UPDATE attendance SET check_out = ?, session = ?, duration_minutes = ? WHERE attendance_id = ?",
        ("23:59", final_session, duration, stale["attendance_id"]),
    )


def apply_punch(
    db: sqlite3.Connection,
    student_id: int,
    day: str,
    punch: str,
    debounce_minutes: int,
) -> str:
    """
    Apply ONE HH:MM punch for a student on a given day.

    Returns one of:
      "checked_in"  - opened a new attendance row (first punch of the day)
      "checked_out" - closed the student's currently-open row
      "duplicate"   - debounced double-tap, a re-read of an already-applied
                       punch, or an out-of-order/stale punch; no write made

    Caller is responsible for resolving the device user_id to a
    student_id (student_id_for_user_id) and for auto-renewing a lapsed
    membership (routers.students.auto_renew_if_expired) before calling
    this -- both are per-student concerns, not per-punch ones.
    """
    # Double-tap guard: a punch right after the previous one (e.g. a
    # second scan a second later, or a straddling minute boundary) is
    # almost certainly accidental -- ignore it so it can't close a session
    # instantly or re-open one.
    latest = latest_punch_time(db, student_id, day)
    if latest is not None and _minutes_between(latest, punch) <= debounce_minutes:
        return "duplicate"

    open_session = db.execute(
        "SELECT * FROM attendance WHERE student_id = ? AND date = ? AND check_out IS NULL",
        (student_id, day),
    ).fetchone()

    if open_session is None:
        # Re-read guard: a log that was already applied (e.g. a poll's
        # device-buffer clear failed after a committed import, or the
        # same live/ADMS event got delivered twice) would otherwise be
        # mistaken for a fresh check-in. Skip it.
        already = db.execute(
            """SELECT 1 FROM attendance
               WHERE student_id = ? AND date = ?
                 AND (check_in = ? OR check_out = ?) LIMIT 1""",
            (student_id, day, punch, punch),
        ).fetchone()
        if already:
            return "duplicate"
        close_stale_open_session(db, student_id, day)
        try:
            session = _determine_provisional_session(punch)
            db.execute(
                """
                INSERT INTO attendance (student_id, date, session, check_in)
                VALUES (?, ?, ?, ?)
                """,
                (student_id, day, session, punch),
            )
            publish(
                "attendance",
                {
                    "student_id": student_id,
                    "day": day,
                    "punch": punch,
                    "outcome": "checked_in",
                },
            )
            return "checked_in"
        except sqlite3.IntegrityError:
            return "duplicate"

    if punch > open_session["check_in"]:
        final_session, duration = _compute_session_and_duration(
            open_session["check_in"], punch
        )
        db.execute(
            """
            UPDATE attendance
            SET check_out = ?, session = ?, duration_minutes = ?
            WHERE attendance_id = ?
            """,
            (punch, final_session, duration, open_session["attendance_id"]),
        )
        _auto_fill_offline_if_needed(db, student_id, day)
        publish(
            "attendance",
            {
                "student_id": student_id,
                "day": day,
                "punch": punch,
                "outcome": "checked_out",
            },
        )
        return "checked_out"

    return "duplicate"
