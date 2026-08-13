"""
database.py
-----------
Central place for SQLite connection handling.

Every connection MUST run `PRAGMA foreign_keys = ON` — SQLite disables
foreign key enforcement by default per-connection, so this is not optional.
Without it, your CHECK constraints tying account_type/subscription_id
together will still work, but FK ON DELETE RESTRICT will not.
"""

import os
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from datetime import date

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
    # Membership lasts one year from join_date, extended by one more year
    # per renewal (join_date itself never changes -- see routers/students.py
    # renew_student). This runs for every API connection so the stored
    # status always matches the validity formula, even between server
    # restarts. Attendance is the one deliberate exception: an expired
    # student who shows up at the desk (or swipes the ZKTeco device) is
    # auto-renewed on check-in instead of blocked -- see
    # routers/students.py auto_renew_if_expired.
    conn.execute(
        """UPDATE students
        SET status = CASE WHEN date(join_date, '+' || (renewal_count + 1) || ' years') < ? THEN 'Inactive' ELSE 'Active' END
        WHERE status != CASE WHEN date(join_date, '+' || (renewal_count + 1) || ' years') < ? THEN 'Inactive' ELSE 'Active' END""",
        (date.today().isoformat(), date.today().isoformat()),
    )
    # Same idea for subscriptions: valid until start_date + validity_days.
    # Only touches rows that actually HAVE a validity_days set AND a
    # start_date -- a subscription with no defined validity period
    # (validity_days IS NULL) or no start_date yet keeps whatever status
    # staff set manually, since there's no date math to base an automatic
    # decision on.
    conn.execute(
        """UPDATE subscriptions
        SET status = CASE WHEN date(start_date, '+' || validity_days || ' days') < ? THEN 'Expired' ELSE 'Active' END
        WHERE validity_days IS NOT NULL
          AND start_date IS NOT NULL
          AND status != CASE WHEN date(start_date, '+' || validity_days || ' days') < ? THEN 'Expired' ELSE 'Active' END""",
        (date.today().isoformat(), date.today().isoformat()),
    )
    # Commit the status refresh immediately so the connection does NOT start
    # inside a write transaction. Otherwise any caller that performs slow I/O
    # while holding this connection (e.g. a pyzk device read that can block
    # for ZK_DEVICE_TIMEOUT seconds) keeps the WAL writer lock for the whole
    # call, and every other connection fails with "database is locked" after
    # the 5s busy_timeout. Committing here just persists the same bookkeeping
    # the caller's final commit would have, so behaviour is unchanged for
    # everyone else.
    conn.commit()
    return conn


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


def _add_column_if_missing(conn, table: str, column: str, ddl: str) -> None:
    """Add a column to an existing table when it isn't already present."""
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


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
