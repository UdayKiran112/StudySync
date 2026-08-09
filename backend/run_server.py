"""
run_server.py
-------------
Production server entry point (also used by the PyInstaller build).

Starts Uvicorn with settings tuned for a Windows-service deployment:
  * binds to 127.0.0.1 only (all LAN traffic arrives through the Caddy proxy)
  * single in-process worker — the app object is passed directly so uvicorn
    never spawns a child interpreter (critical when frozen: a spawned child
    would look for the build machine's python.exe)
  * rotating file logs (inline logging dict, no external config file to load)

Run directly:      python run_server.py
Run frozen:        studysync-api.exe
"""

import multiprocessing
import os
import sys
import threading
import time
from pathlib import Path

# Required for frozen Windows executables that may use multiprocessing.
multiprocessing.freeze_support()

from dotenv import load_dotenv

# .env sits next to the app code when running from source, and next to the
# frozen executable in production. Prefer the executable directory so the
# installer-written .env is always found.
if getattr(sys, "frozen", False):
    _here = Path(sys.executable).parent
else:
    _here = Path(__file__).parent
load_dotenv(_here / ".env")

# Matplotlib font-cache dir. In a frozen PyInstaller app a missing/writable
# cache makes matplotlib rebuild its font DB on every start, which can crash
# with a spurious KeyboardInterrupt. Point it at a persistent writable folder
# (set by WinSW too, but this covers direct `studysync-api.exe` runs).
MPLCONFIGDIR = os.getenv(
    "STUDYSYNC_MPLCONFIGDIR", r"C:\ProgramData\StudySync\data\mplcache"
)
os.environ["MPLCONFIGDIR"] = MPLCONFIGDIR
Path(MPLCONFIGDIR).mkdir(parents=True, exist_ok=True)

from main import app  # noqa: E402

import uvicorn  # noqa: E402

# Inline logging configuration — keeps the frozen build free of external
# config-file loading (uvicorn cannot reload a bundled file from _internal).
LOG_DIR = Path(
    os.getenv("STUDYSYNC_LOG_DIR", r"C:\ProgramData\StudySync\logs\api")
)
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "format": "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "api_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOG_DIR / "api.log"),
            "maxBytes": 5_242_880,
            "backupCount": 10,
            "formatter": "default",
            "encoding": "utf-8",
        },
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
        },
    },
    "root": {"level": "INFO", "handlers": ["api_file", "console"]},
    "loggers": {
        "uvicorn": {"level": "INFO", "handlers": ["api_file", "console"], "propagate": False},
        "studysync": {"level": "INFO", "handlers": ["api_file", "console"], "propagate": False},
        "gspread": {"level": "WARNING", "handlers": ["api_file"], "propagate": False},
        "urllib3": {"level": "WARNING", "handlers": ["api_file"], "propagate": False},
        "matplotlib": {"level": "WARNING", "handlers": ["api_file"], "propagate": False},
        "pyzk": {"level": "INFO", "handlers": ["api_file", "console"], "propagate": False},
    },
}


def _get_lan_ipv4s() -> list[str]:
    """All non-loopback IPv4 addresses of this machine (several if the machine
    has Wi-Fi + Ethernet, or a VPN). Empty if none found."""
    import socket

    addrs: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and not ip.startswith("169.254."):
                if ip not in addrs:
                    addrs.append(ip)
    except Exception:  # noqa: BLE001
        return []
    return addrs


def _other_mdns_responder_present() -> tuple[bool, str]:
    """Detect whether another mDNS responder is already answering for this
    host, so we never fight it for the name (two responders on one machine
    causes studysync.local to resolve unreliably, or the second responder to
    fail its UDP 5353 bind outright).

    Two independent signals are checked:
      1. A Windows service named "Bonjour Service" (Apple's mDNSResponder) is
         running -- the API service may be running on a machine where the
         admin installed Bonjour for its own Windows-client reasons.
      2. UDP port 5353 rejects a SO_REUSEADDR bind, which happens when an
         exclusive-mode responder (Bonjour, or a second zeroconf instance)
         already owns it. Python's zeroconf would then fail to advertise at
         all, so skipping up front is cheaper and logs a clear reason.

    Returns (is_conflict, reason). Never raises; any probe error is treated
    as "no conflict detected" so advertising can proceed and fail normally.
    """
    import socket
    import subprocess

    if sys.platform.startswith("win"):
        try:
            out = subprocess.run(
                ["sc", "query", "Bonjour Service"],
                capture_output=True,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=5,
            ).stdout
            for line in out.splitlines():
                if "STATE" in line:
                    tokens = line.split(":", 1)[1].strip().split()
                    state = tokens[1] if len(tokens) > 1 else tokens[0]
                    if state == "RUNNING":
                        return True, "Apple Bonjour Service is running (its mDNS responder owns UDP 5353)"
                    break
        except Exception:  # noqa: BLE001 - probe is best-effort
            pass

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("", 5353))
        finally:
            s.close()
    except OSError:
        return True, "another process already owns UDP port 5353"
    return False, ""


def _advertise_mdns() -> None:
    """Advertise studysync.local over mDNS so LAN devices can reach the app by
    name (http://studysync.local) instead of a raw IP. Runs in a daemon thread:
    failures are logged and never crash the API service.

    Self-healing: the machine's addresses are re-resolved every 60 s and the
    advertisement is re-registered when they change, so moving the laptop to
    another Wi-Fi (or a DHCP lease change) is picked up within a minute - no
    service restart needed.

    Coexistence: if another mDNS responder (Apple Bonjour Service, or any
    process bound to UDP 5353) is already running on this host, we skip the
    advertisement and log the reason once instead of fighting for the name.
    StudySync's own LAN advertising is a convenience; the app is still fully
    reachable by http://<LAN-IP> in that case."""
    import logging
    import socket

    logger = logging.getLogger("studysync.mdns")

    try:
        from zeroconf import ServiceInfo, Zeroconf
    except Exception as exc:  # pragma: no cover - missing dependency
        logger.warning("mDNS advertisement disabled (zeroconf unavailable): %s", exc)
        return

    conflict, reason = _other_mdns_responder_present()
    if conflict:
        logger.warning(
            "mDNS advertisement skipped: %s. LAN devices should use "
            "http://<this-PC-IP> instead of http://studysync.local.",
            reason,
        )
        return

    zc = None
    registered: tuple[str, ...] | None = None
    try:
        zc = Zeroconf()
        while True:
            addrs = _get_lan_ipv4s()
            current = tuple(sorted(addrs))
            if current != registered:
                if registered is not None:
                    try:
                        zc.unregister_all_services()
                    except Exception:  # noqa: BLE001
                        pass
                if current:
                    # server="studysync.local." makes the responder publish an A
                    # record for the studysync.local hostname (not just the
                    # SRV/PTR service records), so a browser typing
                    # http://studysync.local can resolve it directly.
                    service = ServiceInfo(
                        "_http._tcp.local.",
                        "studysync._http._tcp.local.",
                        addresses=[socket.inet_aton(ip) for ip in addrs],
                        port=80,
                        properties={b"path": b"/"},
                        server="studysync.local.",
                    )
                    zc.register_service(service, allow_name_change=False)
                    logger.info(
                        "mDNS: advertising http://studysync.local -> %s (port 80)",
                        ", ".join(addrs),
                    )
                else:
                    logger.warning("mDNS: no LAN IPv4 found - nothing advertised")
                registered = current
            time.sleep(60)
    except Exception as exc:  # noqa: BLE001
        logger.error("mDNS advertisement error: %s", exc)
    finally:
        if zc is not None:
            try:
                zc.unregister_all_services()
                zc.close()
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    threading.Thread(target=_advertise_mdns, name="mdns-advertise", daemon=True).start()
    host = os.getenv("STUDYSYNC_HOST", "127.0.0.1")
    port = int(os.getenv("STUDYSYNC_PORT", "8000"))
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_config=LOGGING_CONFIG,
        access_log=False,
    )
