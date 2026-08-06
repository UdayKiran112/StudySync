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

from attendance_punch import apply_punch, student_id_for_user_id
from zkteco.config import ZkDeviceConfig, punch_debounce_minutes
from zkteco.device import clear_attendance, list_attendance
from routers.students import auto_renew_if_expired

# The per-punch write itself (open/close session, debounce, dup-guard)
# lives in attendance_punch.py (project root) so both zkteco/live.py and
# adms/ingest.py can share it exactly -- see that module's docstring for
# why it's NOT inside this package. Only the "read the whole buffer,
# group by (user_id, day)" concern is specific to this poll-based path
# and stays here.


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
        student_id = student_id_for_user_id(db, user_id)
        if student_id is None:
            unknown_students += len(punches)
            continue

        # The swipe is a show-up: reactivate a lapsed membership (increment
        # renewal_count, set status back to Active) rather than skip the
        # student. Idempotent, so the rest of this day's punches cost nothing.
        if auto_renew_if_expired(db, student_id):
            renewed += 1

        for punch in sorted(punches):
            outcome = apply_punch(db, student_id, day, punch, debounce_minutes)
            if outcome == "duplicate":
                duplicates += 1
            else:
                imported += 1

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
