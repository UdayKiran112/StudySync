"""
database.py
-----------
Central place for SQLite connection handling.

Every connection MUST run `PRAGMA foreign_keys = ON` — SQLite disables
foreign key enforcement by default per-connection, so this is not optional.
Without it, your CHECK constraints tying account_type/subscription_id
together will still work, but FK ON DELETE RESTRICT will not.
"""

import logging
import os
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from datetime import date

logger = logging.getLogger("studysync.database")

# Path to the SQLite database file. Overridable via STUDYSYNC_DB_PATH so
# production deployments can keep data (and WAL files) in a separate data
# directory that survives application updates.
DB_PATH = Path(
    os.getenv("STUDYSYNC_DB_PATH", str(Path(__file__).parent / "library.db"))
)


def get_connection() -> sqlite3.Connection:
    """
    Create a new SQLite connection with the correct settings applied.
    Row factory is set to sqlite3.Row so query results can be accessed
    both by index and by column name (row["student_id"] or row[0]).
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def sync_student_statuses(conn) -> None:
    """
    Synchronise stored ``status`` columns to the current date so every
    read reflects whether the membership/subscription is still valid.

    Runs as a periodic background task (see main.py lifespan) instead of
    on every API connection.  This avoids two full-table UPDATE scans
    (students + subscriptions) plus an unconditional commit on every
    request, which was the single biggest per-request cost and a source
    of WAL writer lock contention under concurrent access.

    Attendance is the one deliberate exception: an expired student who
    shows up at the desk (or swipes the ZKTeco device) is auto-renewed
    on check-in instead of blocked -- see
    routers/students.py auto_renew_if_expired.
    """
    today = date.today().isoformat()
    conn.execute(
        """UPDATE students
        SET status = CASE WHEN date(join_date, '+' || (renewal_count + 1) || ' years') < ? THEN 'Inactive' ELSE 'Active' END
        WHERE status != CASE WHEN date(join_date, '+' || (renewal_count + 1) || ' years') < ? THEN 'Inactive' ELSE 'Active' END""",
        (today, today),
    )
    conn.execute(
        """UPDATE subscriptions
        SET status = CASE WHEN date(start_date, '+' || validity_days || ' days') < ? THEN 'Expired' ELSE 'Active' END
        WHERE validity_days IS NOT NULL
          AND start_date IS NOT NULL
          AND status != CASE WHEN date(start_date, '+' || validity_days || ' days') < ? THEN 'Expired' ELSE 'Active' END""",
        (today, today),
    )
    conn.commit()


def _status_sync_loop(interval: int = 30, stop_event=None) -> None:
    """
    Background thread that keeps stored statuses in sync with the current
    date.  Sleeps for *interval* seconds between passes.  Called from
    main.py lifespan so it runs for the lifetime of the process.
    """
    import time

    while True:
        try:
            conn = get_connection()
            try:
                sync_student_statuses(conn)
            finally:
                conn.close()
        except Exception:
            logger.exception("Background status sync failed; retrying next cycle.")
        if stop_event is not None and stop_event.wait(timeout=interval):
            break
        elif stop_event is None:
            time.sleep(interval)


def apply_runtime_schema_guards() -> None:
    """Add indexes that protect existing databases from duplicate open sessions."""
    with get_db() as conn:
        attendance_duplicates = conn.execute(
            """
            SELECT student_id FROM attendance WHERE check_out IS NULL
            GROUP BY student_id HAVING COUNT(*) > 1
            """
        ).fetchall()
        digital_duplicates = conn.execute(
            """
            SELECT student_id FROM digital_library_usage WHERE out_time IS NULL
            GROUP BY student_id HAVING COUNT(*) > 1
            """
        ).fetchall()
        if attendance_duplicates or digital_duplicates:
            raise RuntimeError(
                "Cannot add open-session safeguards while duplicate open sessions exist. "
                "Resolve duplicate student IDs first."
            )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_attendance_one_open_session
            ON attendance(student_id) WHERE check_out IS NULL
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_digital_one_open_session
            ON digital_library_usage(student_id) WHERE out_time IS NULL
            """
        )
        _ensure_device_ledger_tables(conn)
        _ensure_holidays_table(conn)
        _ensure_runtime_config_table(conn)


def _ensure_device_ledger_tables(conn) -> None:
    """
    Create the raw-punch ledger and device-state tables on databases that
    predate them (schema.sql now ships them for fresh installs). This is a
    pure additive migration: it never touches existing rows, and it is
    idempotent so it is safe to run on every startup.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS device_punches (
            punch_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            fingerprint    TEXT NOT NULL UNIQUE,
            device_serial  TEXT NOT NULL,
            user_id        TEXT NOT NULL,
            student_id     INTEGER,
            punch_time     TEXT NOT NULL,
            status_code    TEXT NOT NULL DEFAULT '',
            verify_method  TEXT NOT NULL DEFAULT '',
            source         TEXT NOT NULL,
            state          TEXT NOT NULL DEFAULT 'pending'
                           CHECK(state IN ('pending', 'applied', 'duplicate_transport',
                                           'duplicate_debounced', 'duplicate_session',
                                           'unknown_student')),
            raw_record     TEXT,
            captured_at    TEXT NOT NULL,
            applied_at     TEXT,
            FOREIGN KEY (student_id) REFERENCES students(student_id) ON DELETE RESTRICT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_device_punches_punch_time "
        "ON device_punches(punch_time)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_device_punches_student "
        "ON device_punches(student_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_device_punches_state "
        "ON device_punches(state)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS device_state (
            device_serial     TEXT PRIMARY KEY,
            last_seen_at      TEXT,
            last_reconcile_at TEXT,
            last_buffer_count INTEGER,
            ledger_pending    INTEGER,
            last_result       TEXT,
            buffer_capacity   INTEGER,
            buffer_status     TEXT,
            oldest_buffer_ts  TEXT,
            last_archive_path TEXT,
            last_archive_count INTEGER,
            last_clear_at     TEXT,
            clear_failures    INTEGER
        )
        """
    )
    # Additive migration: databases created before buffer management got
    # these columns need them added (idempotent; safe on every startup).
    _add_column_if_missing(conn, "device_state", "buffer_capacity", "buffer_capacity INTEGER")
    _add_column_if_missing(conn, "device_state", "buffer_status", "buffer_status TEXT")
    _add_column_if_missing(conn, "device_state", "oldest_buffer_ts", "oldest_buffer_ts TEXT")
    _add_column_if_missing(conn, "device_state", "last_archive_path", "last_archive_path TEXT")
    _add_column_if_missing(conn, "device_state", "last_archive_count", "last_archive_count INTEGER")
    _add_column_if_missing(conn, "device_state", "last_clear_at", "last_clear_at TEXT")
    _add_column_if_missing(conn, "device_state", "clear_failures", "clear_failures INTEGER")


def _ensure_holidays_table(conn) -> None:
    """
    Create the one-off holidays table on databases that predate it
    (schema.sql now ships it for fresh installs). Pure additive migration:
    idempotent, never touches existing rows.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS holidays (
            holiday_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            holiday_date DATE NOT NULL UNIQUE,
            name         TEXT NOT NULL CHECK(length(trim(name)) > 0),
            notes        TEXT,
            created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _ensure_runtime_config_table(conn) -> None:
    """
    Create the runtime-config key/value store on databases that predate it
    (schema.sql now ships it for fresh installs). Pure additive migration:
    idempotent, never touches existing rows.

    Holds small operator/system settings that must survive restarts but are
    NOT environment config (which lives in app\\api\\.env, read-only for the
    service account). Used by the ZKTeco discovery feature to remember the
    device's current IP across restarts / update swaps.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runtime_config (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def get_runtime_config(conn, key: str):
    """Value for a runtime-config key, or None when it isn't set."""
    row = conn.execute(
        "SELECT value FROM runtime_config WHERE key = ?", (key,)
    ).fetchone()
    return row["value"] if row else None


def set_runtime_config(conn, key: str, value: str) -> None:
    """Upsert a runtime-config value (caller commits the transaction)."""
    conn.execute(
        """
        INSERT INTO runtime_config (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (key, value),
    )


def delete_runtime_config(conn, key: str) -> None:
    """Remove a runtime-config key (caller commits the transaction)."""
    conn.execute("DELETE FROM runtime_config WHERE key = ?", (key,))


def _add_column_if_missing(conn, table: str, column: str, ddl: str) -> None:
    """Add a column to an existing table when it isn't already present."""
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def ensure_active_student(conn, student_id: int) -> None:
    """Raise 404 if *student_id* doesn't exist, 400 if its status is not Active.

    Shared by digital_library, offline_library, exams, quizzes, and any
    other router that gates write operations on an active student record.
    Centralising this avoids four identical copies that would otherwise
    drift independently.
    """
    from fastapi import HTTPException

    row = conn.execute(
        "SELECT student_id, status FROM students WHERE student_id = ?", (student_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Student {student_id} not found")
    if row["status"] != "Active":
        raise HTTPException(status_code=400, detail=f"Student {student_id} is inactive")


@contextmanager
def get_db():
    """
    FastAPI dependency-friendly context manager.
    Ensures the connection is always closed, and commits on success /
    rolls back on error automatically.

    Usage in a router:
        with get_db() as db:
            db.execute("SELECT * FROM students")
    """
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_db_dependency():
    """
    FastAPI dependency version (for use with Depends()).
    Yields a connection, commits on success, rolls back on error.
    """
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
