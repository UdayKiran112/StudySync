"""
backup.py
---------
Online-safe backup for the StudySync SQLite database.

Uses SQLite's online backup API (sqlite3.Connection.backup) so the copy is
always transactionally consistent even while the API service is writing.
The result is a timestamped .zip in the backups folder; old backups are
pruned (retention window).

Run by Windows Task Scheduler every night:
    python C:\\ProgramData\\StudySync\\scripts\\backup.py
"""

import datetime as dt
import os
import shutil
import sqlite3
import zipfile
from pathlib import Path

APP_DIR = Path(os.getenv("STUDYSYNC_APP_DIR", r"C:\ProgramData\StudySync"))
DB_PATH = Path(os.getenv("STUDYSYNC_DB_PATH", APP_DIR / "data" / "library.db"))
BACKUP_DIR = APP_DIR / "backups"
LOG_DIR = APP_DIR / "logs" / "backup"
RETENTION_DAYS = int(os.getenv("STUDYSYNC_BACKUP_RETENTION_DAYS", "30"))
# Minimum free space the backups drive must have (bytes) before a backup runs.
# Backups are pruned oldest-first until we are back above this; if that is not
# enough the backup is skipped with an ERROR so the disk can never fill up.
MIN_FREE_BYTES = int(os.getenv("STUDYSYNC_BACKUP_MIN_FREE_BYTES", str(1 * 1024**3)))
LOG_ROTATE_BYTES = int(os.getenv("STUDYSYNC_BACKUP_LOG_ROTATE_BYTES", str(1 * 1024**2)))


def log(msg: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{dt.datetime.now().isoformat(timespec='seconds')} | {msg}"
    print(line)
    log_file = LOG_DIR / "backup.log"
    if log_file.exists() and log_file.stat().st_size > LOG_ROTATE_BYTES:
        log_file.rename(log_file.with_suffix(".log.old"))
    with open(log_file, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def free_space_bytes() -> int:
    return shutil.disk_usage(BACKUP_DIR).free


def prune_to_min_free() -> int:
    """Delete oldest backups until free space is above MIN_FREE_BYTES.

    Returns the number of backups removed.
    """
    removed = 0
    while free_space_bytes() < MIN_FREE_BYTES:
        oldest = min(
            BACKUP_DIR.glob("studysync_*.zip"),
            key=lambda f: f.stat().st_mtime,
            default=None,
        )
        if oldest is None:
            break
        oldest.unlink()
        removed += 1
        log(f"Pruned {oldest.name} to keep free space above minimum")
    return removed


def main() -> int:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    if not DB_PATH.exists():
        log(f"SKIP: database not found at {DB_PATH}")
        return 1

    # 0) Disk-space guard: never let a backup fill the disk. Prune the oldest
    #    backups first; skip entirely if there still is not enough room.
    if free_space_bytes() < MIN_FREE_BYTES:
        log(f"WARNING: free space below {MIN_FREE_BYTES // (1024**3)} GiB; pruning old backups")
        prune_to_min_free()
    if free_space_bytes() < MIN_FREE_BYTES:
        log("ERROR: not enough free space even after pruning; backup skipped")
        return 4

    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    temp_copy = BACKUP_DIR / f"library_{stamp}.db"
    zip_path = BACKUP_DIR / f"studysync_{stamp}.zip"

    # 1) Consistent online copy via the backup API.
    try:
        src = sqlite3.connect(str(DB_PATH))
        dst = sqlite3.connect(str(temp_copy))
        with dst:
            src.backup(dst)
        dst.close()
        src.close()
    except Exception as exc:  # noqa: BLE001
        log(f"ERROR: online backup failed: {exc}")
        return 2

    # 2) Zip it (compression is poor for SQLite but keeps everything in one file).
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(temp_copy, arcname=f"library_{stamp}.db")
    except Exception as exc:  # noqa: BLE001
        log(f"ERROR: zip failed: {exc}")
        return 3
    finally:
        temp_copy.unlink(missing_ok=True)

    size_kb = zip_path.stat().st_size // 1024
    log(f"OK: backup {zip_path.name} ({size_kb} KiB)")

    # 3) Prune old backups.
    cutoff = dt.datetime.now() - dt.timedelta(days=RETENTION_DAYS)
    removed = 0
    for f in BACKUP_DIR.glob("studysync_*.zip"):
        try:
            mtime = dt.datetime.fromtimestamp(f.stat().st_mtime)
            if mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            continue
    if removed:
        log(f"Pruned {removed} backup(s) older than {RETENTION_DAYS} days")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
