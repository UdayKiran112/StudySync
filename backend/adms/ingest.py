"""
adms/ingest.py
----------------
Turn a raw ZKTeco ADMS ATTLOG push into attendance rows.

This is the ADMS equivalent of zkteco/sync.py, and it deliberately
shares attendance_punch.py with it (and with the pyzk live-capture
module, if you're still using it) -- apply_punch() is the single source
of truth for "what does one punch do to the attendance table", so it
doesn't matter whether the punch arrived via a poll, a pyzk
live_capture() event, or an ADMS HTTP push: the resulting row is
identical. attendance_punch.py has no pyzk dependency, so pulling it in
here does NOT pull pyzk into this module or its import graph.

ATTLOG PAYLOAD FORMAT
-----------------------
The device POSTs a plain-text body to /iclock/cdata?...&table=ATTLOG,
one record per line, tab-separated:

    PIN<TAB>DateTime<TAB>Status<TAB>Verify<TAB>WorkCode<TAB>Reserved<TAB>Reserved\r\n

e.g. "1\t2024-07-28 10:41:21\t0\t1\t\t0\t0\r\n"

  PIN       the device's enrolled user ID (string) -- matched against
            students.student_id, exactly like the pyzk path's user_id.
  DateTime  "YYYY-MM-DD HH:MM:SS", the device's local clock.
  Status    device-reported check-in/out code. Same caveat as the pyzk
            path (see zkteco/sync.py's docstring): firmwares are
            inconsistent about setting this, so it is logged but NOT
            trusted to decide check-in vs. check-out. apply_punch()
            derives that from actual session state in the database
            instead (first punch of the day opens, the next closes).
  Verify    the verification method used (fingerprint/card/face/etc).
            Logged only.
  WorkCode / Reserved  not used here.

A single push can contain a backlog of many records (e.g. after the
device reconnects following a network outage), so -- exactly like
zkteco/sync.py -- records are grouped by (PIN, day) and replayed in
chronological order within each group before being handed to
apply_punch(), rather than applied in raw arrival order.
"""

import logging
import sqlite3
import threading
from datetime import datetime
from typing import List, Optional

from database import get_connection
from routers.students import auto_renew_if_expired
from attendance_punch import apply_punch, punch_debounce_minutes, student_id_for_user_id

logger = logging.getLogger("adms.ingest")

# ---------------------------------------------------------------------
# In-memory "have we heard from this device" status, purely for the
# diagnostic /api/adms/status endpoint (routers/adms.py). Not persisted
# -- restarting the app resets it, which is fine, it's a liveness view,
# not a data store. Guarded by a lock since it's written from request
# handlers (possibly concurrently) and read from a different endpoint.
# ---------------------------------------------------------------------
_state_lock = threading.Lock()
_devices: dict = {}


def _touch(sn: str, **fields) -> None:
    with _state_lock:
        entry = _devices.setdefault(sn, {})
        entry.update(fields)


def get_status() -> dict:
    """Snapshot of every device SN seen since startup, for the status endpoint."""
    with _state_lock:
        return {sn: dict(fields) for sn, fields in _devices.items()}


def note_handshake(sn: str) -> None:
    _touch(sn, last_handshake_at=datetime.utcnow())


def note_heartbeat(sn: str) -> None:
    _touch(sn, last_heartbeat_at=datetime.utcnow())


def _parse_attlog_body(raw: str) -> List[dict]:
    """
    Parse a raw ATTLOG POST body into a list of
    {"pin", "timestamp", "status", "verify", "raw"} dicts. Malformed
    lines (can't parse a PIN/timestamp out of them) are logged and
    skipped rather than aborting the whole batch -- one bad line from a
    device shouldn't cost every other punch in the same push.
    """
    records = []
    for line in raw.splitlines():
        line = line.strip("\r\n").strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            logger.warning("ADMS ATTLOG: unparseable line (too few fields): %r", line)
            continue
        pin = parts[0].strip()
        dt_str = parts[1].strip()
        try:
            timestamp = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            logger.warning(
                "ADMS ATTLOG: unparseable timestamp %r in line: %r", dt_str, line
            )
            continue
        records.append(
            {
                "pin": pin,
                "timestamp": timestamp,
                "status": parts[2].strip() if len(parts) > 2 else None,
                "verify": parts[3].strip() if len(parts) > 3 else None,
                "raw": line,
            }
        )
    return records


def ingest_attlog(sn: str, body: str) -> dict:
    """
    Parse and apply one ADMS ATTLOG push. Returns the same tally shape as
    zkteco.sync.sync_attendance_from_device (pulled/imported/duplicates/
    unknown_students/renewed) so the two are directly comparable, and
    records the result against the device's status entry.
    """
    records = _parse_attlog_body(body)
    for rec in records:
        # Catch and log the payload BEFORE any matching/writing happens,
        # so every raw punch is auditable even if a later step drops it
        # (unknown PIN, debounce, etc.) -- this is the "catch and analyse
        # the payload" step.
        logger.info("ADMS ATTLOG payload from SN=%s: %s", sn, rec["raw"])

    by_day: dict = {}
    for rec in records:
        day = rec["timestamp"].strftime("%Y-%m-%d")
        time_str = rec["timestamp"].strftime("%H:%M")
        by_day.setdefault((rec["pin"], day), []).append(time_str)

    pulled = len(records)
    imported = 0
    duplicates = 0
    unknown_students = 0
    renewed = 0
    debounce_minutes = punch_debounce_minutes()

    db: sqlite3.Connection = get_connection()
    try:
        for (pin, day), punches in by_day.items():
            student_id = student_id_for_user_id(db, pin)
            if student_id is None:
                unknown_students += len(punches)
                logger.warning(
                    "ADMS punch(es) from unknown device PIN=%s (SN=%s) -- no "
                    "matching student, ignored.",
                    pin,
                    sn,
                )
                continue

            # Same show-up-reactivates-membership rule as the front desk
            # and the pyzk paths (routers.students.auto_renew_if_expired).
            if auto_renew_if_expired(db, student_id):
                renewed += 1

            for punch in sorted(punches):
                outcome = apply_punch(db, student_id, day, punch, debounce_minutes)
                if outcome == "duplicate":
                    duplicates += 1
                else:
                    imported += 1
                logger.info(
                    "ADMS punch applied: student_id=%s day=%s time=%s outcome=%s",
                    student_id,
                    day,
                    punch,
                    outcome,
                )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("ADMS ATTLOG ingest failed for SN=%s", sn)
        raise
    finally:
        db.close()

    result = {
        "pulled": pulled,
        "imported": imported,
        "duplicates": duplicates,
        "unknown_students": unknown_students,
        "renewed": renewed,
    }
    _touch(sn, last_push_at=datetime.utcnow(), last_result=result)
    return result
