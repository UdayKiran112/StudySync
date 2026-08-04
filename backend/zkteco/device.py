"""
zkteco/device.py
----------------
Thin, safe wrapper around the `pyzk` library (``from zk import ZK``).

Everything here only talks to the device configured in the environment
(see zkteco/config.py) -- there is no way for a caller to redirect these
connections at an arbitrary device. All failures are normalised to
:class:`ZkError` so the router can return clean 502 responses instead of
leaking pyzk tracebacks.
"""

from contextlib import contextmanager
from typing import Iterator, List, Optional

from zk import ZK

from zkteco.config import ZkDeviceConfig

# The ZKTeco proprietary protocol speaks neither TLS nor SSH. The
# "communication key" is weak obfuscation with a well-known XOR constant,
# so treat the device LAN as trusted. We keep pyzk defaults but skip the
# TCP ping pre-flight (ommit_ping), which is flaky on many office LANs
# while the actual SDK handshake succeeds.
def _build_zk(config: ZkDeviceConfig) -> ZK:
    return ZK(
        config.ip,
        port=config.port,
        timeout=config.timeout,
        password=config.comm_key,
        force_udp=False,
        ommit_ping=True,
    )


class ZkError(RuntimeError):
    """Raised whenever the device cannot be reached or replies with an error."""


@contextmanager
def zk_connection(config: ZkDeviceConfig) -> Iterator:
    """
    Connect to the configured device for the duration of the ``with`` block.

    ``connect()`` puts the device into a locked (offline) state while the
    connection is open, and ``disconnect()`` re-enables it -- which is why
    we always use this context manager and never leave a connection open
    across requests.
    """
    zk = _build_zk(config)
    try:
        conn = zk.connect()
    except Exception as e:  # pyzk raises a mix of socket + OSError types
        raise ZkError(f"Cannot connect to ZKTeco device at {config.ip}:{config.port}: {e}") from e
    try:
        yield conn
    finally:
        try:
            conn.disconnect()
        except Exception:
            pass


def _safe(fn, label: str):
    try:
        return fn()
    except ZkError:
        raise
    except Exception as e:
        raise ZkError(f"{label} failed: {e}") from e


def device_status(config: ZkDeviceConfig) -> dict:
    """Cheap connectivity probe -- just opens and closes a connection."""
    with zk_connection(config):
        return {"ok": True}


def device_info(config: ZkDeviceConfig) -> dict:
    """Gather the device's self-description (name, firmware, serial, MAC...)."""
    probes = [
        ("device_name", "get_device_name"),
        ("firmware_version", "get_firmware_version"),
        ("serial_number", "get_serialnumber"),
        ("platform", "get_platform"),
        ("mac", "get_mac"),
        ("face_version", "get_face_version"),
        ("fp_version", "get_fp_version"),
        ("device_time", "get_time"),
    ]
    info: dict = {}
    with zk_connection(config) as conn:
        for key, method_name in probes:
            method = getattr(conn, method_name, None)
            if not callable(method):
                info[key] = None
                continue
            try:
                info[key] = method()
            except Exception:
                info[key] = None
    return info


def list_users(config: ZkDeviceConfig) -> List[dict]:
    """Return every user enrolled on the device."""
    with zk_connection(config) as conn:
        users = _safe(conn.get_users, "Reading device users")
        return [
            {
                "uid": u.uid,
                "name": u.name,
                "privilege": u.privilege,
                "user_id": u.user_id,
                "group_id": u.group_id,
            }
            for u in users
        ]


def list_attendance(config: ZkDeviceConfig) -> List[dict]:
    """
    Return every attendance log currently buffered on the device, oldest
    first. Each entry is one finger/card/facial swipe.

    Note: this does NOT clear the device buffer -- that only happens via an
    explicit call to clear_attendance() after a successful sync, so a crash
    mid-sync never silently loses logs.
    """
    with zk_connection(config) as conn:
        logs = _safe(conn.get_attendance, "Reading attendance logs")
        logs.sort(key=lambda a: a.timestamp)
        return [
            {
                "uid": a.uid,
                "user_id": a.user_id,
                "timestamp": a.timestamp,
                "status": a.status,
            }
            for a in logs
        ]


def memory_usage(config: ZkDeviceConfig) -> dict:
    """Current vs. capacity for users/fingers/faces/attendance records."""
    with zk_connection(config) as conn:
        _safe(conn.read_sizes, "Reading memory sizes")
        return {
            "users": conn.users,
            "users_capacity": conn.users_cap,
            "fingers": conn.fingers,
            "fingers_capacity": conn.fingers_cap,
            "faces": conn.faces,
            "faces_capacity": conn.faces_cap,
            "records": conn.records,
            "records_capacity": conn.rec_cap,
        }


def clear_attendance(config: ZkDeviceConfig) -> None:
    """Erase the attendance buffer on the device (used after a sync)."""
    with zk_connection(config) as conn:
        _safe(conn.clear_attendance, "Clearing device attendance buffer")


def device_time(config: ZkDeviceConfig) -> Optional[any]:
    with zk_connection(config) as conn:
        return _safe(conn.get_time, "Reading device time")
