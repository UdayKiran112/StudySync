"""
adms/ingest.py
---------------
Turn a raw ZKTeco ADMS ATTLOG push into attendance rows.

This is the ADMS equivalent of zkteco/sync.py, and it deliberately
shares attendance_punch.py with it (and with the pyzk live-capture
module, if you're still using it) -- capture_and_apply() is the single
source of truth for "what does one punch do to the attendance table",
so it doesn't matter whether the punch arrived via a poll, a pyzk
live_capture() event, or an ADMS HTTP push: the resulting row is
identical. attendance_punch.py has no pyzk dependency, so pulling it in
here does NOT pull pyzk into this module or its import graph.

EXACTLY-ONCE
------------
Every punch is claimed in the device_punches ledger by fingerprint
(device_serial|PIN|full-second timestamp|status) before it is applied.
The device_serial here is the "SN" query parameter the device sends on
every request. If the same physical punch also arrived via pyzk (poll,
live or reconcile), its fingerprint is already in the ledger, so this
ADMS delivery is counted as a duplicate_transport and applied nowhere.

ATTLOG PAYLOAD FORMAT
----------------------
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
device reconnects following a network outage). Each record is captured
independently -- one malformed or failing line never costs the rest of
the batch -- and replay order is safe because the session state lives in
the database, not in the arrival order.
"""

import json
import logging
import sqlite3
import threading
from datetime import datetime
from typing import List

from database import get_connection
from attendance_punch import capture_and_apply, new_punch_tally, record_punch_tally

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
    duplicate_transport/duplicate_debounced/unknown_students/renewed) so
    the two are directly comparable, and records the result against the
    device's status entry.

    Each record is captured independently through the device_punches
    ledger, so a punch that failed to parse or apply cannot drag the rest
    of the batch down with it. Any punch that ADMS cannot deliver is still
    sitting in the device's buffer, so the pyzk poller / reconciliation
    loop picks it up -- ADMS is the fast path, never the only path.
    """
    records = _parse_attlog_body(body)
    for rec in records:
        # Catch and log the payload BEFORE any matching/writing happens,
        # so every raw punch is auditable even if a later step drops it
        # (unknown PIN, debounce, etc.) -- this is the "catch and analyse
        # the payload" step.
        logger.info("ADMS ATTLOG payload from SN=%s: %s", sn, rec["raw"])

    tally = new_punch_tally()
    tally["pulled"] = len(records)

    db: sqlite3.Connection = get_connection()
    try:
        for rec in records:
            try:
                result = capture_and_apply(
                    db,
                    sn,
                    rec["pin"],
                    rec["timestamp"],
                    rec["status"],
                    rec["verify"],
                    rec["raw"],
                    "adms",
                )
                record_punch_tally(tally, result)
                logger.info(
                    "ADMS punch applied: pin=%s day=%s time=%s outcome=%s",
                    rec["pin"],
                    rec["timestamp"].strftime("%Y-%m-%d"),
                    rec["timestamp"].strftime("%H:%M"),
                    result["outcome"],
                )
            except Exception:
                # One bad record must not abort the whole batch. The raw
                # record is still in the device buffer, so the pyzk
                # poller / reconciliation loop will capture it.
                logger.exception(
                    "ADMS ATTLOG record failed for SN=%s: %r", sn, rec["raw"]
                )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("ADMS ATTLOG ingest failed for SN=%s", sn)
        raise
    finally:
        db.close()

    _touch(sn, last_push_at=datetime.utcnow(), last_result=tally)
    return tally


def get_sync_status() -> dict:
    """
    Durable per-device sync state from the device_state table (survives
    restarts), combined with the in-memory liveness view.
    """
    db: sqlite3.Connection = get_connection()
    try:
        rows = db.execute(
            "SELECT * FROM device_state ORDER BY device_serial"
        ).fetchall()
        status = {}
        for row in rows:
            entry = {
                "device_serial": row["device_serial"],
                "last_seen_at": row["last_seen_at"],
                "last_reconcile_at": row["last_reconcile_at"],
                "last_buffer_count": row["last_buffer_count"],
                "ledger_pending": row["ledger_pending"],
            }
            if row["last_result"]:
                try:
                    entry["last_result"] = json.loads(row["last_result"])
                except ValueError:
                    entry["last_result"] = None
            status[row["device_serial"]] = entry
        with _state_lock:
            for sn, live in _devices.items():
                entry = status.setdefault(sn, {"device_serial": sn})
                entry.update(live)
        return status
    finally:
        db.close()
