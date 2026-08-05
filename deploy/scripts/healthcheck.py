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


def main() -> int:
    problems = []

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

    if problems:
        log(f"UNHEALTHY: {'; '.join(problems)}")
        return 1
    log("HEALTHY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
