"""Tests for the dated ATTLOG archive (zkteco/archive.py).

Exercises write_archive() / mark_cleared() against real temp-directory
SQLite files (no hardware, no FastAPI). Run from the project root with:
    & .\\study_sync\\Scripts\\python.exe -m unittest discover -s backend/tests -v
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from zkteco.archive import (  # noqa: E402
    archive_path_for,
    mark_cleared,
    write_archive,
)

SERIAL = "SN-ARCHIVE-01"


def _today() -> str:
    return date.today().isoformat()


def _log(uid, dt, status="0"):
    return {"uid": uid, "user_id": str(uid), "timestamp": datetime.strptime(
        dt, "%Y-%m-%d %H:%M:%S"), "status": status}


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["ZK_BUFFER_ARCHIVE_DIR"] = self._tmp.name

    def tearDown(self):
        os.environ.pop("ZK_BUFFER_ARCHIVE_DIR", None)
        self._tmp.cleanup()

    def _read_punches(self):
        conn = sqlite3.connect(archive_path_for())
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM punches ORDER BY fingerprint"
            ).fetchall()
            meta = dict(
                conn.execute("SELECT key, value FROM meta ORDER BY key").fetchall()
            )
        finally:
            conn.close()
        return rows, meta

    def test_write_archive_creates_dated_file_with_all_rows(self):
        result = write_archive(
            SERIAL,
            [_log(1001, _today() + " 09:00:00"), _log(1001, _today() + " 17:00:00")],
            capacity=50000,
        )
        self.assertTrue(
            result["path"].endswith("device_punches_%s.db" % _today())
        )
        self.assertEqual(result["count"], 2)
        rows, meta = self._read_punches()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["device_serial"], SERIAL)
        self.assertEqual(rows[0]["punch_time"], _today() + " 09:00:00")
        self.assertEqual(rows[0]["user_id"], "1001")
        self.assertIsNotNone(rows[0]["raw_record"])
        self.assertEqual(meta["capacity"], "50000")
        self.assertEqual(meta["buffer_count"], "2")
        self.assertEqual(meta["first_punch"], _today() + " 09:00:00")
        self.assertEqual(meta["last_punch"], _today() + " 17:00:00")

    def test_write_archive_is_idempotent_on_same_day_refill(self):
        write_archive(SERIAL, [_log(1001, _today() + " 09:00:00")], capacity=50000)
        # Same-day re-archive overlapping the existing record: upsert, not dup.
        second = write_archive(
            SERIAL,
            [
                _log(1001, _today() + " 09:00:00"),
                _log(1001, _today() + " 12:30:00"),
            ],
            capacity=50000,
        )
        self.assertEqual(second["count"], 2)
        rows, _ = self._read_punches()
        self.assertEqual(len(rows), 2)
        timestamps = {r["punch_time"] for r in rows}
        self.assertEqual(
            timestamps,
            {_today() + " 09:00:00", _today() + " 12:30:00"},
        )

    def test_write_archive_skips_malformed_records(self):
        bad = [{"uid": 1, "user_id": None, "timestamp": None, "status": "0"}]
        result = write_archive(SERIAL, bad, capacity=50000)
        self.assertEqual(result["path"], None)
        self.assertEqual(result["count"], 0)

    def test_write_archive_empty_input_is_noop(self):
        result = write_archive(SERIAL, [], capacity=50000)
        self.assertEqual(result, {"path": None, "count": 0})

    def test_mark_cleared_records_cleared_at(self):
        result = write_archive(
            SERIAL, [_log(1001, _today() + " 09:00:00")], capacity=50000
        )
        mark_cleared(result["path"], SERIAL, "2026-08-13 10:00:00")
        rows, meta = self._read_punches()
        self.assertEqual(meta["cleared_at"], "2026-08-13 10:00:00")
        self.assertEqual(len(rows), 1)  # clear marker never touches punches

    def test_mark_cleared_missing_path_is_noop(self):
        mark_cleared("C:\\does\\not\\exist.db", SERIAL, "2026-08-13 10:00:00")


if __name__ == "__main__":
    unittest.main(verbosity=2)
