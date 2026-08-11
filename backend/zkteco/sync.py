"""
zkteco/sync.py
--------------
Turn raw ZKTeco swipe logs into StudySync attendance records.

A ZKTeco device reports one log per swipe. Each swipe is mapped to a
check-in or a check-out under the hybrid model (attendance_punch.py):

  * TODAY's first punch -> check-in: an OPEN attendance row is created
    immediately (check_in set, check_out NULL, provisional session label)
    so the student shows as present right away -- no pairing of swipes
    required. The next punch for today closes that row (check_out set,
    session and duration recomputed with the same 1-2 PM lunch-break rule
    as manual entry), so Morning + Afternoon splits fall out naturally
    from the alternating punches. A re-tap within ZK_PUNCH_DEBOUNCE_MINUTES
    (default 1) is an accidental double-tap and is NOT treated as a
    check-out or a new session.
  * A PAST day's swipe only produces an attendance row when it completes a
    session: the first punch registers an open check-in in the
    device_punches ledger (state 'pending'), and the row is materialized
    only when its check-out punch lands. A lone past-day check-in never
    leaves an open attendance row.
  * StudySync is a PURE READER of the device: the buffer is never cleared
    or wiped. Every punch stays on the device; the exactly-once ledger
    (see capture_and_apply in attendance_punch.py) makes a re-read a
    no-op, so nothing is lost and nothing is double-counted.

Exactly-once: every physical punch is claimed in the device_punches ledger
by fingerprint, so a swipe that the live transport already captured is
counted as a duplicate_transport and never touches the attendance table
again.

Edge cases:
  * Accidental double-tap: a punch that lands within
    ZK_PUNCH_DEBOUNCE_MINUTES (default 1) of a student's previous punch
    for the same day is ignored, so a second scan a second later can't
    create a 1-minute session or a spurious re-check-in. It is still
    preserved in the ledger as duplicate_debounced for audit. Tune the
    window via the env var (0 disables it).
  * A student who punched in yesterday but never punched out leaves an open
    row. When they next punch in, that stale row is auto-closed at 23:59 of
    its own day so the schema's "one open session per student" rule keeps
    holding and the new check-in can be recorded.
  * Device user_ids with no matching students.student_id are recorded in
    the ledger as unknown_student and reported in the tally.
  * A swipe from a student whose membership has lapsed auto-renews it
    (increment renewal_count, status back to 'Active' -- same rule as the
    front-desk check-in, see routers.students.auto_renew_if_expired) and
    counts toward the returned ``renewed`` tally, instead of blocking the
    student from checking in.
  * If a run ever re-reads a log that was already applied, the log's
    fingerprint is already in the ledger, so it is skipped as a
    duplicate -- a re-read can never create a second row or corrupt an
    existing one.
"""

import sqlite3
from datetime import date
from typing import Optional

from attendance_punch import capture_and_apply, new_punch_tally, record_punch_tally
from zkteco.config import ZkDeviceConfig
from zkteco.device import device_serial, list_attendance


def sync_attendance_from_device(
    db: sqlite3.Connection,
    config: ZkDeviceConfig,
    since: Optional[date] = None,
    source: str = "pyzk_poll",
    return_logs: bool = False,
):
    """
    Pull the device buffer and capture each swipe in the device_punches
    ledger, applying it as a check-in or check-out.

    Read-only against the device: the attendance buffer is NEVER cleared
    (pyzk's get_attendance() only reads it). The device keeps its own log,
    so a crash, restart or re-read can never lose data -- the exactly-once
    ledger turns any re-read into a no-op.

    Returns a tally: pulled / imported / duplicates / duplicate_transport /
    duplicate_debounced / unknown_students / renewed / incomplete.
    ``incomplete`` is always 0 -- a lone punch is never dropped: today it
    is live presence (an open row), on a past day it is a 'pending' open
    check-in awaiting its check-out. ``renewed`` counts students whose
    lapsed memberships were auto-renewed by this run. Raises
    zkteco.device.ZkError if the device can't be reached (nothing is
    written in that case).

    When ``return_logs`` is True, returns ``(tally, logs)`` where ``logs``
    is the exact list of pyzk records this run pulled -- the caller (the
    reconcile pass) uses it to verify that every record from the device has
    a corresponding durable write in the database.

    When ``since`` is given, only logs on/after that date are applied; the
    rest stay in the buffer untouched for a later full sync.
    """
    logs = list_attendance(config)

    if since is not None:
        logs = [log for log in logs if log["timestamp"].date() >= since]

    tally = new_punch_tally()
    serial = device_serial(config)

    for log in logs:
        result = capture_and_apply(
            db,
            serial,
            log["user_id"],
            log["timestamp"],
            log["status"],
            "",
            None,
            source,
        )
        record_punch_tally(tally, result)
    db.commit()

    if return_logs:
        return tally, logs
    return tally
