"""
zkteco/reconcile.py
--------------------
Periodic full-buffer reconciliation -- the completeness backstop.

A realtime transport is best-effort: a punch can be missed server-side
(crash, ingest bug, server down). Every punch is still sitting in the
device's ATTLOG buffer, though, and this loop is what guarantees it
eventually lands in the database regardless:

  * It reads the ENTIRE device buffer on a slow cadence
    (ZK_RECONCILE_INTERVAL, default 60s) and routes every record through
    the exact same capture_and_apply() ledger the poller/live use, so
    records that the live transport already handled become
    duplicate_transport no-ops and anything it MISSED gets applied.
  * It NEVER clears the device buffer -- StudySync is a pure reader, so
    the pass is always read-only against the device and any re-read is a
    no-op thanks to the exactly-once ledger.
  * It persists per-device health into the device_state table
    (last_reconcile_at, buffer size, ledger pending counts) so operators
    can see, after a restart, that the system is fully caught up.
  * After applying, it verifies that every pyzk record it pulled has a
    durable ledger write (see verify_pyzk_vs_db) -- any mismatch is logged
    as "reconcile verify mismatch" without killing the pass. A past-day
    lone check-in is a legitimate 'pending' ledger state (its attendance
    row materializes only when its check-out punch lands), so it verifies
    as healthy rather than as an anomaly.

It runs alongside the pyzk poller (where it's a redundant safety net and
the status keeper) or the pyzk live listener (where it is the only buffer
reader).
"""

import asyncio
import json
import logging
from datetime import datetime

from attendance_punch import build_fingerprint
from database import get_connection
from zkteco.config import device_config, reconcile_interval
from zkteco.device import ZkError, device_serial
from zkteco.sync import sync_attendance_from_device

logger = logging.getLogger("zkteco.reconcile")


def _ledger_stats(db) -> dict:
    """Per-state counts over the whole device_punches ledger."""
    pending = db.execute(
        "SELECT COUNT(*) FROM device_punches WHERE state = 'pending'"
    ).fetchone()[0]
    by_state = {}
    for row in db.execute(
        "SELECT state, COUNT(*) AS n FROM device_punches GROUP BY state"
    ):
        by_state[row["state"]] = row["n"]
    total = sum(by_state.values())
    return {
        "ledger_pending": pending,
        "ledger_total": total,
        "ledger_applied": by_state.get("applied", 0),
        "ledger_duplicate_transport": by_state.get("duplicate_transport", 0),
        "ledger_duplicate_debounced": by_state.get("duplicate_debounced", 0),
        "ledger_duplicate_session": by_state.get("duplicate_session", 0),
        "ledger_unknown_student": by_state.get("unknown_student", 0),
    }


def verify_pyzk_vs_db(db, logs, serial: str) -> dict:
    """
    Verify that every pyzk record pulled from the device produced a durable
    database write.

    For each record the device reported, rebuild its exact ledger
    fingerprint (same inputs capture_and_apply() used) and confirm the
    device_punches row exists -- a missing row means the record was fetched
    but never durably written, exactly the class of bug reconcile exists to
    catch. A row still in state 'pending' is NOT an anomaly: under the
    session completion rule that is a past-day lone check-in legitimately
    awaiting its check-out punch. Malformed records (no user_id, unparsed
    timestamp) are reported too.

    Never raises: any anomaly is returned in the report so the pass keeps
    running. Returns {"verified", "issue_count", "issues"} with issues
    capped to the first 20 for readability.
    """
    verified = 0
    issues = []
    for log in logs:
        uid = log.get("user_id")
        ts = log.get("timestamp")
        if uid is None or not isinstance(ts, datetime):
            issues.append(
                {
                    "user_id": str(uid),
                    "timestamp": str(ts),
                    "issue": "malformed device record",
                }
            )
            continue
        try:
            fingerprint = build_fingerprint(serial, uid, ts, log.get("status"))
        except Exception as e:  # pragma: no cover - defensive
            issues.append(
                {
                    "user_id": str(uid),
                    "timestamp": str(ts),
                    "issue": f"fingerprint failed: {e}",
                }
            )
            continue
        row = db.execute(
            "SELECT state FROM device_punches WHERE fingerprint = ?", (fingerprint,)
        ).fetchone()
        if row is None:
            issues.append(
                {
                    "user_id": str(uid),
                    "timestamp": str(ts),
                    "issue": "no ledger row written",
                }
            )
        else:
            # Any existing row -- including 'pending', a past-day lone
            # check-in legitimately awaiting its check-out punch -- counts
            # as a durable write.
            verified += 1
    return {"verified": verified, "issue_count": len(issues), "issues": issues[:20]}


def _update_device_state(db, config, tally: dict) -> None:
    """Persist durable sync health for the device (survives restarts)."""
    serial = device_serial(config)
    stats = _ledger_stats(db)
    now = datetime.utcnow().isoformat()
    db.execute(
        """
        INSERT INTO device_state
            (device_serial, last_seen_at, last_reconcile_at, last_buffer_count,
             ledger_pending, last_result)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(device_serial) DO UPDATE SET
            last_seen_at = excluded.last_seen_at,
            last_reconcile_at = excluded.last_reconcile_at,
            last_buffer_count = excluded.last_buffer_count,
            ledger_pending = excluded.ledger_pending,
            last_result = excluded.last_result
        """,
        (
            serial,
            now,
            now,
            tally["pulled"],
            stats["ledger_pending"],
            json.dumps(tally),
        ),
    )
    db.commit()


def reconcile_once() -> dict:
    """
    One reconciliation pass over the full device buffer: read everything,
    apply anything not yet in the ledger/database, and persist device
    health. The device buffer is NEVER cleared -- StudySync is a pure
    reader, so a re-read is a no-op thanks to the exactly-once ledger.
    Returns the run tally.

    After the apply pass the run also verifies each pyzk record it pulled
    maps to a durable ledger write (see verify_pyzk_vs_db), reporting any
    mismatch as a "reconcile verify" warning instead of aborting the pass.
    The verify results are folded into the tally returned and persisted in
    device_state.
    """
    config = device_config()
    if config is None:
        return {}
    db = get_connection()
    try:
        tally, logs = sync_attendance_from_device(
            db, config, source="reconcile", return_logs=True
        )
        serial = device_serial(config)
        verify = verify_pyzk_vs_db(db, logs, serial)
        tally["verify_verified"] = verify["verified"]
        tally["verify_issue_count"] = verify["issue_count"]
        _update_device_state(db, config, tally)
        if verify["issue_count"]:
            logger.warning(
                "Reconcile verify mismatch: %s of %s pyzk records have no matching "
                "DB write. First issues: %s",
                verify["issue_count"],
                tally["pulled"],
                verify["issues"],
            )
        logger.info(
            "ZKTeco reconcile: pulled=%s imported=%s dup_transport=%s "
            "dup_debounced=%s unknown=%s verified=%s",
            tally["pulled"],
            tally["imported"],
            tally["duplicate_transport"],
            tally["duplicate_debounced"],
            tally["unknown_students"],
            verify["verified"],
        )
        return tally
    finally:
        db.close()


def current_sync_status() -> dict:
    """
    Durable + live view of device sync health, for the sync-report
    endpoint: per-device state with the ledger breakdown and a
    fully_synced verdict.
    """
    db = get_connection()
    try:
        stats = _ledger_stats(db)
        fully_synced = stats["ledger_pending"] == 0
        open_sessions = db.execute(
            "SELECT COUNT(*) FROM attendance WHERE check_out IS NULL"
        ).fetchone()[0]
        row = db.execute(
            "SELECT * FROM device_state ORDER BY last_reconcile_at DESC LIMIT 1"
        ).fetchone()
        last_result = {}
        if row and row["last_result"]:
            try:
                last_result = json.loads(row["last_result"])
            except (TypeError, ValueError):
                last_result = {}
        status = {
            "device_serial": row["device_serial"] if row else None,
            "last_reconcile_at": row["last_reconcile_at"] if row else None,
            "last_buffer_count": row["last_buffer_count"] if row else None,
            "ledger_pending": stats["ledger_pending"],
            "ledger_total": stats["ledger_total"],
            "ledger_applied": stats["ledger_applied"],
            "ledger_duplicate_transport": stats["ledger_duplicate_transport"],
            "ledger_duplicate_debounced": stats["ledger_duplicate_debounced"],
            "ledger_duplicate_session": stats["ledger_duplicate_session"],
            "ledger_unknown_student": stats["ledger_unknown_student"],
            "open_sessions": open_sessions,
            "last_verify_verified": last_result.get("verify_verified", 0),
            "last_verify_issue_count": last_result.get("verify_issue_count", 0),
            "fully_synced": fully_synced,
        }
        return status
    finally:
        db.close()


async def zkteco_reconcile_loop(stop_event: asyncio.Event) -> None:
    """Reconcile the full device buffer every interval until stopped."""
    if device_config() is None:
        logger.info("ZKTeco reconciliation disabled: ZK_DEVICE_IP is not set.")
        return

    interval = reconcile_interval()
    logger.info("ZKTeco reconciliation started (every %ss).", interval)
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(reconcile_once)
        except ZkError as e:
            # Device unreachable -- nothing to do but retry next cycle.
            logger.warning("ZKTeco reconcile failed (device unreachable?): %s", e)
        except Exception:  # never let one bad cycle kill the loop
            logger.exception("ZKTeco reconcile crashed; retrying next cycle.")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue
    logger.info("ZKTeco reconciliation stopped.")
