"""
routers/zkteco.py
--------------------
API-key-protected endpoints for the ZKTeco attendance device.

These are a hardened re-implementation of the endpoints from
najibullahjafari/zkteco_device_python_connect, fixed to fit StudySync's
security model:

  * Every endpoint requires the X-API-Key header (same require_api_key
    dependency as the other staff routers).
  * The device is configured ONCE server-side via environment variables
    (see zkteco/config.py). The original project took ip/port/comm_key from
    the client on every request, which let anyone who could reach the API
    probe arbitrary ZKTeco devices on the LAN and even delete their users or
    unlock their doors. That attack surface is gone.
  * Device failures become clean 502 responses (ZkError -> 502) instead of
    leaking pyzk/socket tracebacks.
  * `POST /api/zkteco/attendance/sync` pulls logs and writes them into the
    attendance table, pairing swipes into Morning/Afternoon/Full Day rows
    with the same session/duration logic as the manual check-in flow.

NOTE: the ZKTeco wire protocol (TCP/UDP 4370) is not encrypted and the
"communication key" is weak obfuscation. Keep the device and this server on
a trusted network segment; do not expose port 4370 to the open internet.
"""

import logging
import sqlite3
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from database import get_db_dependency
from security import require_api_key
from zkteco import device
from zkteco.config import attendance_mode, device_config
from zkteco.live import get_live_status
from zkteco.sync import sync_attendance_from_device
from models.zkteco import (
    ZkAttendanceLog,
    ZkDeviceInfo,
    ZkDeviceStatus,
    ZkLiveStatus,
    ZkMemoryUsage,
    ZkSyncResult,
    ZkUser,
)

logger = logging.getLogger("studysync.zkteco")

router = APIRouter(
    prefix="/api/zkteco",
    tags=["ZKTeco Device"],
    dependencies=[Depends(require_api_key)],
)


def _config_or_503():
    """Return the device config or raise 503 when none is configured."""
    config = device_config()
    if config is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "ZKTeco device is not configured. Set ZK_DEVICE_IP (and optionally "
                "ZK_DEVICE_PORT / ZK_COMM_KEY) on the server, then restart."
            ),
        )
    return config


def _device_error(exc: Exception):
    """Turn a ZkError into a 502 so internal details stay out of the response."""
    logger.warning("ZKTeco device error: %s", exc)
    raise HTTPException(status_code=502, detail="ZKTeco device error") from exc


@router.get("/status", response_model=ZkDeviceStatus)
def zk_status():
    """Cheap connectivity probe: does the device accept a connection?"""
    config = _config_or_503()
    try:
        device.device_status(config)
    except device.ZkError as e:
        logger.warning("ZKTeco status check failed: %s", e)
        raise HTTPException(status_code=502, detail="ZKTeco device unreachable") from e
    return ZkDeviceStatus(ok=True)


@router.get("/info", response_model=ZkDeviceInfo)
def zk_info():
    """Device self-description: name, firmware, serial number, MAC, time..."""
    config = _config_or_503()
    try:
        return ZkDeviceInfo(**device.device_info(config))
    except device.ZkError as e:
        _device_error(e)


@router.get("/users", response_model=List[ZkUser])
def zk_users():
    """List every user enrolled on the device."""
    config = _config_or_503()
    try:
        return [ZkUser(**u) for u in device.list_users(config)]
    except device.ZkError as e:
        _device_error(e)


@router.get("/attendance", response_model=List[ZkAttendanceLog])
def zk_attendance(
    since: Optional[date] = Query(
        None, description="Only return swipes on or after this date (YYYY-MM-DD)."
    ),
):
    """
    Raw attendance buffer from the device (one entry per swipe).

    This does NOT clear the buffer -- use the sync endpoint for that.
    """
    config = _config_or_503()
    try:
        logs = device.list_attendance(config)
    except device.ZkError as e:
        _device_error(e)
    if since is not None:
        logs = [log for log in logs if log["timestamp"].date() >= since]
    return [ZkAttendanceLog(**log) for log in logs]


@router.post("/attendance/sync", response_model=ZkSyncResult)
def zk_sync_attendance(
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    """
    Pull swipes from the device, applying each as a check-in or check-out.

    First swipe of a day opens an attendance row (check_in set, no
    check_out yet); the next swipe closes it. session and duration use the
    same logic as the manual front-desk flow. The device buffer is cleared
    automatically once the writes are committed -- there is no
    clear_after_sync flag anymore, because in this model every log is
    captured the moment it is read.
    """
    config = _config_or_503()
    try:
        return ZkSyncResult(**sync_attendance_from_device(db, config))
    except device.ZkError as e:
        _device_error(e)


@router.get("/memory", response_model=ZkMemoryUsage)
def zk_memory():
    """Current usage vs. capacity for users, fingerprints, faces, records."""
    config = _config_or_503()
    try:
        return ZkMemoryUsage(**device.memory_usage(config))
    except device.ZkError as e:
        _device_error(e)


@router.get("/live/status", response_model=ZkLiveStatus)
def zk_live_status():
    """
    Status of the real-time punch listener (zkteco/live.py).

    Only meaningful when ZK_ATTENDANCE_MODE=live -- in "poll" mode (the
    default) this just reports mode="poll" and connected=False, since no
    persistent connection is held between polls. Use /api/zkteco/status
    for a connectivity probe in poll mode instead.
    """
    return ZkLiveStatus(mode=attendance_mode(), **get_live_status())
