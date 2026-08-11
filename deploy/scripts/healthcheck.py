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
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

APP_DIR = Path(os.getenv("STUDYSYNC_APP_DIR", r"C:\ProgramData\StudySync"))
LOG_DIR = APP_DIR / "logs" / "health"
DB_PATH = Path(os.getenv("STUDYSYNC_DB_PATH", APP_DIR / "data" / "library.db"))
BACKUP_DIR = APP_DIR / "backups"
RESTORE_STAMP = APP_DIR / "data" / ".restore.stamp"
# Crash-loop guard: never auto-restore more than once per window, so a
# genuinely broken backup cannot destroy the evidence / fight the live data.
RESTORE_COOLDOWN_HOURS = int(os.getenv("STUDYSYNC_AUTO_RESTORE_COOLDOWN_HOURS", "24"))
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


def sc_service(name: str, action: str) -> None:
    subprocess.run(
        ["sc", action, name],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def db_state() -> str:
    """'ok' | 'missing' | 'corrupt' — the DB is the one hard dependency that
    a service restart cannot fix, so it is the trigger for auto-restore."""
    if not DB_PATH.exists():
        return "missing"
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=5)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            if not row or row[0] != "ok":
                return "corrupt"
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return "corrupt"
    except Exception:  # noqa: BLE001
        return "corrupt"
    return "ok"


def newest_local_backup() -> Path | None:
    try:
        zips = sorted(BACKUP_DIR.glob("studysync_*.zip"), key=lambda f: f.stat().st_mtime, reverse=True)
        return zips[0] if zips else None
    except OSError:
        return None


def download_newest_remote(dest_dir: Path) -> Path | None:
    """Pull the newest backup from Google Drive, or None if unconfigured."""
    try:
        import gdrive

        gdrive.load_env()
        if not gdrive.enabled():
            return None
        session = gdrive.get_session()
        return gdrive.download_newest(session, dest_dir)
    except Exception as exc:  # noqa: BLE001
        log(f"Drive download failed: {exc}")
        return None


def auto_restore() -> None:
    """Restore the DB from the newest backup when it is missing/corrupt.

    Stops the API service, keeps the broken DB as library.db.corrupt-<ts>
    (evidence), extracts the newest local zip (falling back to the newest
    Drive copy), verifies the result, then starts the service again. Guarded
    by a 24h crash-loop cooldown.
    """
    try:
        if RESTORE_STAMP.exists():
            last = datetime.fromtimestamp(RESTORE_STAMP.stat().st_mtime)
            if datetime.now() - last < timedelta(hours=RESTORE_COOLDOWN_HOURS):
                log("Auto-restore SKIPPED: last restore inside the cooldown window")
                return
    except OSError:
        pass

    source = newest_local_backup()
    remote_dir = None
    if source is None:
        log("No local backup found; checking Google Drive...")
        remote_dir = LOG_DIR
        remote_dir.mkdir(parents=True, exist_ok=True)
        source = download_newest_remote(remote_dir)
        if source is None:
            log("ERROR: no local or remote backup available; auto-restore impossible")
            return

    evidence = Path(str(DB_PATH) + f".corrupt-{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    restored = False
    try:
        log(f"Auto-restore: stopping StudySyncAPI (source: {source.name})")
        sc_service("StudySyncAPI", "stop")
        for _ in range(12):
            if service_state("StudySyncAPI") != "RUNNING":
                break
            time.sleep(5)

        if DB_PATH.exists():
            shutil.copy2(DB_PATH, evidence)
            log(f"Preserved broken database at {evidence.name}")

        for suffix in ("-wal", "-shm"):
            Path(str(DB_PATH) + suffix).unlink(missing_ok=True)

        with zipfile.ZipFile(source) as zf:
            names = [n for n in zf.namelist() if n.endswith(".db")]
            if not names:
                raise RuntimeError("backup zip contains no .db member")
            with zf.open(names[0]) as src, open(DB_PATH, "wb") as dst:
                shutil.copyfileobj(src, dst)

        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
        finally:
            conn.close()
        if not row or row[0] != "ok":
            raise RuntimeError("restored database failed integrity check")

        RESTORE_STAMP.parent.mkdir(parents=True, exist_ok=True)
        RESTORE_STAMP.write_text(datetime.now().isoformat(timespec="seconds"))
        restored = True
        log(f"Auto-restore OK: {DB_PATH} restored from {source.name}")
    except Exception as exc:  # noqa: BLE001
        log(f"ERROR: auto-restore failed: {exc}")
        if evidence.exists():
            shutil.copy2(evidence, DB_PATH)
            log(f"Rolled back to the pre-restore database ({evidence.name}).")
    finally:
        sc_service("StudySyncAPI", "start")
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


def check_mdns_name(timeout: float = 3.0) -> str:
    """Best-effort: does `studysync.local` resolve on this host right now?
    Non-fatal — the name is a convenience (the LAN IP works too). Returns a
    short human-readable result."""
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

    try:
        import gdrive

        gdrive.load_env()
    except Exception:  # noqa: BLE001
        pass

    log(f"mDNS: studysync.local {check_mdns_name()}")

    db = db_state()
    log(f"Database: {db}")

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

    if db != "ok":
        problems.append(f"database is {db}")
        log(f"AUTO-RESTORE: database {db}; restoring from newest backup...")
        auto_restore()
        # The service was just restarted; HTTP probes would race its startup.
        log("Skipping HTTP probes this run (services were restarted by auto-restore).")
        if problems:
            log(f"UNHEALTHY: {'; '.join(problems)}")
            return 1
        log("HEALTHY (pending restart)")
        return 0

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
