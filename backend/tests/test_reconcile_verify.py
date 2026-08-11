"""Tests for reconcile-time verification of pyzk records vs. database writes.

verify_pyzk_vs_db() (zkteco/reconcile.py) confirms that every record pulled
from the device ended up as a durable device_punches ledger row. A row in
state 'pending' is NOT a mismatch: under the session completion rule that is
a past-day lone check-in legitimately awaiting its check-out punch. These
tests exercise it against an in-memory database with no device.

Run from the project root with:
    & .\\study_sync\\Scripts\\python.exe -m unittest discover -s backend/tests -v
"""

import os
import sqlite3
import sys
import unittest
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import attendance_punch  # noqa: E402
from zkteco.reconcile import verify_pyzk_vs_db  # noqa: E402


def _dt(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


class ReconcileVerifyTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript((BACKEND_DIR / "schema.sql").read_text(encoding="utf-8"))
        self.db.execute(
            "INSERT INTO students (student_id, name, gender, join_date, status) "
            "VALUES (1001, 'Verify Unit', 'Female', '2026-01-01', 'Active')"
        )
        self.db.commit()
        self.serial = "SN-VERIFY-01"

    def tearDown(self):
        self.db.close()

    def _log(self, uid, dt, status="0"):
        return {"uid": 1, "user_id": str(uid), "timestamp": _dt(dt), "status": status}

    def _apply(self, log, source="reconcile"):
        return attendance_punch.capture_and_apply(
            self.db, self.serial, log["user_id"], log["timestamp"],
            log["status"], "", None, source,
        )

    def test_all_records_have_durable_writes(self):
        logs = [self._log(1001, "2026-06-10 09:00:00"),
                self._log(1001, "2026-06-10 17:00:00")]
        for log in logs:
            self._apply(log)
        report = verify_pyzk_vs_db(self.db, logs, self.serial)
        self.assertEqual(report["verified"], 2)
        self.assertEqual(report["issue_count"], 0)
        self.assertEqual(report["issues"], [])

    def test_missing_ledger_row_is_a_mismatch(self):
        # A record pulled from the device with no corresponding write at all.
        logs = [self._log(1001, "2026-06-10 09:00:00")]
        report = verify_pyzk_vs_db(self.db, logs, self.serial)
        self.assertEqual(report["verified"], 0)
        self.assertEqual(report["issue_count"], 1)
        self.assertEqual(report["issues"][0]["issue"], "no ledger row written")

    def test_pending_ledger_row_is_not_a_mismatch(self):
        # A ledger row in 'pending' is the legitimate state of a past-day
        # lone check-in whose attendance row has not materialized yet (the
        # session completion rule). Its write IS durable -- the record was
        # captured into the ledger -- so it verifies as healthy, not as a bug.
        log = self._log(1001, "2026-06-10 09:00:00")
        fingerprint = attendance_punch.build_fingerprint(
            self.serial, log["user_id"], log["timestamp"], log["status"]
        )
        self.db.execute(
            """
            INSERT INTO device_punches
                (fingerprint, device_serial, user_id, punch_time,
                 status_code, source, captured_at)
            VALUES (?, ?, ?, ?, '', 'reconcile', ?)
            """,
            (fingerprint, self.serial, log["user_id"],
             "2026-06-10 09:00:00", "2026-06-10 08:00:00"),
        )
        self.db.commit()
        report = verify_pyzk_vs_db(self.db, [log], self.serial)
        self.assertEqual(report["verified"], 1)
        self.assertEqual(report["issue_count"], 0)
        self.assertEqual(report["issues"], [])

    def test_malformed_record_is_reported(self):
        logs = [{"uid": 1, "user_id": None, "timestamp": None, "status": "0"}]
        report = verify_pyzk_vs_db(self.db, logs, self.serial)
        self.assertEqual(report["verified"], 0)
        self.assertEqual(report["issue_count"], 1)
        self.assertEqual(report["issues"][0]["issue"], "malformed device record")

    def test_duplicate_transport_records_still_verify(self):
        # Same physical punch delivered by two transports: the second is a
        # duplicate_transport, but its ledger row is durable -- it must count
        # as a verified DB write, not a mismatch.
        log = self._log(1001, "2026-06-10 09:00:00")
        self.assertEqual(self._apply(log, source="pyzk_poll")["outcome"], "checked_in")
        self.assertEqual(self._apply(log, source="reconcile")["outcome"], "duplicate_transport")
        report = verify_pyzk_vs_db(self.db, [log], self.serial)
        self.assertEqual(report["verified"], 1)
        self.assertEqual(report["issue_count"], 0)

    def test_unknown_student_records_still_verify(self):
        # A PIN with no matching student is ledged as unknown_student -- still
        # a durable write of the device record, so it verifies.
        log = self._log(99999, "2026-06-10 09:00:00")
        self.assertEqual(self._apply(log)["outcome"], "unknown_student")
        report = verify_pyzk_vs_db(self.db, [log], self.serial)
        self.assertEqual(report["verified"], 1)
        self.assertEqual(report["issue_count"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
