"""Unit tests for the exactly-once device-punch pipeline.

Exercises capture_and_apply()/apply_punch() directly against an in-memory
SQLite database (no HTTP, no hardware). Run from the project root with:
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


def _dt(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


class AttendancePunchTests(unittest.TestCase):
    def setUp(self):
        os.environ["ZK_PUNCH_DEBOUNCE_MINUTES"] = "1"
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript((BACKEND_DIR / "schema.sql").read_text(encoding="utf-8"))
        self.db.execute(
            "INSERT INTO students (student_id, name, gender, join_date, status) "
            "VALUES (?, ?, ?, ?, ?)",
            (1001, "Punch Unit", "Female", "2026-01-01", "Active"),
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def _punch(self, uid, dt, serial="SN-TEST-01", status="0", source="pyzk_poll"):
        return attendance_punch.capture_and_apply(
            self.db, serial, uid, _dt(dt), status, "0", None, source
        )

    def _attendance(self, student_id=1001, day="2026-06-10"):
        rows = self.db.execute(
            "SELECT * FROM attendance WHERE student_id = ? AND date = ? "
            "ORDER BY check_in",
            (student_id, day),
        ).fetchall()
        return [dict(r) for r in rows]

    def _ledger(self, day="2026-06-10"):
        rows = self.db.execute(
            "SELECT * FROM device_punches WHERE punch_time LIKE ? ORDER BY punch_id",
            (day + "%",),
        ).fetchall()
        return [dict(r) for r in rows]

    # --- session semantics -------------------------------------------------

    def test_01_two_punches_make_one_full_day(self):
        self.assertEqual(self._punch(1001, "2026-06-10 09:00:00")["outcome"], "checked_in")
        self.assertEqual(self._punch(1001, "2026-06-10 17:00:00")["outcome"], "checked_out")
        rows = self._attendance()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["session"], "Full Day")
        self.assertEqual(rows[0]["check_in"], "09:00")
        self.assertEqual(rows[0]["check_out"], "17:00")
        self.assertEqual(rows[0]["duration_minutes"], 420)  # lunch 13:00-14:00 excluded

    def test_02_four_punches_make_two_sessions(self):
        for dt in ["2026-06-10 09:00:00", "2026-06-10 11:00:00",
                   "2026-06-10 15:00:00", "2026-06-10 17:00:00"]:
            self._punch(1001, dt)
        rows = self._attendance()
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["session"] for r in rows], ["Morning", "Afternoon"])
        self.assertEqual(rows[0]["check_in"], "09:00")
        self.assertEqual(rows[0]["check_out"], "11:00")
        self.assertEqual(rows[0]["duration_minutes"], 120)
        self.assertEqual(rows[1]["check_in"], "15:00")
        self.assertEqual(rows[1]["check_out"], "17:00")
        self.assertEqual(rows[1]["duration_minutes"], 120)

    def test_03_morning_only_session(self):
        self._punch(1001, "2026-06-10 09:00:00")
        self._punch(1001, "2026-06-10 12:00:00")
        rows = self._attendance()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["session"], "Morning")
        self.assertEqual(rows[0]["duration_minutes"], 180)

    def test_04_afternoon_only_session(self):
        self._punch(1001, "2026-06-10 14:30:00")
        self._punch(1001, "2026-06-10 17:00:00")
        rows = self._attendance()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["session"], "Afternoon")
        self.assertEqual(rows[0]["duration_minutes"], 150)

    def test_05_three_punches_leave_an_open_afternoon(self):
        for dt in ["2026-06-10 09:00:00", "2026-06-10 11:00:00", "2026-06-10 15:00:00"]:
            self._punch(1001, dt)
        rows = self._attendance()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["session"], "Morning")
        self.assertEqual(rows[0]["check_out"], "11:00")
        self.assertEqual(rows[1]["session"], "Afternoon")
        self.assertIsNone(rows[1]["check_out"])

    def test_06_stale_open_session_closed_before_new_checkin(self):
        self._punch(1001, "2026-06-09 09:00:00")
        self._punch(1001, "2026-06-10 09:00:00")
        prev = self._attendance(day="2026-06-09")
        cur = self._attendance(day="2026-06-10")
        self.assertEqual(prev[0]["check_out"], "23:59")
        self.assertEqual(len(cur), 1)
        self.assertEqual(cur[0]["check_in"], "09:00")
        self.assertIsNone(cur[0]["check_out"])

    # --- double-tap debounce ------------------------------------------------

    def test_07_double_tap_after_checkin_is_not_a_checkout(self):
        self.assertEqual(self._punch(1001, "2026-06-10 09:00:00")["outcome"], "checked_in")
        result = self._punch(1001, "2026-06-10 09:00:30")
        self.assertEqual(result["outcome"], "duplicate_debounced")
        rows = self._attendance()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["check_in"], "09:00")
        self.assertIsNone(rows[0]["check_out"])
        ledger = self._ledger()
        self.assertEqual(len(ledger), 2)
        self.assertEqual(ledger[0]["state"], "applied")
        self.assertEqual(ledger[1]["state"], "duplicate_debounced")
        self.assertEqual(ledger[1]["punch_time"], "2026-06-10 09:00:30")
        self.assertEqual(ledger[1]["raw_record"], None)

    def test_08_double_tap_after_checkout_does_not_reopen(self):
        self._punch(1001, "2026-06-10 09:00:00")
        self._punch(1001, "2026-06-10 11:00:00")
        result = self._punch(1001, "2026-06-10 11:00:20")
        self.assertEqual(result["outcome"], "duplicate_debounced")
        rows = self._attendance()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["check_out"], "11:00")
        self.assertEqual(len(self._ledger()), 3)

    def test_09_legitimate_ten_minute_separation_is_not_debounced(self):
        self._punch(1001, "2026-06-10 09:00:00")
        self.assertEqual(self._punch(1001, "2026-06-10 09:10:00")["outcome"], "checked_out")
        self._punch(1001, "2026-06-10 15:00:00")
        self._punch(1001, "2026-06-10 17:00:00")
        rows = self._attendance()
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["duration_minutes"] for r in rows], [10, 120])

    def test_10_debounce_zero_disables_the_guard(self):
        os.environ["ZK_PUNCH_DEBOUNCE_MINUTES"] = "0"
        self._punch(1001, "2026-06-10 09:00:00")
        result = self._punch(1001, "2026-06-10 09:01:00")
        self.assertEqual(result["outcome"], "checked_out")
        rows = self._attendance()
        self.assertEqual(rows[0]["check_out"], "09:01")

    # --- exactly-once transport deduplication --------------------------------

    def test_11_same_punch_via_two_transports_claimed_once(self):
        first = attendance_punch.capture_and_apply(
            self.db, "SN-TEST-01", 1001, _dt("2026-06-10 09:00:00"), "0", "0", None, "pyzk_poll"
        )
        second = attendance_punch.capture_and_apply(
            self.db, "SN-TEST-01", 1001, _dt("2026-06-10 09:00:00"), "0", "0", None, "pyzk_live"
        )
        self.assertEqual(first["outcome"], "checked_in")
        self.assertEqual(second["outcome"], "duplicate_transport")
        ledger = self._ledger()
        self.assertEqual(len(ledger), 1)  # one ledger row, two sightings
        self.assertEqual(ledger[0]["state"], "applied")
        self.assertEqual(ledger[0]["source"], "pyzk_poll, pyzk_live")
        rows = self._attendance()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["check_in"], "09:00")
        self.assertIsNone(rows[0]["check_out"])

    def test_12_same_punch_replayed_by_device_is_a_duplicate(self):
        self._punch(1001, "2026-06-10 09:00:00")
        result = self._punch(1001, "2026-06-10 09:00:00")
        self.assertEqual(result["outcome"], "duplicate_transport")
        self.assertEqual(len(self._attendance()), 1)

    def test_13_unknown_user_is_never_fabricated(self):
        result = self._punch(99999, "2026-06-10 09:00:00")
        self.assertEqual(result["outcome"], "unknown_student")
        self.assertEqual(self._attendance(), [])
        ledger = self._ledger()
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0]["state"], "unknown_student")
        self.assertIsNone(ledger[0]["student_id"])

    def test_14_raw_record_is_preserved_in_ledger(self):
        raw = "4351\t2026-06-10 09:00:00\t0\t1\t\t0\t0\r\n"
        result = attendance_punch.capture_and_apply(
            self.db, "SN-TEST-01", 1001, _dt("2026-06-10 09:00:00"),
            "0", "1", raw, "pyzk_poll",
        )
        self.assertEqual(result["outcome"], "checked_in")
        self.assertEqual(self._ledger()[0]["raw_record"], raw)


if __name__ == "__main__":
    unittest.main(verbosity=2)
