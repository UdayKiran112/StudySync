"""
attendance_punch.py
-------------------
Single source of truth for turning ONE device punch (a device user_id, a
calendar day, and an HH:MM time) into an attendance table write.

This file deliberately lives at the TOP LEVEL of the project, as a
sibling to database.py and security.py, rather than inside zkteco/. It
belongs to no single integration:

  * zkteco/sync.py   - pyzk periodic poll (pull model)
  * zkteco/live.py   - pyzk live_capture() (push model, real-time)

...both import capture_and_apply()/apply_punch()/student_id_for_user_id()
from HERE, so a swipe produces an identical attendance row no matter which
transport caught it. If it lived inside zkteco/ (as it briefly did), a
change to the connection layer would have taken this logic down with it
even though punch application has nothing to do with pyzk -- that's the
situation this file's placement is designed to avoid. This module
DOES depend on routers/attendance.py (for those helpers) and on
database.py's schema, but not on zkteco, so it's safe either way.

The rules mirror the front-desk flow in routers/attendance.py exactly:
first punch of a day opens a session (check_in set), the next punch
closes it (check_out set, session/duration recomputed with the 1-2 PM
lunch-break rule). See routers/attendance.py's module docstring for the
session/duration logic itself -- it is not duplicated here.

EXACTLY-ONCE LEDGER
-------------------
Every physical punch is first captured into the device_punches ledger via
capture_and_apply(). The ledger's UNIQUE(fingerprint) -- where fingerprint
= device_serial|user_id|full-second timestamp|status -- is identical no
matter which transport delivers the punch, so a punch that both the poll
and the live stream see is claimed ONCE and applied to attendance exactly
once. The second delivery becomes a "duplicate_transport" that never
touches the attendance table. A process-wide lock (capture_and_apply
commits while holding it) makes the claim + apply atomic, so two
transports racing the same punch cannot both apply it.

A re-tap of the fingerprint within ZK_PUNCH_DEBOUNCE_MINUTES (default 1)
of the student's previous successful punch is judged an accidental
double-tap: it is recorded in the ledger as "duplicate_debounced" (so the
raw record survives for audit) but is NOT a check-out and does NOT open a
new session. The original raw device record is always preserved in the
ledger's raw_record column regardless of how the punch is classified.
"""

import os
import sqlite3
import threading
from datetime import datetime
from typing import Optional

from routers.attendance import (
    _auto_fill_offline_if_needed,
    _compute_session_and_duration,
    _determine_provisional_session,
    _minutes_between,
)
from routers.students import auto_renew_if_expired
from realtime import publish

# ---------------------------------------------------------------------
# Exactly-once capture: one writer at a time across every transport
# (pyzk poll worker, pyzk live worker, reconcile worker).
# capture_and_apply() commits WHILE holding this lock, so by the
# time a competing transport acquires it the ledger row it wanted to claim
# is already visible -- the UNIQUE(fingerprint) index then makes the loser
# a no-op duplicate_transport instead of a second apply.
# ---------------------------------------------------------------------
_punch_lock = threading.Lock()


def build_fingerprint(device_serial: str, user_id, punch_dt, status) -> str:
    """
    Stable identity of one PHYSICAL punch, identical across transports.
    (device_serial, user_id, full-second timestamp, status) is the same
    tuple whether the punch arrived from the pyzk poll or the pyzk live
    stream, so a punch delivered by both can only be claimed once in the
    device_punches ledger.
    """
    ts = punch_dt.strftime("%Y-%m-%d %H:%M:%S")
    return f"{device_serial}|{user_id}|{ts}|{status if status is not None else ''}"


def new_punch_tally() -> dict:
    """Fresh tally for one sync/push/reconcile run. Shared by all transports."""
    return {
        "pulled": 0,
        "imported": 0,
        "duplicates": 0,
        "duplicate_transport": 0,
        "duplicate_debounced": 0,
        "unknown_students": 0,
        "renewed": 0,
        "incomplete": 0,
    }


def record_punch_tally(tally: dict, result: dict) -> None:
    """Fold one capture_and_apply() result into a running tally."""
    tally["pulled"] += 1
    outcome = result["outcome"]
    if outcome in ("checked_in", "checked_out"):
        tally["imported"] += 1
    elif outcome == "duplicate_transport":
        tally["duplicate_transport"] += 1
    elif outcome == "duplicate_debounced":
        tally["duplicate_debounced"] += 1
    elif outcome == "duplicate":
        tally["duplicates"] += 1
    elif outcome == "unknown_student":
        tally["unknown_students"] += 1
    if result["renewed"]:
        tally["renewed"] += 1


def capture_and_apply(
    db: sqlite3.Connection,
    device_serial: str,
    user_id,
    punch_dt: datetime,
    status_code="",
    verify_method="",
    raw_record=None,
    source: str = "device",
) -> dict:
    """
    Exactly-once capture + application of ONE physical device punch.

    1. Claim the punch in the device_punches ledger (INSERT OR IGNORE keyed
       by fingerprint). If another transport already captured it, mark the
       duplicate and touch NOTHING in the attendance table.
    2. Resolve the device user_id to a students.student_id. Unknown PINs are
       recorded as unknown_student, never fabricated into attendance.
    3. Auto-renew a lapsed membership (same rule as the front desk).
    4. apply_punch() derives the session effect -- first punch of a day
       opens a session, the next closes it, etc. Re-taps inside the debounce
       window become duplicate_debounced and can never close a session.
    5. Commit while holding the process-wide lock, so the claim is visible
       to competing transports before this function returns.

    Returns {"outcome": <str>, "renewed": <bool>}. outcome is one of:
      checked_in / checked_out / duplicate_transport /
      duplicate_debounced / duplicate / unknown_student.
    The caller is responsible for tallying (record_punch_tally) and for any
    device-buffer housekeeping (clearing) once the DB writes are durable.
    """
    day = punch_dt.strftime("%Y-%m-%d")
    time_str = punch_dt.strftime("%H:%M")
    fingerprint = build_fingerprint(device_serial, user_id, punch_dt, status_code)
    now = datetime.utcnow().isoformat()

    with _punch_lock:
        try:
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO device_punches
                    (fingerprint, device_serial, user_id, punch_time,
                     status_code, verify_method, source, raw_record, captured_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fingerprint,
                    device_serial,
                    str(user_id).strip(),
                    punch_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    str(status_code) if status_code is not None else "",
                    str(verify_method) if verify_method is not None else "",
                    source,
                    raw_record,
                    now,
                ),
            )
            if cursor.rowcount == 0:
                # Already claimed by another transport / a device retry of
                # the same physical punch. Record the extra sighting for
                # audit, apply nothing.
                db.execute(
                    "UPDATE device_punches SET source = source || ', ' || ? "
                    "WHERE fingerprint = ?",
                    (source, fingerprint),
                )
                db.commit()
                return {"outcome": "duplicate_transport", "renewed": False}

            punch_id = cursor.lastrowid

            student_id = student_id_for_user_id(db, user_id)
            if student_id is None:
                db.execute(
                    "UPDATE device_punches SET state = 'unknown_student' "
                    "WHERE punch_id = ?",
                    (punch_id,),
                )
                db.commit()
                return {"outcome": "unknown_student", "renewed": False}

            # A show-up reactivates a lapsed membership. Idempotent.
            renewed = auto_renew_if_expired(db, student_id)

            outcome = apply_punch(db, student_id, day, time_str, punch_debounce_minutes())

            if outcome in ("checked_in", "checked_out"):
                state = "applied"
            elif outcome == "duplicate_debounced":
                state = "duplicate_debounced"
            else:
                state = "duplicate_session"
            db.execute(
                "UPDATE device_punches SET state = ?, student_id = ?, applied_at = ? "
                "WHERE punch_id = ?",
                (state, student_id, now, punch_id),
            )
            db.commit()
            return {"outcome": outcome, "renewed": renewed}
        except Exception:
            db.rollback()
            raise


def punch_debounce_minutes() -> int:
    """
    Minutes of anti double-tap debounce (env var ZK_PUNCH_DEBOUNCE_MINUTES,
    default 1, clamped to >= 0). A punch that lands this many minutes or
    fewer after a student's previous successful punch for the same day is
    treated as an accidental re-tap: it is recorded in the device_punches
    ledger as a duplicate but never becomes a check-out and never opens a
    new session. Lives here rather than in zkteco/config.py because it
    parameterizes apply_punch() below directly and is read by all the
    transports (poll, pyzk live) -- it isn't a pyzk-specific setting, it's
    a punch-application setting. Same env var name either way, so nothing
    changes for anyone already using it.
    """
    try:
        return max(0, int(os.getenv("ZK_PUNCH_DEBOUNCE_MINUTES", "1")))
    except ValueError:
        return 1


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
    Used by the double-tap debounce below, and works across polls and pyzk
    live events alike since it always reads from the database, not from any
    transport's in-memory state.
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
      "checked_in"        - opened a new attendance row (first punch of the day)
      "checked_out"       - closed the student's currently-open row
      "duplicate_debounced" - re-tap within the debounce window of the
                            student's previous successful punch (accidental
                            double fingerprint) -- no write, and crucially
                            NOT a check-out and NOT a new session
      "duplicate"         - a re-read of an already-applied punch or an
                            out-of-order/stale punch; no write made

    Caller is responsible for resolving the device user_id to a
    student_id (student_id_for_user_id) and for auto-renewing a lapsed
    membership (routers.students.auto_renew_if_expired) before calling
    this -- both are per-student concerns, not per-punch ones.
    """
    # Double-tap guard: a punch right after the previous one (e.g. a
    # second scan a second later, or a straddling minute boundary) is
    # almost certainly accidental -- ignore it so it can't close a session
    # instantly or re-open one. Only punches AT/AFTER the last one count as
    # debounce candidates; an earlier (out-of-order) punch falls through to
    # the session logic where it is safely classified as a duplicate.
    latest = latest_punch_time(db, student_id, day)
    diff = _minutes_between(latest, punch) if latest is not None else None
    if diff is not None and 0 <= diff <= debounce_minutes:
        return "duplicate_debounced"

    open_session = db.execute(
        "SELECT * FROM attendance WHERE student_id = ? AND date = ? AND check_out IS NULL",
        (student_id, day),
    ).fetchone()

    if open_session is None:
        # Re-read guard: a log that was already applied (e.g. a poll's
        # device-buffer clear failed after a committed import, or the
        # same live event got delivered twice) would otherwise be
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
