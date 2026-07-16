"""
init_db.py
----------
Run this ONCE to create library.db from schema.sql.

    python init_db.py

If library.db already exists, this script will refuse to run and tell you
so — to prevent accidentally wiping your data. Delete library.db manually
first if you really want to start fresh.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "library.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def main():
    if DB_PATH.exists():
        print(f"'{DB_PATH.name}' already exists. Refusing to overwrite.")
        print("Delete it manually first if you want to start fresh.")
        return

    if not SCHEMA_PATH.exists():
        print(f"Could not find schema.sql at {SCHEMA_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")

    with open(SCHEMA_PATH, "r") as f:
        schema_script = f.read()

    conn.executescript(schema_script)
    conn.commit()
    conn.close()

    print(f"Database created successfully at: {DB_PATH}")


if __name__ == "__main__":
    main()
