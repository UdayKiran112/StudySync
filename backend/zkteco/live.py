"""
zkteco/live.py
----------------
Real-time punch capture for the ZKTeco device using pyzk's live_capture(),
as an alternative to the periodic poller in zkteco/poller.py.

WHY THIS IS A SEPARATE MODULE
------------------------------
zkteco/poller.py wakes up every ZK_POLL_INTERVAL seconds, opens a fresh
short-lived connection, reads whatever is sitting in the device's
attendance buffer, applies it, and clears the buffer -- a "pull" model.

This module instead opens ONE persistent connection and registers for the
device's attendance-log event (pyzk's live_capture()), so the device
notifies us the instant a punch happens -- a "push" model, functionally
similar to a realtime device-push server, but reached through pyzk's
proprietary-protocol client. This is why it's kept as its own module rather
than folded into the poller: it's a second, independent transport for the
same downstream write path.

Both pull the SAME physical device buffer and (on most firmwares) a
ZKTeco device only tolerates one open session at a time, so run only ONE
of poller / live against a given device. Select with:

    ZK_ATTENDANCE_MODE=poll   (default) -> zkteco/poller.py
    ZK_ATTENDANCE_MODE=live             -> this module

main.py's lifespan reads that flag and starts the matching background
task -- see zkteco/config.py for the full list of related env vars.

HOW A PUNCH IS HANDLED
------------------------
1. pyzk's live_capture() yields a raw zk.attendance.Attendance object the
   instant the device reports a fingerprint/card/face event. It carries:
   user_id (the device's enrolled ID, e.g. "4351"), timestamp, status and
   punch (verify-method / in-out codes reported by the device firmware),
   and uid (internal device slot number).
2. That payload is caught and logged (_handle_event / _payload_from_event)
   before anything is written, so every raw punch is auditable even if a
   later step drops it.
3. We deliberately do NOT trust the device's status/punch fields to decide
   check-in vs. check-out -- different ZKTeco firmwares are inconsistent
   about how they set them. Instead we derive it exactly the way the
   front desk and the poller do: first punch of the day opens a session,
   the next closes it. That derivation is attendance_punch.apply_punch(),
   the exact same function zkteco/sync.py calls for polled punches, so a
   live-captured swipe and a polled swipe produce identical attendance
   rows -- see attendance_punch.py at the project root.
4. The event is handed to attendance_punch.capture_and_apply(), the same
   exactly-once entry point the poller uses: it claims the punch in
   the device_punches ledger by fingerprint (so a punch that the poller
   also delivered is applied only once), resolves the user_id
   against students.student_id, auto-renews a lapsed membership, and
   derives the session effect. Unmatched IDs are recorded as
   unknown_student and never written to attendance.
5. The raw event payload is preserved in the ledger (raw_record) for
   audit even when the punch turns out to be a duplicate.

RECONNECTION
-------------
live_capture() itself returns once the socket read fails for a reason
that isn't a plain timeout (device reboot, Wi-Fi hiccup, cable pull,
etc). The loop below wraps a connect/listen cycle in a reconnect-with-
backoff loop (ZK_LIVE_RECONNECT_SECONDS) so a transient disconnect only
costs a few seconds of missed real-time delivery, not a crashed
background task. The `new_timeout` passed to live_capture() doubles as
the cadence at which the stop flag is checked -- pyzk yields None on
every socket-read timeout that wasn't a real event, so the loop can never
block forever and ignore a shutdown request.

live_capture() does blocking socket I/O (pyzk predates asyncio and offers
no async variant), so the whole listen loop runs on a dedicated worker
thread via asyncio.to_thread, exactly like zkteco/poller.py's individual
poll cycles do -- the difference is the poller hands off one short call
per cycle, this hands off one long-running loop for the app's lifetime.
"""

import asyncio
import contextlib
import logging
import sqlite3
import threading
from typing import Optional

from database import get_connection
from attendance_punch import capture_and_apply
from zkteco.config import device_config, live_reconnect_seconds
from zkteco.device import build_zk

logger = logging.getLogger("zkteco.live")

# How often (seconds) the blocking socket read wakes up on its own even
# with nothing to report -- also the cadence at which shutdown is noticed.
LIVE_CAPTURE_TIMEOUT = 8

# In-memory status, refreshed as the listener runs, so the API can expose
# "is the persistent connection actually up?" without touching the device.
# Protected by a lock since it's written from the worker thread and read
# from request-handling threads/tasks.
_state_lock = threading.Lock()
_state: dict = {
    "connected": False,
    "last_event_at": None,
    "last_payload": None,
    "last_outcome": None,
    "last_error": None,
}


def _set_state(**updates) -> None:
    with _state_lock:
        _state.update(updates)


def get_live_status() -> dict:
    """Snapshot of the listener's current state, for a status endpoint."""
    with _state_lock:
        return dict(_state)


def _payload_from_event(event) -> dict:
    """Normalise a pyzk Attendance object into a plain, loggable dict."""
    return {
        "user_id": event.user_id,
        "timestamp": event.timestamp,
        "status": event.status,
        "punch": event.punch,
        "uid": event.uid,
    }


def _handle_event(event, device_serial: str) -> None:
    """
    Catch one live punch, analyse (log) its payload, and write the
    attendance row.

    Each event gets its own short-lived DB connection (mirrors
    zkteco/poller.py's per-cycle connection) so a slow or failing write
    can never hold up the live_capture socket loop, and one bad event can
    never poison the connection used by the next one.
    """
    payload = _payload_from_event(event)
    logger.info("ZKTeco live punch received: %s", payload)
    _set_state(
        last_event_at=payload["timestamp"], last_payload=payload, last_error=None
    )

    db: sqlite3.Connection = get_connection()
    try:
        # capture_and_apply resolves the PIN, auto-renews a lapsed
        # membership, dedups by fingerprint (against poller / reconcile)
        # and derives the session effect. The raw event is preserved in the
        # device_punches ledger for audit either way.
        result = capture_and_apply(
            db,
            device_serial,
            payload["user_id"],
            payload["timestamp"],
            payload["status"],
            payload["verify"],
            None,
            "pyzk_live",
        )
        logger.info(
            "ZKTeco live punch applied: student_id=%s day=%s time=%s outcome=%s",
            payload["user_id"],
            payload["timestamp"].strftime("%Y-%m-%d"),
            payload["timestamp"].strftime("%H:%M"),
            result["outcome"],
        )
        _set_state(last_outcome=result["outcome"])
    except Exception as e:
        db.rollback()
        logger.exception("Failed to apply live ZKTeco punch: %s", payload)
        _set_state(last_outcome="error", last_error=str(e))
    finally:
        db.close()


def _run_until_stopped(stop_event: threading.Event) -> None:
    """
    Blocking loop: connect, live_capture forever, reconnect on failure.
    Runs on a worker thread (see zkteco_live_loop) since live_capture()
    does blocking socket I/O pyzk never made async-friendly.
    """
    backoff = live_reconnect_seconds()

    while not stop_event.is_set():
        config = device_config()
        if config is None:
            logger.info("ZKTeco live capture disabled: ZK_DEVICE_IP is not set.")
            return

        zk = build_zk(config)
        try:
            conn = zk.connect()
        except Exception as e:
            logger.warning(
                "ZKTeco live capture: cannot connect to %s:%s (%s). Retrying in %ss.",
                config.ip,
                config.port,
                e,
                backoff,
            )
            _set_state(connected=False, last_error=str(e))
            stop_event.wait(backoff)
            continue

        logger.info(
            "ZKTeco live capture connected to %s:%s -- listening for punches.",
            config.ip,
            config.port,
        )
        _set_state(connected=True, last_error=None)
        # Stable identity for the punch ledger; falls back to the device IP
        # if the serial can't be read from the open connection.
        try:
            device_serial = conn.get_serialnumber() or config.ip
        except Exception:
            device_serial = config.ip
        try:
            for event in conn.live_capture(new_timeout=LIVE_CAPTURE_TIMEOUT):
                if stop_event.is_set():
                    conn.end_live_capture = True
                    break
                if event is None:
                    # Just a socket-read timeout / keepalive tick, not a punch.
                    continue
                try:
                    _handle_event(event, device_serial)
                except Exception:
                    # A single bad payload must never take the listener down.
                    logger.exception("Unhandled error processing a live punch event.")
        except Exception as e:
            logger.warning("ZKTeco live capture connection lost (%s). Reconnecting.", e)
        finally:
            _set_state(connected=False)
            try:
                conn.disconnect()
            except Exception:
                pass

        if not stop_event.is_set():
            stop_event.wait(backoff)

    logger.info("ZKTeco live capture stopped.")


async def zkteco_live_loop(stop_event: asyncio.Event) -> None:
    """
    Async-facing entrypoint. Mirrors zkteco.poller.zkteco_poller_loop's
    signature so main.py can start either task the same way.

    live_capture() blocks a real OS thread, not the asyncio event loop, so
    this bridges the asyncio stop_event to a threading.Event and runs the
    actual listen loop via asyncio.to_thread.
    """
    if device_config() is None:
        logger.info("ZKTeco live capture disabled: ZK_DEVICE_IP is not set.")
        return

    thread_stop = threading.Event()

    async def _forward_stop() -> None:
        await stop_event.wait()
        thread_stop.set()

    watcher = asyncio.create_task(_forward_stop())
    try:
        await asyncio.to_thread(_run_until_stopped, thread_stop)
    finally:
        thread_stop.set()
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
