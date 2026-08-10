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
  * It is what keeps the buffer safe to clear: sync_attendance_from_device
    re-reads the buffer after applying and only then clears it, so no log
    is destroyed before its ledger row is durable. On a device another
    system drains, set ZK_CLEAR_BUFFER=0 and the pass becomes read-only --
    a pure completeness check that never wipes the ring.
  * It persists per-device health into the device_state table
    (last_reconcile_at, buffer size, ledger pending counts) so operators
    can see, after a restart, that the system is fully caught up.

It runs alongside the pyzk poller (where it's a redundant safety net and
the status keeper) or the pyzk live listener (where it is the only buffer
reader).
"""

import asyncio
import json
import logging
from datetime import datetime

from database import get_connection
from zkteco.config import device_config, reconcile_interval, zk_clear_buffer
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
    apply anything not yet in the ledger/database, then (unless
    ZK_CLEAR_BUFFER=0) re-read + clear the buffer safely, and persist
    device health. Returns the run tally.
    """
    config = device_config()
    if config is None:
        return {}
    db = get_connection()
    try:
        tally = sync_attendance_from_device(
            db, config, source="reconcile", clear=zk_clear_buffer()
        )
        _update_device_state(db, config, tally)
        logger.info(
            "ZKTeco reconcile: pulled=%s imported=%s dup_transport=%s "
            "dup_debounced=%s unknown=%s",
            tally["pulled"],
            tally["imported"],
            tally["duplicate_transport"],
            tally["duplicate_debounced"],
            tally["unknown_students"],
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
        row = db.execute(
            "SELECT * FROM device_state ORDER BY last_reconcile_at DESC LIMIT 1"
        ).fetchone()
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
