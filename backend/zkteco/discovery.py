"""
zkteco/discovery.py
-------------------
Find a ZKTeco attendance device on the LAN when its IP is unknown or has
drifted (a DHCP re-lease on the MB360 is exactly the failure this exists
for). The ZK wire protocol listens on TCP 4370, so discovery:

  1. derives the local subnets to scan (the machine's own /24s, or an
     explicit ``STUDYSYNC_SCAN_SUBNETS`` override),
  2. probes every host in parallel for an open 4370 port (a few seconds for
     a full /24),
  3. confirms each open port with a real pyzk session (reads the serial
     number) so a random service is never mistaken for an attendance device.

Confirmed results can be persisted in the ``runtime_config`` table via
:func:`cache_device` -- the service account cannot write ``app\\api\\.env``
(ACL-hardened read-only), so the discovered IP lives in the database, which
is writable and survives restarts/update swaps. The poller falls back to it
when the configured IP stops answering (see zkteco/poller.py).

Only single-device venues auto-accept a discovery result. When several
devices respond, discovery reports them all and the operator picks one via
``POST /api/zkteco/device`` -- we never silently connect to the wrong
machine.
"""

import ipaddress
import logging
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

from zk import ZK

from database import get_runtime_config, set_runtime_config
from zkteco.config import ZkDeviceConfig

logger = logging.getLogger("zkteco.discovery")

ZK_PORT = int(os.getenv("ZK_DEVICE_PORT", "4370"))
CONNECT_TIMEOUT_SECONDS = float(os.getenv("ZK_SCAN_TIMEOUT", "0.4"))
CONFIRM_TIMEOUT_SECONDS = float(os.getenv("ZK_CONFIRM_TIMEOUT", "5"))
MAX_WORKERS = int(os.getenv("ZK_SCAN_WORKERS", "80"))
SUBNET_OVERRIDE = os.getenv("STUDYSYNC_SCAN_SUBNETS", "").strip()

KEY_DEVICE_IP = "zk.device_ip"
KEY_DEVICE_SERIAL = "zk.device_serial"
KEY_LAST_SCAN = "zk.last_scan_at"


# ---------------------------------------------------------------------------
# subnet enumeration
# ---------------------------------------------------------------------------
def _primary_ip() -> Optional[str]:
    """The outbound (default-route) IPv4 address. Never raises."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return None


def _hostname_ips() -> List[str]:
    """Best-effort extra local IPv4s from the hostname. Never raises."""
    ips: List[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    return ips


def _to_network(ip: str) -> Optional[str]:
    """Normalise a host IP to its /24 network string ('' for unusable IPs)."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if addr.is_loopback or addr.is_link_local or addr.is_unspecified:
        return None
    return str(ipaddress.IPv4Network(f"{addr}/24", strict=False))


def local_subnets() -> List[str]:
    """
    The /24 subnets worth scanning: the primary outbound interface plus any
    other local IPv4s, deduped. ``STUDYSYNC_SCAN_SUBNETS`` (comma-separated
    CIDRs) overrides this entirely for venues where the device lives on a
    different segment than the default route.
    """
    if SUBNET_OVERRIDE:
        nets = []
        for raw in SUBNET_OVERRIDE.split(","):
            raw = raw.strip()
            if not raw:
                continue
            try:
                nets.append(str(ipaddress.ip_network(raw, strict=False)))
            except ValueError:
                logger.warning("Ignoring invalid STUDYSYNC_SCAN_SUBNETS entry %r", raw)
        if nets:
            return nets
    nets: List[str] = []
    candidates = [_primary_ip()] + _hostname_ips()
    for ip in candidates:
        if not ip:
            continue
        net = _to_network(ip)
        if net and net not in nets:
            nets.append(net)
    if not nets:
        logger.warning(
            "Could not determine a local subnet to scan for the ZKTeco device. "
            "Set STUDYSYNC_SCAN_SUBNETS to scan a specific range."
        )
    return nets


def _iter_hosts(network: str):
    """Usable host addresses of a CIDR network (excludes net/broadcast)."""
    net = ipaddress.ip_network(network, strict=False)
    return (str(host) for host in net.hosts())


# ---------------------------------------------------------------------------
# scanning
# ---------------------------------------------------------------------------
def _port_open(ip: str, port: int, timeout: float) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((ip, port)) == 0
    except OSError:
        return False


def _confirm_zk(ip: str, port: int, comm_key: int, timeout: float) -> Optional[dict]:
    """
    Open a real pyzk session and read identifying fields. Returns a device
    descriptor, or None when the open port is not a ZKTeco device (or the
    probe times out).
    """
    zk = ZK(
        ip,
        port=port,
        timeout=timeout,
        password=comm_key,
        force_udp=False,
        ommit_ping=True,
    )
    try:
        conn = zk.connect()
    except Exception as e:  # pyzk raises mixed socket/OSError types
        logger.debug("Port 4370 open at %s but ZK handshake failed: %s", ip, e)
        return None
    try:
        serial = None
        device_name = None
        try:
            serial = conn.get_serialnumber()
        except Exception:
            pass
        try:
            device_name = conn.get_device_name()
        except Exception:
            pass
        return {
            "ip": ip,
            "port": port,
            "serial": serial,
            "device_name": device_name,
            "confirmed": True,
        }
    finally:
        try:
            conn.disconnect()
        except Exception:
            pass


def discover(
    subnet: Optional[str] = None,
    port: int = ZK_PORT,
    comm_key: Optional[int] = None,
    connect_timeout: float = CONNECT_TIMEOUT_SECONDS,
    confirm_timeout: float = CONFIRM_TIMEOUT_SECONDS,
    max_workers: int = MAX_WORKERS,
) -> dict:
    """
    Scan for ZKTeco devices on the LAN.

    ``subnet`` restricts the scan to a single CIDR (else all local subnets
    are used). Returns a report dict::

        {
          "scanned_subnets": [...],
          "scanned_hosts": n,
          "devices": [ {ip, port, serial, device_name, confirmed}, ... ],
          "elapsed_ms": n,
        }

    Confirmed devices (serial read OK) are listed first; open-but-unprobed
    hosts follow with ``confirmed: False`` so the operator can still pick
    one manually. This never writes to the database.
    """
    comm_key = comm_key if comm_key is not None else int(os.getenv("ZK_COMM_KEY", "0"))
    subnets = [subnet] if subnet else local_subnets()
    if not subnets:
        return {"scanned_subnets": [], "scanned_hosts": 0, "devices": [], "elapsed_ms": 0}

    start = time.monotonic()
    hosts = [ip for net in subnets for ip in _iter_hosts(net)]
    open_ips: List[str] = []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_port_open, ip, port, connect_timeout): ip for ip in hosts
        }
        for future in futures:
            try:
                if future.result():
                    open_ips.append(futures[future])
            except Exception:  # defensive: one bad probe must not kill the scan
                continue

    devices: List[dict] = []
    confirmed: List[dict] = []
    unconfirmed: List[dict] = []
    if open_ips:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(open_ips))) as pool:
            futures = {
                pool.submit(_confirm_zk, ip, port, comm_key, confirm_timeout): ip
                for ip in open_ips
            }
            for future in futures:
                ip = futures[future]
                try:
                    result = future.result()
                except Exception:  # defensive
                    result = None
                if result:
                    confirmed.append(result)
                else:
                    unconfirmed.append({"ip": ip, "port": port, "serial": None,
                                        "device_name": None, "confirmed": False})

    confirmed.sort(key=lambda d: d["serial"] or d["ip"])
    devices = confirmed + unconfirmed

    elapsed_ms = int((time.monotonic() - start) * 1000)
    logger.info(
        "ZKTeco discovery: scanned %s host(s) across %s subnet(s); %s confirmed "
        "device(s), %s unconfirmed, in %sms.",
        len(hosts),
        len(subnets),
        len(confirmed),
        len(unconfirmed),
        elapsed_ms,
    )
    return {
        "scanned_subnets": subnets,
        "scanned_hosts": len(hosts),
        "devices": devices,
        "elapsed_ms": elapsed_ms,
    }


def probe_device(
    ip: str,
    port: int = ZK_PORT,
    comm_key: Optional[int] = None,
    timeout: float = CONFIRM_TIMEOUT_SECONDS,
) -> Optional[dict]:
    """
    Confirm a single IP is a ZKTeco device (opens a pyzk session, reads the
    serial). Returns the device descriptor or None. Used by the Settings
    "use this IP" action to capture the serial.
    """
    comm_key = comm_key if comm_key is not None else int(os.getenv("ZK_COMM_KEY", "0"))
    return _confirm_zk(ip, port, comm_key, timeout)


# ---------------------------------------------------------------------------
# persistence (runtime_config)
# ---------------------------------------------------------------------------
def cached_device_ip(db) -> Optional[str]:
    return get_runtime_config(db, KEY_DEVICE_IP)


def cached_device_serial(db) -> Optional[str]:
    return get_runtime_config(db, KEY_DEVICE_SERIAL)


def last_scan_at(db) -> Optional[str]:
    return get_runtime_config(db, KEY_LAST_SCAN)


def cache_device(db, ip: str, serial: Optional[str] = None) -> None:
    """Persist the device IP (and serial, if known) as the runtime config."""
    set_runtime_config(db, KEY_DEVICE_IP, ip)
    if serial:
        set_runtime_config(db, KEY_DEVICE_SERIAL, serial)
    db.commit()


def clear_cached_device(db) -> None:
    """Forget the discovered/manually-selected device (back to env config)."""
    db.execute("DELETE FROM runtime_config WHERE key IN (?, ?, ?)", (
        KEY_DEVICE_IP, KEY_DEVICE_SERIAL, KEY_LAST_SCAN,
    ))
    db.commit()


def record_scan(db) -> None:
    """Remember when a discovery scan last ran (for the Settings status)."""
    set_runtime_config(db, KEY_LAST_SCAN, time.strftime("%Y-%m-%d %H:%M:%S"))
    db.commit()


def scan_and_cache(db, subnet: Optional[str] = None, **kwargs) -> dict:
    """
    Run a discovery pass, remember when it ran, and -- ONLY when exactly one
    device answers -- persist it as the runtime device IP. Multi-device
    networks are reported but never auto-accepted (the operator picks).
    """
    report = discover(subnet=subnet, **kwargs)
    record_scan(db)
    confirmed = [d for d in report["devices"] if d.get("confirmed")]
    if len(confirmed) == 1:
        device = confirmed[0]
        cache_device(db, device["ip"], device.get("serial"))
        logger.info(
            "ZKTeco discovery auto-accepted single device %s (serial=%s).",
            device["ip"],
            device.get("serial"),
        )
    elif len(confirmed) > 1:
        logger.warning(
            "ZKTeco discovery found %s devices (%s) -- operator must pick one.",
            len(confirmed),
            ", ".join(d["ip"] for d in confirmed),
        )
    return report


def discovered_device_config(db) -> Optional[ZkDeviceConfig]:
    """Runtime-config device (operator pick or auto-healed) as a config."""
    ip = cached_device_ip(db)
    if not ip:
        return None
    return ZkDeviceConfig(
        ip=ip,
        port=int(os.getenv("ZK_DEVICE_PORT", "4370")),
        comm_key=int(os.getenv("ZK_COMM_KEY", "0")),
        timeout=int(os.getenv("ZK_DEVICE_TIMEOUT", "30")),
    )
