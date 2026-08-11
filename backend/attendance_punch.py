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

SESSION COMPLETION RULE (why some swipes never touch attendance)
----------------------------------------------------------------
The attendance table stores ONLY sessions that are "real". A PAST day
with two punches (in then out) becomes one row with both check_in and
check_out set. A lone check-in on a PAST day -- a student who swiped in
but never swiped out -- is NOT materialized into attendance: it stays in
the device_punches ledger in state 'pending' as an open check-in, waiting
for its check-out punch. When the check-out arrives, the pair is
materialized as one attendance row (session/duration recomputed with the
same 1-2 PM lunch-break rule as the front desk) and BOTH ledger punches
become 'applied'. A past day with an odd punch count therefore contributes
no attendance row at all -- no open rows and no artificial 23:59
stale-close of device history.

TODAY is the one deliberate exception: a swipe happening NOW is live
presence. A student who checks in today and has not yet checked out still
gets an attendance row immediately (check_in set, check_out NULL), exactly
like the front-desk manual check-in, so they show as present. Their
check-out closes that row when it lands; if it never lands, the row is
closed at 23:59 of its own day before their next check-in.

Session/duration logic itself is not duplicated here -- see
routers/attendance.py's module docstring. The manual front-desk
check-in/check-out flow is unchanged: staff click "check in" and
"check out" as two deliberate actions.

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

MEMBERSHIP RENEWAL
------------------
A swipe only reactivates a lapsed membership when it is happening NOW --
a same-day show-up, the same rule as the front desk. Historical records
being re-imported from a device buffer NEVER renew anyone (a student who
attended 2024-25 but nothing after is not renewed in 2026 just because
their old records were re-read). Renewal adds exactly ONE year, once -- it
does not loop over every lapsed year. See
routers.students.auto_renew_if_expired.

SESSION CONFLICT RECONCILIATION
-------------------------------
The attendance schema enforces UNIQUE(student_id, date, session). When the
device re-reads days that ALSO have pre-existing/preloaded attendance
(e.g. the full re-import after a database restore), a device-derived
session promotion (Morning -> Full Day at day-end) can collide with a
pre-existing row for that student-date. That collision is detected and
reconciled BEFORE the write (see _resolve_session_conflict /
_drop_conflicting_session) instead of raising through the poll: the
pre-existing row is treated as the stale one, PyZK device data is
authoritative, and a "session conflict reconciled" warning is logged with
both rows plus every device punch recorded for the day. Conflicts that
only surface as an INSERT collision (a device punch whose provisional
session is already claimed by a closed pre-existing row) are preserved in
the ledger as duplicate_session -- never a crash and never a second
attendance row.
"""

import logging
import os
import sqlite3
import threading
from datetime import date, datetime, timedelta
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
    3. Auto-renew a lapsed membership, but ONLY for a same-day show-up and
       only one year at a time (historical re-imports never renew).
    4. apply_punch() derives the session effect -- for TODAY it opens an
       attendance row immediately (check_in set, closed by the next punch);
       for PAST days a lone check-in stays 'pending' in the ledger and only
       becomes an attendance row when its check-out punch lands. Re-taps
       inside the debounce window become duplicate_debounced and can never
       close a session.
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
                # the same physical punch. Apply nothing. The source stays
                # the FIRST sighting: appending one tag per re-sight would
                # grow a row to tens of KB on a punch that stays visible in
                # the (never-cleared) device buffer -- the poll re-reads
                # every visible punch every few seconds.
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

            # A show-up reactivates a lapsed membership -- but only a real
            # one happening NOW. Historical records being re-imported from a
            # device buffer never renew anyone (see the module docstring and
            # routers.students.auto_renew_if_expired). Idempotent.
            renewed = False
            if punch_dt.date() == date.today():
                renewed = auto_renew_if_expired(db, student_id)

            outcome = apply_punch(
                db, student_id, day, time_str, punch_debounce_minutes()
            )

            if outcome == "checked_in":
                # Opens (today) or registers (past day) an open check-in that
                # is awaiting its check-out punch -- never a complete row yet.
                state = "pending"
                db.execute(
                    "UPDATE device_punches SET state = ?, student_id = ? "
                    "WHERE punch_id = ?",
                    (state, student_id, punch_id),
                )
            else:
                if outcome == "checked_out":
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


def ledger_retention_days() -> int:
    """
    Days of ledger history the retention pruner keeps (env var
    ZK_LEDGER_RETENTION_DAYS, default 90, clamped to >= 1). Rows older than
    this that are no longer needed for dedup/debounce are deleted by
    prune_old_ledger_rows() (called from the poller and from any manual
    housekeeping). The debounce/dedup windows are minutes-to-a-few-days
    deep, so a 90-day default keeps a generous audit trail while bounding
    the ledger's footprint. Lives here rather than zkteco/config.py for the
    same reason as punch_debounce_minutes: it is a punch-application
    setting shared by every transport.
    """
    try:
        return max(1, int(os.getenv("ZK_LEDGER_RETENTION_DAYS", "90")))
    except ValueError:
        return 90


def prune_old_ledger_rows(
    db: sqlite3.Connection,
    retention_days: int = None,
    batch_size: int = 2000,
) -> int:
    """
    Delete ledger rows that are no longer needed and past the retention
    window. Returns the number of rows deleted, capped at ``batch_size``;
    call repeatedly (until it returns 0) to drain a deep backlog.

    Exactly-once dedup and the double-tap debounce only need ledger rows for
    punches the device could still re-serve, plus any still-'pending' open
    check-in awaiting its check-out. Once a row is 'applied' or
    'duplicate_*' and its punch is far in the past it is pure audit: the
    attendance table already holds the real sessions and nightly backups
    hold the raw record. Keeping it forever is what let the ledger grow to
    ~100k rows / ~18 MB in production.

    Never deletes:
      * state = 'pending'  -- an open check-in still awaiting its check-out.
      * today's punches    -- the debounce reads same-day rows to swallow
                              accidental double-taps.
    """
    if retention_days is None:
        retention_days = ledger_retention_days()
    cutoff = (
        date.today() - timedelta(days=retention_days)
    ).strftime("%Y-%m-%d") + " 00:00:00"
    today = date.today().isoformat()
    cursor = db.execute(
        """
        DELETE FROM device_punches
         WHERE punch_id IN (
               SELECT punch_id FROM device_punches
                WHERE punch_time < ?
                  AND state != 'pending'
                  AND substr(punch_time, 1, 10) != ?
                LIMIT ?
         )
        """,
        (cutoff, today, batch_size),
    )
    db.commit()
    return cursor.rowcount


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


def _student_name(db: sqlite3.Connection, student_id: int) -> Optional[str]:
    """The student's display name, for the live punch notification payload."""
    row = db.execute(
        "SELECT name FROM students WHERE student_id = ?", (student_id,)
    ).fetchone()
    return row["name"] if row else None


def latest_punch_time(
    db: sqlite3.Connection, student_id: int, day: str
) -> Optional[str]:
    """
    Latest physical punch already captured in the ledger for this student on
    this day (the later of any punch's full timestamp, HH:MM string). Used
    by the double-tap debounce below. Reads the device_punches ledger rather
    than the attendance table so a just-registered open check-in (which has
    no attendance row yet on a past day) still counts as the reference time
    for the next punch.
    """
    row = db.execute(
        """SELECT MAX(punch_time) AS latest
           FROM device_punches WHERE student_id = ? AND punch_time LIKE ?""",
        (student_id, day + "%"),
    ).fetchone()
    latest = row["latest"] if row and row["latest"] else None
    return latest[11:16] if latest else None


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


def _drop_conflicting_session(
    db: sqlite3.Connection,
    student_id: int,
    day: str,
    final_session: str,
    check_in: str,
    check_out: str,
) -> None:
    """
    Device-derived INSERT variant of _resolve_session_conflict: when a
    materialized (check_in, check_out) pair computes to a session slot that a
    pre-existing/preloaded attendance row already claims, the pre-existing
    row is the stale one -- PyZK device data is authoritative, so it is
    removed and the device-derived row is inserted. Logs the same "session
    conflict reconciled" warning as the UPDATE-path resolver.
    """
    conflicting = _find_conflicting_session(
        db, student_id, day, final_session, exclude_attendance_id=-1
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
        check_in,
        check_out,
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


def _apply_today(
    db: sqlite3.Connection, student_id: int, day: str, punch: str
) -> str:
    """
    Today's swipe is LIVE presence: the first punch of the day opens an
    attendance row immediately (check_in set, check_out NULL, provisional
    session label) so the student shows as present right away -- exactly the
    front-desk manual check-in. The next punch closes it, recomputing
    session/duration with the lunch-break rule. This is the only path that
    ever leaves a check_out IS NULL row behind (today is still in progress);
    a row left open overnight is closed at 23:59 of its own day by
    close_stale_open_session() before the student's next check-in.
    """
    open_session = db.execute(
        "SELECT * FROM attendance WHERE student_id = ? AND date = ? AND check_out IS NULL",
        (student_id, day),
    ).fetchone()

    if open_session is None:
        # Re-read guard: a log that was already applied would otherwise be
        # mistaken for a fresh check-in.
        already = db.execute(
            """SELECT 1 FROM attendance
               WHERE student_id = ? AND date = ?
                 AND (check_in = ? OR check_out = ?) LIMIT 1""",
            (student_id, day, punch, punch),
        ).fetchone()
        if already:
            return "duplicate"
        # Covered-by-span guard: a pre-existing/preloaded row already spans
        # the punch time -- opening a second session for it would double the
        # day and (once a closing punch promotes it) collide with that row.
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
                    "name": _student_name(db, student_id),
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
        # The ledger check-in punch for this row is now a complete pair with
        # this check-out -- mark it applied too so the ledger stays honest.
        # The day's only 'pending' punch is exactly that open check-in, so
        # matching on state keeps a debounced re-tap from being clobbered.
        db.execute(
            """UPDATE device_punches SET state = 'applied', applied_at = ?
               WHERE student_id = ? AND punch_time LIKE ? AND state = 'pending'""",
            (datetime.utcnow().isoformat(), student_id, day + "%"),
        )
        _auto_fill_offline_if_needed(db, student_id, day)
        publish(
            "attendance",
            {
                "student_id": student_id,
                "name": _student_name(db, student_id),
                "day": day,
                "punch": punch,
                "check_in": open_session["check_in"],
                "outcome": "checked_out",
            },
        )
        return "checked_out"

    return "duplicate"


def _apply_historical(
    db: sqlite3.Connection, student_id: int, day: str, punch: str
) -> str:
    """
    A PAST day's swipe contributes an attendance row ONLY when it completes a
    session. The first punch of a past day is registered as an open check-in
    in the device_punches ledger (state 'pending') and nothing is written to
    attendance. When its check-out punch lands, the pair is materialized as
    one complete row (check_in + check_out, session/duration recomputed) and
    BOTH ledger punches become 'applied'. A past day with a lone check-in
    never produces an attendance row at all.
    """
    pending = db.execute(
        """SELECT * FROM device_punches
           WHERE student_id = ? AND state = 'pending' AND punch_time LIKE ?
           ORDER BY punch_time LIMIT 1""",
        (student_id, day + "%"),
    ).fetchone()

    if pending is None:
        # Covered-by-span guard: a pre-existing/preloaded row already covers
        # this punch time, so it is not genuine new presence.
        spanned = db.execute(
            """SELECT 1 FROM attendance
               WHERE student_id = ? AND date = ?
                 AND check_in <= ? AND check_out >= ? LIMIT 1""",
            (student_id, day, punch, punch),
        ).fetchone()
        if spanned:
            return "duplicate"
        # A live row left open on an earlier day (today's path) is stale now
        # that a later check-in is being processed -- close it at 23:59 of
        # its own day, exactly as a fresh today check-in would.
        close_stale_open_session(db, student_id, day)
        # This punch becomes the day's open check-in. The ledger row (already
        # inserted by capture_and_apply, state set to 'pending') is the open
        # session; no attendance write happens until a check-out arrives.
        return "checked_in"

    pending_hm = pending["punch_time"][11:16]
    if punch <= pending_hm:
        return "duplicate"

    final_session, duration = _compute_session_and_duration(pending_hm, punch)
    _drop_conflicting_session(db, student_id, day, final_session, pending_hm, punch)
    db.execute(
        """
        INSERT INTO attendance (student_id, date, session, check_in, check_out, duration_minutes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (student_id, day, final_session, pending_hm, punch, duration),
    )
    db.execute(
        "UPDATE device_punches SET state = 'applied', applied_at = ? WHERE punch_id = ?",
        (datetime.utcnow().isoformat(), pending["punch_id"]),
    )
    _auto_fill_offline_if_needed(db, student_id, day)
    publish(
        "attendance",
        {
            "student_id": student_id,
            "name": _student_name(db, student_id),
            "day": day,
            "punch": punch,
            "check_in": pending_hm,
            "outcome": "checked_out",
        },
    )
    return "checked_out"


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
      "checked_in"          - today: opened an attendance row; past day:
                              registered an open check-in in the ledger
      "checked_out"         - completed a session (row now has both times)
      "duplicate_debounced" - re-tap within the debounce window of the
                              student's previous punch (accidental double
                              fingerprint) -- no write, and crucially NOT a
                              check-out and NOT a new session
      "duplicate"           - a re-read of an already-applied punch, an
                              out-of-order/stale punch, or a punch already
                              covered by a pre-existing row; no write made

    TODAY's punches follow the live front-desk model (open row immediately,
    closed by the next punch). PAST days follow the session-completion rule:
    a lone check-in stays 'pending' in the ledger and only becomes an
    attendance row when its check-out punch lands -- see the module docstring.

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

    if day == date.today().isoformat():
        return _apply_today(db, student_id, day, punch)
    return _apply_historical(db, student_id, day, punch)
