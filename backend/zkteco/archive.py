r"""
zkteco/archive.py
-------------------
Dated offline archive of the device ATTLOG buffer.

When the device buffer fills past ZK_BUFFER_CLEAR_PERCENT (default 95) the
reconcile pass archives the whole buffer into a dated SQLite file named
``device_punches_YYYY-MM-DD.db`` (the day the buffer was archived) under
ZK_BUFFER_ARCHIVE_DIR (default ``<database folder>\device_punches``), then
clears the device. Each punch is keyed by the exact ledger fingerprint
(build_fingerprint), so a same-day refill -- a punch that landed during the
clear, a retried clear, a duplicate pass -- upserts into the same archive
file instead of duplicating rows.

Why a separate database: the main library.db stays small (the device_punches
ledger is pruned aggressively -- once the device buffer is cleared it can
never re-serve a punch, so its dedup rows are disposable), while the archive
keeps the raw record forever for the audit trail / a future re-import. One
file per day is also a clean unit of backup and cleanup.

Archive schema (created on demand):

    punches(fingerprint TEXT PRIMARY KEY, device_serial, user_id,
            punch_time, status_code, verify_method, raw_record)
    meta(key TEXT PRIMARY KEY, value TEXT)   -- serial, capacity, count,
            first/last punch, retrieved_at, cleared_at, archive_version
"""

import json
import logging
import os
import sqlite3
import threading
from datetime import date, datetime
from typing import List, Optional

from attendance_punch import build_fingerprint
from zkteco.config import buffer_archive_dir

logger = logging.getLogger("zkteco.archive")

# The reconcile worker and the manual clear endpoint can race writing
# today's archive file; a process-wide lock serializes them.
_write_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS punches (
    fingerprint    TEXT PRIMARY KEY,
    device_serial  TEXT NOT NULL,
    user_id        TEXT NOT NULL,
    punch_time     TEXT NOT NULL,
    status_code    TEXT NOT NULL DEFAULT '',
    verify_method  TEXT NOT NULL DEFAULT '',
    raw_record     TEXT
);
CREATE INDEX IF NOT EXISTS idx_archive_punch_time ON punches(punch_time);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def archive_path_for(day: Optional[str] = None) -> str:
    """Absolute path of the archive DB for the given day (default today)."""
    day = day or date.today().isoformat()
    return os.path.join(buffer_archive_dir(), f"device_punches_{day}.db")


def _open_archive(day: str) -> sqlite3.Connection:
    os.makedirs(buffer_archive_dir(), exist_ok=True)
    conn = sqlite3.connect(archive_path_for(day))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def write_archive(
    serial: str,
    logs: List[dict],
    *,
    capacity: int = 0,
    meta_extra: Optional[dict] = None,
) -> dict:
    """
    Archive the pulled ATTLOG records into today's dated archive file.

    Idempotent: rows are keyed by fingerprint and inserted with INSERT OR
    IGNORE, so a same-day re-archive of overlapping records is a no-op.
    Malformed records (no user_id / timestamp) are skipped, mirroring
    verify_pyzk_vs_db. Returns ``{"path", "count"}`` where ``count`` is the
    total punches in the archive file after this write. An empty input
    returns ``{"path": None, "count": 0}`` without touching the filesystem.
    """
    if not logs:
        return {"path": None, "count": 0}

    rows = []
    for log in logs:
        user_id = log.get("user_id")
        ts = log.get("timestamp")
        if user_id is None or not isinstance(ts, datetime):
            continue
        rows.append(
            (
                build_fingerprint(serial, user_id, ts, log.get("status")),
                serial,
                str(user_id).strip(),
                ts.strftime("%Y-%m-%d %H:%M:%S"),
                str(log.get("status")) if log.get("status") is not None else "",
                "",
                json.dumps(log, default=str),
            )
        )
    if not rows:
        return {"path": None, "count": 0}

    day = date.today().isoformat()
    path = archive_path_for(day)
    with _write_lock:
        conn = _open_archive(day)
        try:
            conn.executemany(
                """
                INSERT OR IGNORE INTO punches
                    (fingerprint, device_serial, user_id, punch_time,
                     status_code, verify_method, raw_record)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            times = [row[3] for row in rows]
            meta = {
                "device_serial": serial,
                "capacity": str(capacity),
                "buffer_count": str(len(rows)),
                "first_punch": min(times),
                "last_punch": max(times),
                "retrieved_at": datetime.utcnow().isoformat(),
                "archive_version": "1",
            }
            meta.update(meta_extra or {})
            conn.executemany(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                list(meta.items()),
            )
            conn.commit()
            count = conn.execute("SELECT COUNT(*) FROM punches").fetchone()[0]
            return {"path": path, "count": count}
        finally:
            conn.close()


def mark_cleared(path: str, serial: str, cleared_at: str) -> None:
    """
    Record on the archive file that the device buffer was cleared after it
    was archived. Best-effort: a missing/foreign path is a no-op.
    """
    if not path or not os.path.exists(path):
        return
    conn = sqlite3.connect(path)
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            [("device_serial", serial), ("cleared_at", cleared_at)],
        )
        conn.commit()
    finally:
        conn.close()
