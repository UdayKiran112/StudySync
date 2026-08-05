"""
zkteco/sync.py
--------------
Turn raw ZKTeco swipe logs into StudySync attendance records.

A ZKTeco device reports one log per swipe. Each swipe is mapped to a
check-in or a check-out exactly like the front-desk flow
(routers/attendance.py):

  * First punch of the day  -> check-in: an OPEN attendance row is created
    immediately (check_in set, check_out NULL, provisional session label).
    It shows up on the attendance page within a poll cycle -- no pairing
    of swipes required.
  * Next punch for that day -> check-out: the student's open row is closed
    (check_out set, session and duration recomputed with the same 1-2 PM
    lunch-break rule as manual entry). Punch #3 reopens (afternoon),
    punch #4 closes it again, so Morning + Afternoon splits fall out
    naturally from the alternating punches.
  * Because the open state now lives in the database, the device buffer is
    cleared after EVERY successful sync -- the log is captured the moment
    it is read, so the buffer never needs to accumulate a full in/out pair.
    This is also mandatory: leaving stale logs on the device would make the
    next run re-import the same punches and create duplicate rows. The
    buffer is cleared only AFTER the database writes have been committed.

Edge cases:
  * Accidental double-tap: a punch that lands within
    ZK_PUNCH_DEBOUNCE_MINUTES (default 5) of a student's previous punch for
    the same day is ignored, so a second scan a second later can't create a
    1-minute session or a spurious re-check-in. Tune the window via the env
    var (0 disables it).
  * A student who punched in yesterday but never punched out leaves an open
    row. When they next punch in, that stale row is auto-closed at 23:59 of
    its own day so the schema's "one open session per student" rule keeps
    holding and the new check-in can be recorded.
  * Device user_ids with no matching students.student_id are reported as
    unknown_students (the log is still cleared from the device).
  * A swipe from a student whose membership has lapsed auto-renews it
    (increment renewal_count, status back to 'Active' -- same rule as the
    front-desk check-in, see routers.students.auto_renew_if_expired) and
    counts toward the returned ``renewed`` tally, instead of blocking the
    student from checking in.
  * If a run ever re-reads a log that was already applied (e.g. the device
    buffer could not be cleared after a committed import), the log matches
    an existing row's check_in or check_out and is skipped as a duplicate,
    so a re-read can never create a second row or corrupt an existing one.
"""

import sqlite3
from datetime import date
from typing import Optional

from zkteco.config import ZkDeviceConfig, punch_debounce_minutes
from zkteco.device import clear_attendance, list_attendance
from routers.attendance import (
    _auto_fill_offline_if_needed,
    _compute_session_and_duration,
    _determine_provisional_session,
    _minutes_between,
)
from routers.students import auto_renew_if_expired


def _student_id_for_user_id(db: sqlite3.Connection, user_id: str) -> Optional[int]:
    """Map a device user_id (string, e.g. "4351") to a students.student_id."""
    try:
        student_id = int(str(user_id).strip())
    except (TypeError, ValueError):
        return None
    row = db.execute(
        "SELECT student_id FROM students WHERE student_id = ?", (student_id,)
    ).fetchone()
    return row["student_id"] if row else None


def _latest_punch_time(
    db: sqlite3.Connection, student_id: int, day: str
) -> Optional[str]:
    """
    Latest punch already recorded for this student on this day (the later of
    any row's check_in/check_out, HH:MM strings compare chronologically).
    Used by the double-tap debounce, and also across poll cycles since the
    device buffer is cleared after every successful sync.
    """
    row = db.execute(
        """SELECT MAX(CASE WHEN check_out IS NOT NULL THEN check_out ELSE check_in END) AS latest
           FROM attendance WHERE student_id = ? AND date = ?""",
        (student_id, day),
    ).fetchone()
    return row["latest"] if row and row["latest"] else None


def _close_stale_open_session(
    db: sqlite3.Connection, student_id: int, day: str
) -> None:
    """
    If the student still has an open session from a PREVIOUS day (punched in
    and never out), close it at 23:59 of its own day. Runs just before a new
    check-in so the unique "one open session per student" index can't block
    the insert.
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


def sync_attendance_from_device(
    db: sqlite3.Connection,
    config: ZkDeviceConfig,
    since: Optional[date] = None,
) -> dict:
    """
    Pull the device buffer, apply each swipe as a check-in or check-out, and
    clear the buffer once the writes are committed.

    Returns a tally: pulled / imported / duplicates / unknown_students /
    renewed / incomplete. ``incomplete`` is always 0 -- a lone punch is an
    open session now, not a dropped punch. ``renewed`` counts students whose
    lapsed memberships were auto-renewed by this run. Raises
    zkteco.device.ZkError if the device can't be reached (nothing is
    written in that case).
    """
    logs = list_attendance(config)

    if since is not None:
        logs = [log for log in logs if log["timestamp"].date() >= since]

    by_day: dict = {}
    for log in logs:
        day = log["timestamp"].strftime("%Y-%m-%d")
        time_str = log["timestamp"].strftime("%H:%M")
        key = (log["user_id"], day)
        by_day.setdefault(key, []).append(time_str)

    pulled = len(logs)
    imported = 0
    duplicates = 0
    unknown_students = 0
    renewed = 0
    debounce_minutes = punch_debounce_minutes()

    for (user_id, day), punches in by_day.items():
        student_id = _student_id_for_user_id(db, user_id)
        if student_id is None:
            unknown_students += len(punches)
            continue

        # The swipe is a show-up: reactivate a lapsed membership (increment
        # renewal_count, set status back to Active) rather than skip the
        # student. Idempotent, so the rest of this day's punches cost nothing.
        if auto_renew_if_expired(db, student_id):
            renewed += 1

        for punch in sorted(punches):
            # Double-tap guard: a punch right after the previous one (e.g. a
            # second scan a second later, or a straddling minute boundary)
            # is almost certainly accidental -- ignore it so it can't close a
            # session instantly or re-open one.
            latest = _latest_punch_time(db, student_id, day)
            if latest is not None and _minutes_between(latest, punch) <= debounce_minutes:
                duplicates += 1
                continue

            open_session = db.execute(
                "SELECT * FROM attendance WHERE student_id = ? AND date = ? AND check_out IS NULL",
                (student_id, day),
            ).fetchone()

            if open_session is None:
                # Re-read guard: a log that was already applied (e.g. the
                # device clear failed after a committed import) would
                # otherwise be mistaken for a fresh check-in. Skip it.
                already = db.execute(
                    """SELECT 1 FROM attendance
                       WHERE student_id = ? AND date = ?
                         AND (check_in = ? OR check_out = ?) LIMIT 1""",
                    (student_id, day, punch, punch),
                ).fetchone()
                if already:
                    duplicates += 1
                    continue
                _close_stale_open_session(db, student_id, day)
                try:
                    session = _determine_provisional_session(punch)
                    db.execute(
                        """
                        INSERT INTO attendance (student_id, date, session, check_in)
                        VALUES (?, ?, ?, ?)
                        """,
                        (student_id, day, session, punch),
                    )
                    imported += 1
                except sqlite3.IntegrityError:
                    duplicates += 1
            elif punch > open_session["check_in"]:
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
                imported += 1
            else:
                duplicates += 1

    if pulled:
        db.commit()  # make the import durable before touching the device
        clear_attendance(config)

    return {
        "pulled": pulled,
        "imported": imported,
        "duplicates": duplicates,
        "unknown_students": unknown_students,
        "renewed": renewed,
        "incomplete": 0,
    }
