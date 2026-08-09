"""
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
    ZK_ATTENDANCE_MODE  "poll" (default) or "live". Selects which ONE
                        background task main.py starts:
                          poll -> zkteco/poller.py  (pulls the device's
                                  buffer every ZK_POLL_INTERVAL seconds)
                          live -> zkteco/live.py    (holds one persistent
                                  connection open and reacts the instant
                                  the device reports a punch, via pyzk's
                                  live_capture())
                        Run only one against a given device at a time --
                        pyzk devices generally accept a single open
                        session, and both paths read/clear the same
                        physical buffer.
    ZK_LIVE_RECONNECT_SECONDS
                        Default 5. Wait time before zkteco/live.py retries
                        after a dropped/failed connection.
    ZK_RECONCILE_INTERVAL
                        Default 60. Seconds between full-buffer reconciliation
                        passes (zkteco/reconcile.py), the completeness
                        backstop that re-reads the whole device buffer and
                        captures anything ADMS/live missed.
    ZK_PUNCH_DEBOUNCE_MINUTES
                        Default 1. A punch that lands this many minutes or
                        fewer after a student's previous punch for the same
                        day is treated as an accidental double-tap and
                        ignored (set 0 to disable).

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
# setting shared by poll/live/ADMS alike, not a pyzk-specific one -- see
# that module's docstring. Importing it here does NOT create a
# zkteco-depends-on-adms or adms-depends-on-zkteco edge; both simply
# depend on the neutral attendance_punch module.
from attendance_punch import punch_debounce_minutes  # noqa: F401


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
    Which background task main.py should start: "poll" (default,
    zkteco/poller.py) or "live" (zkteco/live.py). Any other value falls
    back to "poll".
    """
    mode = os.getenv("ZK_ATTENDANCE_MODE", "poll").strip().lower()
    return mode if mode in ("poll", "live") else "poll"


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
