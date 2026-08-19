"""
zkteco/poller.py
-----------------
Background task that keeps StudySync in sync with the ZKTeco device.

While the FastAPI app is alive and a device is configured (ZK_DEVICE_IP),
a poller task wakes up every ``ZK_POLL_INTERVAL`` seconds, opens a fresh
connection, reads the attendance buffer, and applies each swipe under the
hybrid model (see zkteco/sync.py): TODAY's first punch opens an attendance
row immediately, its next punch closes it; a PAST day only materializes a
row when its check-out punch lands (a lone past-day check-in stays
'pending' in the device_punches ledger). No manual "Sync" click is required.

Design notes:

  * The sync captures each log the moment it is read and never clears the
    device buffer -- StudySync is a pure reader, so the device keeps its
    own log and the exactly-once ledger turns any re-read into a no-op.
    The buffer can keep accumulating on the device; it is never touched.
  * Inserts are idempotent and a re-read can only close an open today-row
    with a strictly later punch time, so a crash mid-poll never corrupts
    data or duplicates rows.
  * Device failures (unreachable, mid-reboot) are logged and swallowed --
    the poll just retries next cycle. The manual sync endpoint still
    surfaces real errors to the user.
  * Cycles run strictly one at a time (await before the sleep), so a slow
    poll never overlaps the next one.
"""

import asyncio
import logging
import time

from database import get_connection
from attendance_punch import ledger_retention_days, prune_old_ledger_rows
from zkteco.config import effective_device_config, invalidate_effective_device_config, poll_interval
from zkteco.device import ZkError
from zkteco.discovery import discovered_device_config, scan_and_cache
from zkteco.sync import sync_attendance_from_device
from typing import Optional

logger = logging.getLogger("zkteco.poller")

# Run the ledger retention prune at most this often. A 3-second poll cycle
# runs far faster than rows age out of the retention window, so pruning on
# every cycle would just churn the database needlessly.
PRUNE_INTERVAL_SECONDS = 3600
_prune_due = time.monotonic()

# Auto-heal: after this many consecutive connect failures the poller runs a
# LAN discovery scan (throttled to SCAN_MIN_INTERVAL_SECONDS). If the device
# answers at a new IP (its DHCP lease moved it, e.g. .101 -> .100), the new
# address is persisted in runtime_config and syncing resumes on the next
# cycle -- no .env edit and no restart needed.
SCAN_AFTER_FAILURES = 3
SCAN_MIN_INTERVAL_SECONDS = 300
_scan_due = 0.0
_consecutive_failures = 0


def _buffer_min_ts(db) -> Optional[str]:
    """Oldest record currently on the device (device_state.oldest_buffer_ts)."""
    row = db.execute(
        "SELECT oldest_buffer_ts FROM device_state WHERE device_serial IS NOT NULL "
        "ORDER BY last_reconcile_at DESC LIMIT 1"
    ).fetchone()
    return row["oldest_buffer_ts"] if row and row["oldest_buffer_ts"] else None


def _prune_ledger_if_due(db) -> None:
    """
    Delete ledger rows the device can no longer re-serve (older than the
    oldest record it still holds), at most once an hour. When the buffer is
    empty/unknown the retention-window fallback applies instead.
    """
    global _prune_due
    now = time.monotonic()
    if now - _prune_due < PRUNE_INTERVAL_SECONDS:
        return
    _prune_due = now
    try:
        deleted = 0
        while True:
            batch = prune_old_ledger_rows(
                db,
                ledger_retention_days(),
                buffer_min_ts=_buffer_min_ts(db),
            )
            deleted += batch
            if batch == 0:
                break
        if deleted:
            logger.info(
                "ZKTeco poll: pruned %s stale ledger rows (device-oldest=%s, "
                "%s-day retention fallback).",
                deleted,
                _buffer_min_ts(db) or "none",
                ledger_retention_days(),
            )
    except Exception:  # noqa: BLE001 -- a prune failure must not kill the cycle
        logger.exception("ZKTeco poll: ledger prune failed; retrying next cycle.")


def _try_heal(db, last_error: Exception) -> None:
    """
    Discovery fallback after repeated connect failures: scan the LAN, and if
    the device answers (single confirmed hit) cache its current IP and retry
    once against it. Throttled so an unreachable device never turns the
    poller into a scan loop.
    """
    global _scan_due, _consecutive_failures
    now = time.monotonic()
    if now - _scan_due < SCAN_MIN_INTERVAL_SECONDS:
        return
    _scan_due = now
    try:
        report = scan_and_cache(db)
    except Exception:  # never let a broken scan kill the poll cycle
        logger.exception("ZKTeco auto-heal: discovery scan crashed.")
        return
    healed = discovered_device_config(db)
    if healed is None:
        logger.warning(
            "ZKTeco auto-heal: device still unreachable (%s); no device found "
            "on the LAN after %s failure(s).",
            last_error,
            _consecutive_failures,
        )
        return
    try:
        result = sync_attendance_from_device(db, healed)
        db.commit()
        _consecutive_failures = 0
        logger.info(
            "ZKTeco auto-heal: recovered, syncing from device at %s "
            "(pulled=%s imported=%s).",
            healed.ip,
            result["pulled"],
            result["imported"],
        )
    except ZkError as e:
        logger.warning(
            "ZKTeco auto-heal: recovery attempt against %s failed: %s",
            healed.ip,
            e,
        )


def _poll_once() -> None:
    """One synchronous sync cycle. Runs in a worker thread off the loop."""
    global _consecutive_failures
    db = get_connection()
    try:
        config = effective_device_config(db)
        if config is None:
            return
        try:
            result = sync_attendance_from_device(db, config)
            db.commit()
            _consecutive_failures = 0
            if result["imported"]:
                logger.info(
                    "ZKTeco poll: pulled=%s imported=%s duplicates=%s "
                    "unknown_students=%s",
                    result["pulled"],
                    result["imported"],
                    result["duplicates"],
                    result["unknown_students"],
                )
        except ZkError as e:
            _consecutive_failures += 1
            logger.warning(
                "ZKTeco poll failed (%s); %s consecutive failure(s).",
                e,
                _consecutive_failures,
            )
            invalidate_effective_device_config()
            if _consecutive_failures >= SCAN_AFTER_FAILURES:
                _try_heal(db, e)
    finally:
        try:
            _prune_ledger_if_due(db)
        finally:
            db.close()


async def zkteco_poller_loop(stop_event: asyncio.Event) -> None:
    """Poll the device every interval until ``stop_event`` is set."""
    if effective_device_config() is None:
        logger.info("ZKTeco polling disabled: no device configured (set ZK_DEVICE_IP or find one in Settings).")
        return

    interval = poll_interval()
    logger.info("ZKTeco polling started (every %ss).", interval)
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(_poll_once)
        except Exception:  # never let one bad cycle kill the loop
            logger.exception("ZKTeco poll crashed; retrying next cycle.")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue
    logger.info("ZKTeco polling stopped.")
