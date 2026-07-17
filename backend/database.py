"""
database.py
-----------
Central place for SQLite connection handling.

Every connection MUST run `PRAGMA foreign_keys = ON` — SQLite disables
foreign key enforcement by default per-connection, so this is not optional.
Without it, your CHECK constraints tying account_type/subscription_id
together will still work, but FK ON DELETE RESTRICT will not.
"""

import sqlite3
from pathlib import Path
from contextlib import contextmanager

# Path to the SQLite database file. Lives next to this file.
DB_PATH = Path(__file__).parent / "library.db"


def get_connection() -> sqlite3.Connection:
    """
    Create a new SQLite connection with the correct settings applied.
    Row factory is set to sqlite3.Row so query results can be accessed
    both by index and by column name (row["student_id"] or row[0]).
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
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
