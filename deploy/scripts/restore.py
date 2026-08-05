"""
restore.py
----------
Restore the StudySync database from a backup zip.

Usage (run from an elevated prompt, AFTER stopping the API service):
    python C:\\ProgramData\\StudySync\\scripts\\restore.py [backup-file.zip]

Without an argument it lists available backups and prompts for one.
The API service must be stopped first so the restore cannot be interrupted
by live writes; the script refuses to run if the service is running.

    sc stop StudySyncAPI   (or: net stop StudySyncAPI)

After restore, start the service again:
    sc start StudySyncAPI
"""

import datetime as dt
import os
import shutil
import sqlite3
import sys
import zipfile
from pathlib import Path

APP_DIR = Path(os.getenv("STUDYSYNC_APP_DIR", r"C:\ProgramData\StudySync"))
DB_PATH = Path(os.getenv("STUDYSYNC_DB_PATH", APP_DIR / "data" / "library.db"))
BACKUP_DIR = APP_DIR / "backups"


def _api_running() -> bool:
    import subprocess

    out = subprocess.run(
        ["sc", "query", "StudySyncAPI"],
        capture_output=True,
        text=True,
    ).stdout
    return "RUNNING" in out


def main() -> int:
    if not DB_PATH.exists():
        print(f"ERROR: database not found at {DB_PATH}")
        return 1

    if _api_running():
        print("ERROR: the StudySync API service is running.")
        print("  Run:  sc stop StudySyncAPI   then re-run this script.")
        return 2

    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if arg:
        zip_path = Path(arg)
        if not zip_path.exists():
            print(f"ERROR: {zip_path} not found")
            return 1
    else:
        backups = sorted(BACKUP_DIR.glob("studysync_*.zip"))
        if not backups:
            print(f"No backups found in {BACKUP_DIR}")
            return 1
        print("Available backups:")
        for i, b in enumerate(backups, 1):
            mtime = dt.datetime.fromtimestamp(b.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            size = b.stat().st_size // 1024
            print(f"  [{i}] {b.name}  ({mtime}, {size} KiB)")
        choice = input("Select backup number (or 0 to abort): ").strip()
        if not choice.isdigit() or int(choice) == 0:
            print("Aborted.")
            return 0
        idx = int(choice) - 1
        if not 0 <= idx < len(backups):
            print("Invalid choice.")
            return 1
        zip_path = backups[idx]

    db_backup = DB_PATH.with_suffix(DB_PATH.suffix + ".pre-restore")
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = [n for n in zf.namelist() if n.endswith(".db")]
            if not names:
                print("ERROR: no .db file inside the backup archive.")
                return 1
            member = names[0]

            shutil.copy2(DB_PATH, db_backup)
            for suffix in ("-wal", "-shm"):
                Path(str(DB_PATH) + suffix).unlink(missing_ok=True)

            with zf.open(member) as src, open(DB_PATH, "wb") as dst:
                shutil.copyfileobj(src, dst)

        # Verify the restored file opens cleanly.
        conn = sqlite3.connect(str(DB_PATH))
        try:
            conn.execute("PRAGMA integrity_check").fetchone()
        finally:
            conn.close()

        print(f"Restored {member} from {zip_path.name} -> {DB_PATH}")
        print(f"Previous database kept at: {db_backup}")
        print("Start the service with:  sc start StudySyncAPI")
        return 0
    except Exception as exc:  # noqa: BLE001
        if db_backup.exists():
            shutil.copy2(db_backup, DB_PATH)
            print("Restore failed; previous database restored automatically.")
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
