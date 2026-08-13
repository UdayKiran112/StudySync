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
import os
import sqlite3
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from database import get_db_dependency
from security import require_api_key
from zkteco import device
from zkteco.config import attendance_mode, effective_device_config
from zkteco.discovery import (
    cache_device,
    cached_device_ip,
    cached_device_serial,
    clear_cached_device,
    discover,
    last_scan_at,
    probe_device,
    record_scan,
)
from zkteco.live import get_live_status
from zkteco.reconcile import (
    archive_and_clear_attlog,
    current_sync_status,
    verify_pyzk_vs_db,
)
from zkteco.sync import sync_attendance_from_device
from models.zkteco import (
    ZkAttendanceLog,
    ZkBufferClearResult,
    ZkDeviceConfigStatus,
    ZkDeviceInfo,
    ZkDeviceSelection,
    ZkDeviceStatus,
    ZkDiscoveredDevice,
    ZkDiscoveryResult,
    ZkLiveStatus,
    ZkMemoryUsage,
    ZkSyncReport,
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
    """Return the effective device config or raise 503 when none is set."""
    config = effective_device_config()
    if config is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "ZKTeco device is not configured. Set ZK_DEVICE_IP (and optionally "
                "ZK_DEVICE_PORT / ZK_COMM_KEY) on the server, or find the device "
                "from Settings, then restart."
            ),
        )
    return config


def _device_error(exc: Exception):
    """Turn a ZkError into a 502 so internal details stay out of the response."""
    logger.warning("ZKTeco device error: %s", exc)
    raise HTTPException(status_code=502, detail="ZKTeco device error") from exc


def _device_config_status(db) -> ZkDeviceConfigStatus:
    """The current device StudySync is configured to use, for Settings."""
    env_ip = os.getenv("ZK_DEVICE_IP", "").strip() or None
    disc_ip = cached_device_ip(db)
    serial = cached_device_serial(db)
    cfg = effective_device_config(db)
    if disc_ip:
        source = "discovered"
    elif env_ip:
        source = "env"
    else:
        source = "none"
    return ZkDeviceConfigStatus(
        configured=cfg is not None,
        source=source,
        ip=cfg.ip if cfg else None,
        port=cfg.port if cfg else None,
        discovered_ip=disc_ip,
        discovered_serial=serial,
        env_ip=env_ip,
        last_scan_at=last_scan_at(db),
    )


@router.get("/discover", response_model=ZkDiscoveryResult)
def zk_discover(
    subnet: Optional[str] = Query(
        default=None,
        description="Scan a specific CIDR (e.g. 192.168.0.0/24). Defaults to the machine's local subnets.",
    ),
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    """
    Scan the LAN for ZKTeco devices. Confirmed devices (ZK handshake OK, serial
    read) come first; open-but-unconfirmed hosts follow so an operator can still
    pick one. Takes a few seconds for a /24. Never changes the configured device.
    """
    try:
        report = discover(subnet=subnet)
        record_scan(db)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("ZKTeco discovery failed: %s", e)
        raise HTTPException(status_code=502, detail="Device discovery failed") from e
    return ZkDiscoveryResult(
        scanned_subnets=report["scanned_subnets"],
        scanned_hosts=report["scanned_hosts"],
        devices=[ZkDiscoveredDevice(**d) for d in report["devices"]],
        elapsed_ms=report["elapsed_ms"],
    )


@router.get("/device", response_model=ZkDeviceConfigStatus)
def zk_get_device(db: sqlite3.Connection = Depends(get_db_dependency)):
    """The device StudySync is currently configured to talk to (and why)."""
    return _device_config_status(db)


@router.post("/device", response_model=ZkDeviceConfigStatus)
def zk_set_device(
    selection: ZkDeviceSelection,
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    """
    Point StudySync at a device. This is how the Settings "use this IP"
    action is applied: the selection is persisted in runtime_config (the
    service account cannot write .env), survives restarts and update swaps,
    and takes precedence over the .env ZK_DEVICE_IP -- which stays as the
    seed/first-run value.
    """
    ip = selection.ip.strip()
    if not ip:
        raise HTTPException(status_code=422, detail="Device IP is required")
    # Best-effort serial capture so the Settings page can show which device
    # is wired up. A non-ZK or unreachable address still gets stored -- the
    # auto-heal poller simply won't connect until a real device answers.
    serial = None
    try:
        confirmed = probe_device(ip, selection.port, selection.comm_key)
        if confirmed:
            serial = confirmed.get("serial")
    except Exception:  # never let a probe failure block the operator's pick
        logger.debug("Serial probe for %s failed; storing IP without serial.", ip)
    cache_device(db, ip, serial)
    logger.info("ZKTeco device set by operator: %s", ip)
    return _device_config_status(db)


@router.delete("/device", response_model=ZkDeviceConfigStatus)
def zk_clear_device(db: sqlite3.Connection = Depends(get_db_dependency)):
    """
    Forget the discovered/manually-selected device. StudySync falls back to
    the .env ZK_DEVICE_IP (or to "not configured").
    """
    clear_cached_device(db)
    logger.info("ZKTeco runtime device config cleared (back to .env / unconfigured).")
    return _device_config_status(db)


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

    Read-only -- the device buffer is never cleared.
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
    Pull swipes from the device, applying each as check-in/check-out.

    TODAY's first swipe opens an attendance row immediately (check_in set,
    no check_out yet, so the student shows as present); the next swipe
    closes it. A PAST day only materializes an attendance row when its
    check-out punch lands (a lone past-day check-in stays 'pending' in the
    device_punches ledger and never produces an open row). session and
    duration use the same logic as the manual front-desk flow. The device
    buffer is never cleared -- StudySync only reads; the exactly-once
    ledger makes a re-read a no-op.
    """
    config = _config_or_503()
    try:
        tally, logs = sync_attendance_from_device(
            db, config, return_logs=True
        )
        serial = device.device_serial(config)
        verify = verify_pyzk_vs_db(db, logs, serial)
        tally["verify_verified"] = verify["verified"]
        tally["verify_issue_count"] = verify["issue_count"]
        if verify["issue_count"]:
            logger.warning(
                "Sync verify mismatch: %s of %s pyzk records have no matching "
                "DB write. First issues: %s",
                verify["issue_count"],
                tally["pulled"],
                verify["issues"],
            )
        return ZkSyncResult(**tally)
    except device.ZkError as e:
        _device_error(e)


@router.post("/attendance/clear", response_model=ZkBufferClearResult)
def zk_clear_attendance(
    db: sqlite3.Connection = Depends(get_db_dependency),
):
    """
    Explicitly archive the full ATTLOG buffer and clear the device.

    Pulls every record, applies anything new to the ledger, verifies each
    pulled record has a durable DB write, archives the buffer into today's
    dated archive database (device_punches_YYYY-MM-DD.db), then clears the
    device and confirms the buffer is empty. This is the operator's
    explicit override, so it proceeds regardless of ZK_BUFFER_AUTO_CLEAR --
    but it NEVER clears unless verification is clean: a record with no
    durable write must not be destroyed. Returns the archive path, the
    number of records archived, and the records remaining on the device
    (0 means fully cleared).
    """
    config = _config_or_503()
    try:
        tally, logs = sync_attendance_from_device(
            db, config, source="manual_clear", return_logs=True
        )
    except device.ZkError as e:
        _device_error(e)
    serial = device.device_serial(config)
    verify = verify_pyzk_vs_db(db, logs, serial)
    if verify["issue_count"]:
        logger.warning(
            "Manual clear: %s of %s records have no durable write; refusing to clear.",
            verify["issue_count"],
            tally["pulled"],
        )
    result = archive_and_clear_attlog(config, logs, serial, verify, force=True)
    return ZkBufferClearResult(
        archived=result["buffer_archived"],
        cleared=result["buffer_cleared"],
        archive_path=result["archive_path"],
        archive_count=result["archive_count"],
        remaining_records=result["remaining_records"],
        verify_verified=verify["verified"],
        verify_issue_count=verify["issue_count"],
        buffer_status=result["buffer_status"],
    )


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


@router.get("/sync-report", response_model=ZkSyncReport)
def zk_sync_report():
    """
    Durable device sync health: how many raw punches are in the
    device_punches ledger, broken down by verdict (applied / duplicate /
    unknown), how many are still pending, when the device was last
    reconciled, and whether the system is fully synchronized
    (ledger_pending == 0).
    """
    return ZkSyncReport(**current_sync_status())
