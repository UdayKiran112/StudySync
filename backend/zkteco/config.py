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
                        The poller only runs while the app is alive and
                        ZK_DEVICE_IP is set.
    ZK_PUNCH_DEBOUNCE_MINUTES
                        Default 5. A punch that lands this many minutes or
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


def punch_debounce_minutes() -> int:
    """Minutes of anti double-tap debounce. Clamped to >= 0, bad -> 5."""
    try:
        return max(0, int(os.getenv("ZK_PUNCH_DEBOUNCE_MINUTES", "5")))
    except ValueError:
        return 5
