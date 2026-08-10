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
    naturally from the alternating punches. A re-tap within
    ZK_PUNCH_DEBOUNCE_MINUTES (default 1) is an accidental double-tap and
    is NOT treated as a check-out or a new session.
  * Because the open state now lives in the database, the device buffer is
    cleared only AFTER every swipe in it has been durably captured in the
    device_punches ledger (see capture_and_apply in attendance_punch.py)
    and committed to the attendance table.

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
from zkteco.device import clear_attendance, device_serial, list_attendance


def drain_and_clear(db: sqlite3.Connection, config: ZkDeviceConfig, source: str) -> None:
    """
    Close the "punch landed while we were syncing" race, then clear the
    buffer.

    pyzk's clear_attendance() wipes the WHOLE buffer, so if a swipe is
    buffered between our read and our clear it would be destroyed before
    ever reaching the database. We re-read the buffer, apply anything new
    through the same ledger + apply path (silently -- it is not re-counted
    in the surfaced tally), commit, and only then clear. The residual
    sub-second window is covered by a fast poll or the live transport,
    which captures each punch the instant it happens.
    """
    extra = list_attendance(config)
    if extra:
        serial = device_serial(config)
        for log in extra:
            capture_and_apply(
                db,
                serial,
                log["user_id"],
                log["timestamp"],
                log["status"],
                "",
                None,
                source,
            )
        db.commit()
    clear_attendance(config)


def sync_attendance_from_device(
    db: sqlite3.Connection,
    config: ZkDeviceConfig,
    since: Optional[date] = None,
    source: str = "pyzk_poll",
    clear: bool = True,
) -> dict:
    """
    Pull the device buffer, capture each swipe in the device_punches ledger
    and apply it as a check-in or check-out, then (optionally) clear the
    buffer once the writes are committed.

    Returns a tally: pulled / imported / duplicates / duplicate_transport /
    duplicate_debounced / unknown_students / renewed / incomplete.
    ``incomplete`` is always 0 -- a lone punch is an open session now, not
    a dropped punch. ``renewed`` counts students whose lapsed memberships
    were auto-renewed by this run. Raises zkteco.device.ZkError if the
    device can't be reached (nothing is written in that case).

    When ``since`` is given, the buffer is read and applied but NOT
    cleared -- older (unfiltered) logs must stay on the device so they can
    be captured by a later full sync.

    When ``clear`` is False the run is read-only even in full-buffer mode:
    the device is shared with another reader that owns the drain, so
    StudySync must never wipe the ring. A punch that lands while we read
    is picked up by the next cycle instead of the mid-sync re-read, which
    is why this mode is only used alongside the realtime live transport or
    a fast poll.
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

    # In full-buffer mode (no `since`), re-read to catch anything that
    # landed mid-sync, then clear the buffer safely -- unless this device
    # is shared and clearing is disabled (clear=False).
    if since is None and clear:
        drain_and_clear(db, config, source)

    return tally
