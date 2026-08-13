r"""
zkteco/config.py
-----------------
Server-side configuration for the ZKTeco attendance device.

Device settings come from environment variables, NEVER from the client.
The reference project (najibullahjafari/zkteco_device_python_connect) let
callers pass ip/port/comm_key as query parameters on every request, which
meant anyone who could reach the API could point it at *any* ZKTeco device
on the network. Here the operator fixes the device once in the environment,
and the API key (STUDYSYNC_API_KEY) gates every endpoint on top of that.

Environment variables:

    ZK_DEVICE_IP        Device IP/hostname (e.g. 192.168.1.201). Required.
    ZK_DEVICE_PORT      Device TCP port. Default 4370 (ZKTeco standard).
    ZK_COMM_KEY         Device communication key. Default 0.
    ZK_DEVICE_TIMEOUT   Seconds to wait for a device reply. Default 30.
    ZK_POLL_INTERVAL    Seconds between automatic background syncs. Default 3.
                        Only read while ZK_ATTENDANCE_MODE=poll.
    ZK_ATTENDANCE_MODE  "poll" (default), "live" or "both". Selects which
                        background tasks main.py starts:
                          poll -> zkteco/poller.py  (pulls the device's
                                  buffer every ZK_POLL_INTERVAL seconds)
                          live -> zkteco/live.py    (holds one persistent
                                  connection open and reacts the instant
                                  the device reports a punch, via pyzk's
                                  live_capture())
                          both -> the live stream PLUS the poll as a
                                  safety net.
                        StudySync NEVER clears the device buffer -- it is a
                        pure reader, so any number of readers can run
                        against the same device without records being lost.
    ZK_LIVE_RECONNECT_SECONDS
                        Default 5. Wait time before zkteco/live.py retries
                        after a dropped/failed connection.
    ZK_RECONCILE_INTERVAL
                        Default 60. Seconds between full-buffer reconciliation
                        passes (zkteco/reconcile.py), the completeness
                        backstop that re-reads the whole device buffer and
                        captures anything poll/live missed.
    ZK_PUNCH_DEBOUNCE_MINUTES
                        Default 1. A punch that lands this many minutes or
                        fewer after a student's previous punch for the same
                        day is treated as an accidental double-tap and
                        ignored (set 0 to disable).
    ZK_LEDGER_RETENTION_DAYS
                        Default 30. Safety window of 'applied'/'duplicate_*'
                        device_punches ledger rows to keep when the device
                        buffer is empty (see
                        attendance_punch.prune_old_ledger_rows). While the
                        device still holds records, the pruner instead keeps
                        every row newer than the oldest record on the device
                        (dedup needs them -- a cleared buffer can never
                        re-serve a punch). 'pending' open check-ins and
                        today's punches are always kept. The raw record now
                        lives in the dated ATTLOG archives (see
                        ZK_BUFFER_ARCHIVE_DIR), so 30 days is plenty of audit
                        in the main DB.
    ZK_BUFFER_CLEAR_PERCENT
                        Default 95. When the device ATTLOG buffer fills to
                        this % of capacity, the reconcile pass archives the
                        whole buffer into a dated offline database
                        (device_punches_YYYY-MM-DD.db, see zkteco/archive.py)
                        and clears the device buffer. Clamped to 1..100.
    ZK_BUFFER_ALERT_PERCENT
                        Default 80. Buffer fill % at which /sync-report
                        starts warning that a clear is approaching.
                        Clamped to 1..100.
    ZK_BUFFER_AUTO_CLEAR
                        Default 1 (on). Whether the reconcile pass may clear
                        the device buffer after a verified archive. 0 keeps
                        StudySync a pure reader (the buffer is never
                        touched); POST /api/zkteco/attendance/clear is the
                        explicit operator override and works either way.
                        Auto-clear never runs unless verify_pyzk_vs_db
                        reports zero issues -- a record with no durable DB
                        write is never destroyed.
    ZK_BUFFER_ARCHIVE_DIR
                        Default <database folder>\device_punches. Directory
                        for the dated ATTLOG archive databases. One file per
                        day; each punch is keyed by its ledger fingerprint so
                        a same-day re-archive upserts instead of duplicating.

Not configured (no ZK_DEVICE_IP) => every device endpoint returns 503 (and
the background poller stays idle) so the rest of the app keeps working until
the operator wires the machine up.
"""

import os
from dataclasses import dataclass
from typing import Optional

# Re-exported for backward compatibility: `from zkteco.config import
# punch_debounce_minutes` still works, but the actual definition lives in
# attendance_punch.py (project root) since it's a punch-application
# setting shared by poll/live alike, not a pyzk-specific one -- see that
# module's docstring.
from attendance_punch import punch_debounce_minutes  # noqa: F401
from attendance_punch import ledger_retention_days  # noqa: F401


@dataclass(frozen=True)
class ZkDeviceConfig:
    ip: str
    port: int
    comm_key: int
    timeout: int


def device_config() -> Optional[ZkDeviceConfig]:
    """Return the device config from the environment, or None if no IP set."""
    ip = os.getenv("ZK_DEVICE_IP", "").strip()
    if not ip:
        return None
    return ZkDeviceConfig(
        ip=ip,
        port=int(os.getenv("ZK_DEVICE_PORT", "4370")),
        comm_key=int(os.getenv("ZK_COMM_KEY", "0")),
        timeout=int(os.getenv("ZK_DEVICE_TIMEOUT", "30")),
    )


def poll_interval() -> int:
    """Seconds between background syncs. Clamped to >= 1s, bad values -> 3s."""
    try:
        return max(1, int(os.getenv("ZK_POLL_INTERVAL", "3")))
    except ValueError:
        return 3


def attendance_mode() -> str:
    """
    Which background task(s) main.py should start: "poll" (default,
    zkteco/poller.py), "live" (zkteco/live.py), or "both". Any other
    value falls back to "poll".
    """
    mode = os.getenv("ZK_ATTENDANCE_MODE", "poll").strip().lower()
    return mode if mode in ("poll", "live", "both") else "poll"


def live_reconnect_seconds() -> int:
    """Backoff before zkteco/live.py retries a dropped connection. Clamped to >= 1s."""
    try:
        return max(1, int(os.getenv("ZK_LIVE_RECONNECT_SECONDS", "5")))
    except ValueError:
        return 5


def reconcile_interval() -> int:
    """Seconds between full-buffer reconciliation passes. Clamped to >= 5s."""
    try:
        return max(5, int(os.getenv("ZK_RECONCILE_INTERVAL", "60")))
    except ValueError:
        return 60


def buffer_clear_percent() -> int:
    """ATTLOG fill % at which the reconcile pass archives + clears the buffer."""
    try:
        return min(100, max(1, int(os.getenv("ZK_BUFFER_CLEAR_PERCENT", "95"))))
    except ValueError:
        return 95


def buffer_alert_percent() -> int:
    """ATTLOG fill % at which the sync-report starts warning. Clamped 1..100."""
    try:
        return min(100, max(1, int(os.getenv("ZK_BUFFER_ALERT_PERCENT", "80"))))
    except ValueError:
        return 80


def buffer_auto_clear_enabled() -> bool:
    """Whether reconcile may clear the device buffer after a verified archive."""
    return os.getenv("ZK_BUFFER_AUTO_CLEAR", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def buffer_archive_dir() -> str:
    """
    Directory holding the dated ATTLOG archive databases
    (device_punches_YYYY-MM-DD.db). Defaults to a ``device_punches``
    subfolder next to the main database (dirname of STUDYSYNC_DB_PATH).
    """
    env = os.getenv("ZK_BUFFER_ARCHIVE_DIR", "").strip()
    if env:
        return env
    db_path = os.getenv("STUDYSYNC_DB_PATH", "").strip()
    if db_path:
        return os.path.join(os.path.dirname(os.path.abspath(db_path)), "device_punches")
    return os.path.join(os.getcwd(), "device_punches")
