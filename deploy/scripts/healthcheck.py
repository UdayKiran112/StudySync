"""
healthcheck.py
--------------
Checks that StudySync is fully operational and writes a machine-readable
result. Exit code 0 = healthy, non-zero = something needs attention.

Run on demand by staff, or by the scheduled watchdog task every 5 minutes.
If a service is down it attempts a recovery restart (must run elevated).

    python C:\\ProgramData\\StudySync\\scripts\\healthcheck.py
"""

import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

APP_DIR = Path(os.getenv("STUDYSYNC_APP_DIR", r"C:\ProgramData\StudySync"))
LOG_DIR = APP_DIR / "logs" / "health"
API_URL = "http://127.0.0.1:8000/"
WEB_URL = "http://127.0.0.1/"

# Synthetic serial used for the ADMS /iclock* probe. The backend treats this
# serial specially (adms/ingest.HEALTHCHECK_SERIAL): the probe proves the
# device-push path (Caddy -> backend) works, but is never recorded in device
# status and never ingested as a punch. Keep it in sync with the backend.
HEALTHCHECK_SERIAL = os.getenv(
    "STUDYSYNC_HEALTHCHECK_SERIAL", "STUDYSYNC-HEALTHCHECK-PROBE"
)


def log(msg: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now().isoformat(timespec='seconds')} | {msg}"
    print(line)
    with open(LOG_DIR / "health.log", "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def service_state(name: str) -> str:
    # CREATE_NO_WINDOW: this exe is built windowless (PyInstaller --noconsole),
    # so any console child it spawns would flash a new Command Prompt window.
    # sc.exe is a console app; without this flag it pops a window on every run.
    out = subprocess.run(
        ["sc", "query", name],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    ).stdout
    for line in out.splitlines():
        if "STATE" in line:
            # sc output:  "        STATE              : 4  RUNNING"
            after = line.split(":", 1)[1].strip()
            tokens = after.split()
            return tokens[1] if len(tokens) > 1 else tokens[0]
    return "UNKNOWN"


def try_restart(name: str) -> None:
    log(f"Attempting restart of {name}...")
    subprocess.run(
        ["sc", "start", name],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    time.sleep(5)


def check_http(url: str, timeout: float = 5.0) -> bool:
    try:
        sock = socket.create_connection(("127.0.0.1", 80), timeout=timeout)
        sock.close()
        import urllib.request

        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status < 500
    except Exception:  # noqa: BLE001
        return False


def check_adms_path(url: str, timeout: float = 5.0):
    """Exercise the exact ADMS device-push path the ZKTeco device uses.

    A GET to /iclock/cdata is the device's handshake: the backend answers with
    a "GET OPTION FROM:<SN>" config block and records nothing (the probe
    serial is filtered by the backend, so it never lands in device status or
    the attendance ledger). A healthy response proves Caddy is proxying
    /iclock/* to the backend, uncompressed and unfiltered -- the same route a
    real ATTLOG push would take.

    Returns True = healthy, None = route not deployed (backend answered 404,
    i.e. this install runs ZK_INTEGRATION=pyzk/none without the ADMS router),
    False = deployed but broken (non-404 failure: Caddy down, backend down,
    or the ADMS handler erroring).
    """
    url = f"{url}?SN={HEALTHCHECK_SERIAL}"
    try:
        import urllib.request

        with urllib.request.urlopen(url, timeout=timeout) as r:
            if r.status != 200:
                return False
            body = r.read(4096).decode("utf-8", errors="replace")
            return "GET OPTION FROM:" in body
    except urllib.error.HTTPError as exc:
        return None if exc.code == 404 else False
    except Exception:  # noqa: BLE001
        return False


def check_mdns_name(timeout: float = 3.0) -> str:
    """Best-effort: does `studysync.local` resolve on this host right now?
    Non-fatal — the name is a convenience (the ADMS device can also use the
    LAN IP). Returns a short human-readable result."""
    import threading

    result: dict = {}

    def probe():
        try:
            infos = socket.getaddrinfo("studysync.local", 80, socket.AF_INET)
            result["addrs"] = sorted({i[4][0] for i in infos})
        except Exception as exc:  # noqa: BLE001
            result["err"] = str(exc)

    t = threading.Thread(target=probe, daemon=True)
    t.start()
    t.join(timeout)
    if "addrs" in result:
        return "OK (" + ", ".join(result["addrs"]) + ")"
    if "err" in result:
        return "NOT RESOLVING (" + result["err"] + ")"
    return "UNRESOLVED (timeout)"


def main() -> int:
    problems = []

    log(f"mDNS: studysync.local {check_mdns_name()}")

    api_state = service_state("StudySyncAPI")
    caddy_state = service_state("StudySyncCaddy")
    log(f"Services: API={api_state}  Caddy={caddy_state}")

    if api_state != "RUNNING":
        problems.append(f"API service is {api_state}")
        try_restart("StudySyncAPI")
        if service_state("StudySyncAPI") != "RUNNING":
            log("API service failed to restart.")

    if caddy_state != "RUNNING":
        problems.append(f"Caddy service is {caddy_state}")
        try_restart("StudySyncCaddy")
        if service_state("StudySyncCaddy") != "RUNNING":
            log("Caddy service failed to restart.")

    if not check_http(API_URL):
        problems.append("API not responding on 127.0.0.1:8000")
    if not check_http(WEB_URL):
        problems.append("Web server not responding on port 80")

    # ADMS device-push path: once directly (backend) and once through Caddy
    # (the route the device actually uses). Fails separately so the log pinpoints
    # whether Caddy's /iclock/* proxying broke or the backend's ADMS handler did.
    # A 404 means this install deliberately runs without the ADMS router
    # (ZK_INTEGRATION=pyzk/none) - that is not a fault.
    api_adms_ok = check_adms_path(API_URL.rstrip("/") + "/iclock/cdata")
    web_adms_ok = check_adms_path(WEB_URL.rstrip("/") + "/iclock/cdata")
    if api_adms_ok is False:
        problems.append("ADMS /iclock path not serving on 127.0.0.1:8000")
    if web_adms_ok is False:
        problems.append("ADMS /iclock path not served through Caddy on port 80")
    if api_adms_ok is True or web_adms_ok is True:
        log("ADMS /iclock probe OK (Caddy -> backend device-push path)")

    if problems:
        log(f"UNHEALTHY: {'; '.join(problems)}")
        return 1
    log("HEALTHY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
