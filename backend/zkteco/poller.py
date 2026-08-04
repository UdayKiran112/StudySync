"""
zkteco/poller.py
-----------------
Background task that keeps StudySync in sync with the ZKTeco device.

While the FastAPI app is alive and a device is configured (ZK_DEVICE_IP),
a poller task wakes up every ``ZK_POLL_INTERVAL`` seconds, opens a fresh
connection, reads the attendance buffer, and applies each swipe as a
check-in or check-out (see zkteco/sync.py). No manual "Sync" click is
required anymore -- a first punch opens an attendance row, the next punch
closes it.

Design notes:

  * The sync captures each log the moment it is read and clears the device
    buffer itself once the writes are committed, so the buffer never
    accumulates and no operator cleanup is needed.
  * Inserts are idempotent and a re-read can only close an open session
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

from database import get_connection
from zkteco.config import device_config, poll_interval
from zkteco.device import ZkError
from zkteco.sync import sync_attendance_from_device

logger = logging.getLogger("zkteco.poller")


def _poll_once() -> None:
    """One synchronous sync cycle. Runs in a worker thread off the loop."""
    config = device_config()
    if config is None:
        return
    db = get_connection()
    try:
        result = sync_attendance_from_device(db, config)
        db.commit()
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
        logger.warning("ZKTeco poll failed (device unreachable?): %s", e)
    finally:
        db.close()


async def zkteco_poller_loop(stop_event: asyncio.Event) -> None:
    """Poll the device every interval until ``stop_event`` is set."""
    if device_config() is None:
        logger.info("ZKTeco polling disabled: ZK_DEVICE_IP is not set.")
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
