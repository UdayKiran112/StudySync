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

SESSION CONFLICT RECONCILIATION
-------------------------------
The attendance schema enforces UNIQUE(student_id, date, session). When the
device re-reads days that ALSO have pre-existing/preloaded attendance
(e.g. the full re-import after a database restore), a device-derived
session promotion (Morning -> Full Day at day-end) can collide with a
pre-existing row for that student-date. That collision is detected and
reconciled BEFORE the write (see _resolve_session_conflict) instead of
raising through the poll: the pre-existing row is treated as the stale one,
PyZK device data is authoritative, and a "session conflict reconciled"
warning is logged with both rows plus every device punch recorded for the
day. Conflicts that only surface as an INSERT collision (a device punch
whose provisional session is already claimed by a closed pre-existing row)
are preserved in the ledger as duplicate_session -- never a crash and never
a second attendance row.
"""

import logging
import os
import sqlite3
import threading
from datetime import datetime
from typing import Optional

logger = logging.getLogger("studysync.attendance_punch")

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

            outcome = apply_punch(
                db, student_id, day, time_str, punch_debounce_minutes()
            )

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


def _find_conflicting_session(
    db: sqlite3.Connection,
    student_id: int,
    day: str,
    session: str,
    exclude_attendance_id: int,
):
    """
    Look for a DIFFERENT attendance row that already occupies the given
    (student_id, day, session) slot. Used before any write that would move
    another row into that slot (the session-promotion UPDATE at day-end,
    the stale-open auto-close) so a UNIQUE(student_id, date, session)
    collision is detected and reconciled instead of crashing the device
    poll.
    """
    return db.execute(
        """SELECT attendance_id, session, check_in, check_out
           FROM attendance
           WHERE student_id = ? AND date = ? AND session = ?
             AND attendance_id != ?""",
        (student_id, day, session, exclude_attendance_id),
    ).fetchone()


def _resolve_session_conflict(
    db: sqlite3.Connection,
    student_id: int,
    day: str,
    final_session: str,
    keep_attendance_id: int,
    keep_check_in: str,
    keep_check_out: str,
) -> None:
    """
    Resolve a session collision between device-derived data and a
    pre-existing/preloaded attendance row.

    Example: student 5729 already has a preloaded "Full Day" row
    (09:40 - 18:00) for a date. The device punch stream re-reads the same
    day: 09:41 opens a provisional "Morning" row, the day-end punch closes
    it, and _compute_session_and_duration() reclassifies it to "Full Day" --
    which collides with the preloaded row. Without this step the closing
    UPDATE would raise UNIQUE(student_id, date, session) and abort the whole
    poll.

    PyZK device data is authoritative on conflict: the obsolete pre-existing
    row is removed and the device-derived row (keep_attendance_id) survives
    with the device's check_in/check_out. The full picture -- both rows plus
    every device punch recorded for the student-day -- is logged as a
    "session conflict reconciled" warning. This runs inside
    capture_and_apply()'s lock + transaction, so a failure rolls back and the
    conflicting punch stays in the device_punches ledger, keeping the whole
    resolution idempotent on re-read. The normal (non-conflicting) attendance
    logic is untouched.
    """
    conflicting = _find_conflicting_session(
        db, student_id, day, final_session, keep_attendance_id
    )
    if conflicting is None:
        return

    punches = db.execute(
        """SELECT punch_time, state FROM device_punches
           WHERE student_id = ? AND punch_time LIKE ? AND punch_time != ''
           ORDER BY punch_time""",
        (student_id, day + "%"),
    ).fetchall()

    logger.warning(
        "Session conflict reconciled: student %s on %s -- the device punch "
        "stream derives session=%s (check_in=%s, check_out=%s) but an existing "
        "row already claims that session (attendance_id=%s, session=%s, "
        "check_in=%s, check_out=%s). PyZK device data is authoritative, so the "
        "obsolete pre-existing row is removed and the device-derived row is "
        "kept. Device punches recorded for the day: %s",
        student_id,
        day,
        final_session,
        keep_check_in,
        keep_check_out,
        conflicting["attendance_id"],
        conflicting["session"],
        conflicting["check_in"],
        conflicting["check_out"],
        [(p["punch_time"], p["state"]) for p in punches],
    )

    db.execute(
        "DELETE FROM attendance WHERE attendance_id = ?",
        (conflicting["attendance_id"],),
    )


def close_stale_open_session(db: sqlite3.Connection, student_id: int, day: str) -> None:
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
    conflicting = _find_conflicting_session(
        db, stale["student_id"], stale["date"], final_session, stale["attendance_id"]
    )
    if conflicting is not None:
        # The stale day already has a closed row covering this session and
        # that row carries a REAL check-out, so it is more truthful than the
        # artificial 23:59 auto-close. Drop the stale open row instead of
        # crashing on UNIQUE(student_id, date, session).
        logger.warning(
            "Session conflict reconciled (stale open): student %s on %s -- the "
            "open session (attendance_id=%s, check_in=%s) auto-closes at 23:59 "
            "into session=%s, which an existing row already claims "
            "(attendance_id=%s, session=%s, check_in=%s, check_out=%s). The "
            "closed row keeps its real check-out; the stale open row is removed.",
            stale["student_id"],
            stale["date"],
            stale["attendance_id"],
            stale["check_in"],
            final_session,
            conflicting["attendance_id"],
            conflicting["session"],
            conflicting["check_in"],
            conflicting["check_out"],
        )
        db.execute(
            "DELETE FROM attendance WHERE attendance_id = ?",
            (stale["attendance_id"],),
        )
        return
    db.execute(
        "UPDATE attendance SET check_out = ?, session = ?, duration_minutes = ? WHERE attendance_id = ?",
        ("23:59", final_session, duration, stale["attendance_id"]),
    )


def close_open_with_last_punch(
    db: sqlite3.Connection, student_id: int, day: str
) -> Optional[str]:
    """
    Backfill the check-out of an "empty" attendance row (check_out NULL) from
    the day's last device punch, when that punch is strictly LATER than the
    row's check-in.

    An empty row normally means the student's final punch of the day was
    itself the check-in (odd punch count) -- there is no out-punch to backfill
    with, so the row is left open for the 23:59 stale-close on their next
    visit. But when a later punch exists and simply never became a check-out
    (e.g. it was debounced as a double-tap, or a session conflict kept the row
    open), that punch is the truthful closing time and this is where the row
    gets it, instead of waiting for the artificial 23:59 auto-close.

    Recomputes session and duration through _compute_session_and_duration()
    (same 1-2 PM lunch-break rule as every other write) and runs the
    _resolve_session_conflict() guard first, so promoting e.g. Morning ->
    Full Day can never collide with a UNIQUE(student_id, date, session) slot.

    Returns the new check_out (HH:MM) that was written, or None when there is
    no open row, no device punch for the day, or no punch strictly after the
    check-in (nothing truthful to close with).
    """
    open_session = db.execute(
        "SELECT * FROM attendance WHERE student_id = ? AND date = ? AND check_out IS NULL",
        (student_id, day),
    ).fetchone()
    if open_session is None:
        return None

    last = db.execute(
        """SELECT MAX(punch_time) AS last
           FROM device_punches
           WHERE student_id = ? AND punch_time LIKE ?""",
        (student_id, day + "%"),
    ).fetchone()["last"]
    if not last:
        return None

    last_hm = last[11:16]
    if last_hm <= open_session["check_in"]:
        return None

    final_session, duration = _compute_session_and_duration(
        open_session["check_in"], last_hm
    )
    _resolve_session_conflict(
        db,
        student_id,
        day,
        final_session,
        open_session["attendance_id"],
        open_session["check_in"],
        last_hm,
    )
    db.execute(
        """
        UPDATE attendance
        SET check_out = ?, session = ?, duration_minutes = ?
        WHERE attendance_id = ?
        """,
        (last_hm, final_session, duration, open_session["attendance_id"]),
    )
    return last_hm


def backfill_empty_sessions(db: sqlite3.Connection) -> int:
    """
    Close every "empty" attendance row that has a later device punch to
    backfill with. Runs at the end of a reconcile pass so no open row is
    left behind when the data to close it exists. Runs under the punch
    lock so it can't race a live transport, and commits its own
    transaction. Returns how many rows were closed.
    """
    open_rows = db.execute(
        "SELECT DISTINCT student_id, date FROM attendance WHERE check_out IS NULL"
    ).fetchall()
    closed = 0
    if not open_rows:
        return 0
    with _punch_lock:
        for row in open_rows:
            if close_open_with_last_punch(db, row["student_id"], row["date"]) is not None:
                closed += 1
        db.commit()
    return closed


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
        # Covered-by-span guard: if a pre-existing/preloaded row for this day
        # ALREADY spans the punch time (e.g. a preloaded "Full Day" row
        # 09:40 - 18:00 and the device re-reads a 09:41 punch), the punch adds
        # no new presence -- opening a second session for it would both double
        # the day and (once a closing punch promotes it to "Full Day") collide
        # with the preloaded row. Treat it as a duplicate instead.
        spanned = db.execute(
            """SELECT 1 FROM attendance
               WHERE student_id = ? AND date = ?
                 AND check_in <= ? AND check_out >= ? LIMIT 1""",
            (student_id, day, punch, punch),
        ).fetchone()
        if spanned:
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
            logger.warning(
                "Session conflict: student %s on %s -- the device punch %s maps "
                "to provisional session %s, which an existing row already "
                "claims. No duplicate row is created; the punch is preserved in "
                "the device_punches ledger as duplicate_session for audit.",
                student_id,
                day,
                punch,
                session,
            )
            return "duplicate"

    if punch > open_session["check_in"]:
        final_session, duration = _compute_session_and_duration(
            open_session["check_in"], punch
        )
        _resolve_session_conflict(
            db,
            student_id,
            day,
            final_session,
            open_session["attendance_id"],
            open_session["check_in"],
            punch,
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
