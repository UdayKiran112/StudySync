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

    # --- session conflict reconciliation (pre-existing/preloaded data) -------

    def test_15_dayend_promotion_conflict_is_reconciled_not_a_crash(self):
        # Pre-existing/preloaded data: student already has a "Full Day" row.
        # The device re-reads the day with punches OUTSIDE that span (09:00
        # opens a Morning session, 17:00 closes it into "Full Day") -- the
        # 5729-style pattern that crashed the old poll.
        self.db.execute(
            "INSERT INTO attendance (student_id, date, session, check_in, check_out, duration_minutes) "
            "VALUES (1001, '2026-06-10', 'Full Day', '09:40', '18:00', 440)"
        )
        self.db.commit()

        with self.assertLogs("studysync.attendance_punch", level="WARNING") as logs:
            self.assertEqual(
                self._punch(1001, "2026-06-10 09:00:00")["outcome"], "checked_in"
            )
            self.assertEqual(
                self._punch(1001, "2026-06-10 17:00:00")["outcome"], "checked_out"
            )

        self.assertTrue(
            any("Session conflict reconciled" in line for line in logs.output),
            "expected a 'session conflict reconciled' warning",
        )

        rows = self._attendance()
        self.assertEqual(len(rows), 1)  # obsolete preloaded row replaced
        self.assertEqual(rows[0]["session"], "Full Day")
        self.assertEqual(rows[0]["check_in"], "09:00")
        self.assertEqual(rows[0]["check_out"], "17:00")
        self.assertEqual(rows[0]["duration_minutes"], 420)  # lunch 13:00-14:00 excluded

        ledger = self._ledger()
        self.assertEqual([r["state"] for r in ledger], ["applied", "applied"])

    def test_16_stale_open_promotion_conflict_keeps_closed_row(self):
        # Pre-existing: a closed Full Day row on 2026-06-09. The device
        # re-opens a Morning session for that day with an 08:30 punch that
        # falls OUTSIDE the preloaded span (so it is genuine new presence).
        self.db.execute(
            "INSERT INTO attendance (student_id, date, session, check_in, check_out, duration_minutes) "
            "VALUES (1001, '2026-06-09', 'Full Day', '09:00', '17:00', 420)"
        )
        self.db.commit()

        self.assertEqual(
            self._punch(1001, "2026-06-09 08:30:00")["outcome"], "checked_in"
        )
        # Next day's punch auto-closes the stale open; the 23:59 auto-close
        # would promote it to "Full Day" and collide with the pre-existing row.
        self.assertEqual(
            self._punch(1001, "2026-06-10 09:00:00")["outcome"], "checked_in"
        )

        prev = self._attendance(day="2026-06-09")
        self.assertEqual(len(prev), 1)  # closed row kept, stale open removed
        self.assertEqual(prev[0]["session"], "Full Day")
        self.assertEqual(prev[0]["check_out"], "17:00")

        cur = self._attendance(day="2026-06-10")
        self.assertEqual(len(cur), 1)
        self.assertEqual(cur[0]["check_in"], "09:00")
        self.assertIsNone(cur[0]["check_out"])

    def test_17_insert_collision_is_a_duplicate_not_a_crash(self):
        # Pre-existing: a closed Morning row already claims the session.
        self.db.execute(
            "INSERT INTO attendance (student_id, date, session, check_in, check_out, duration_minutes) "
            "VALUES (1001, '2026-06-10', 'Morning', '09:00', '12:00', 180)"
        )
        self.db.commit()

        result = self._punch(1001, "2026-06-10 09:30:00")
        self.assertEqual(result["outcome"], "duplicate")

        rows = self._attendance()
        self.assertEqual(len(rows), 1)  # no second Morning row
        self.assertEqual(rows[0]["check_in"], "09:00")
        self.assertEqual(rows[0]["check_out"], "12:00")

        ledger = self._ledger()
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0]["state"], "duplicate_session")

    def test_18_reconcile_is_idempotent_on_reread(self):
        # Same pre-existing Full Day row as test_15 (punches outside the span).
        self.db.execute(
            "INSERT INTO attendance (student_id, date, session, check_in, check_out, duration_minutes) "
            "VALUES (1001, '2026-06-10', 'Full Day', '09:40', '18:00', 440)"
        )
        self.db.commit()

        self._punch(1001, "2026-06-10 09:00:00")
        self._punch(1001, "2026-06-10 17:00:00")
        before = self._attendance()

        # A second transport re-reads the same day: the 09:00 punch is earlier
        # than the recorded 17:00 check-out, so it hits the re-read guard and
        # classifies as a duplicate; the 17:00 punch lands exactly on the last
        # recorded punch, so the debounce guard (diff = 0) catches it first.
        # Neither path re-runs the resolution and nothing changes.
        for dt, expected in [
            ("2026-06-10 09:00:00", "duplicate"),
            ("2026-06-10 17:00:00", "duplicate_debounced"),
        ]:
            result = attendance_punch.capture_and_apply(
                self.db, "SN-TEST-02", 1001, _dt(dt), "0", "0", None, "pyzk_poll"
            )
            self.assertEqual(result["outcome"], expected)

        self.assertEqual(self._attendance(), before)

    def test_19_punch_inside_preexisting_span_is_covered_not_a_new_session(self):
        # The 5729 single-punch pattern: the device re-reads a 09:41 punch on
        # a day whose preloaded "Full Day" row already spans 09:40 - 18:00.
        # The punch is already accounted for, so it must NOT open a second
        # (unclosable) Morning session.
        self.db.execute(
            "INSERT INTO attendance (student_id, date, session, check_in, check_out, duration_minutes) "
            "VALUES (1001, '2026-06-10', 'Full Day', '09:40', '18:00', 440)"
        )
        self.db.commit()

        result = self._punch(1001, "2026-06-10 09:41:00")
        self.assertEqual(result["outcome"], "duplicate")

        rows = self._attendance()
        self.assertEqual(len(rows), 1)  # only the preloaded Full Day row
        self.assertEqual(rows[0]["session"], "Full Day")
        self.assertEqual(rows[0]["check_in"], "09:40")
        self.assertEqual(rows[0]["check_out"], "18:00")

        ledger = self._ledger()
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0]["state"], "duplicate_session")

    def test_20_punch_outside_preexisting_span_opens_a_real_session(self):
        # A punch that falls OUTSIDE every existing row's span is genuine new
        # presence and must still open a session (normal behavior preserved).
        self.db.execute(
            "INSERT INTO attendance (student_id, date, session, check_in, check_out, duration_minutes) "
            "VALUES (1001, '2026-06-10', 'Morning', '09:00', '12:00', 180)"
        )
        self.db.commit()

        self.assertEqual(
            self._punch(1001, "2026-06-10 15:00:00")["outcome"], "checked_in"
        )
        self.assertEqual(
            self._punch(1001, "2026-06-10 17:00:00")["outcome"], "checked_out"
        )

        rows = self._attendance()
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["session"] for r in rows], ["Morning", "Afternoon"])
        self.assertEqual(rows[1]["check_in"], "15:00")
        self.assertEqual(rows[1]["check_out"], "17:00")

    # --- empty-entry backfill (last punch of the day as check-out) ----------

    def test_21_backfill_closes_row_whose_checkout_was_debounced(self):
        # The 6434-on-2024-09-03 pattern: 09:45 in / 12:40 out (Morning), then
        # 14:04 opens an Afternoon session and the 14:05 punch is swallowed by
        # the 1-minute debounce, leaving the Afternoon row empty forever.
        for dt in ["2026-06-10 09:45:00", "2026-06-10 12:40:00",
                   "2026-06-10 14:04:00", "2026-06-10 14:05:00"]:
            self._punch(1001, dt)
        rows = self._attendance()
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[1]["check_out"])

        closed = attendance_punch.backfill_empty_sessions(self.db)
        self.assertEqual(closed, 1)

        rows = self._attendance()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["session"], "Afternoon")
        self.assertEqual(rows[1]["check_in"], "14:04")
        self.assertEqual(rows[1]["check_out"], "14:05")
        self.assertEqual(rows[1]["duration_minutes"], 1)

    def test_22_backfill_single_punch_day_stays_open(self):
        # The 6020-on-2024-02-19 pattern: one 10:06 punch opens a Morning row
        # with no closing punch. The day's last punch IS the check-in, so there
        # is nothing truthful to backfill -- the row must stay open (the next
        # visit's 23:59 stale-close handles it), and the pass must not invent a
        # zero-length session.
        self.assertEqual(self._punch(1001, "2026-06-10 10:06:00")["outcome"], "checked_in")

        self.assertIsNone(
            attendance_punch.close_open_with_last_punch(self.db, 1001, "2026-06-10")
        )
        self.assertEqual(attendance_punch.backfill_empty_sessions(self.db), 0)

        rows = self._attendance()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["session"], "Morning")
        self.assertEqual(rows[0]["check_in"], "10:06")
        self.assertIsNone(rows[0]["check_out"])

    def test_23_backfill_uses_any_later_punch_even_when_not_applied(self):
        # Open Morning at 12:54 (single punch). The day's closing punch (16:20)
        # exists in the ledger as duplicate_session (a conflict swallowed it)
        # but was never applied. The backfill must still close the row with it
        # -- ledger state does not matter, only that a punch strictly later
        # than the check-in was recorded.
        self._punch(1001, "2026-06-10 12:54:00")
        rows = self._attendance()
        self.assertIsNone(rows[0]["check_out"])

        self.db.execute(
            """
            INSERT INTO device_punches
                (fingerprint, device_serial, user_id, student_id, punch_time,
                 status_code, source, state, captured_at)
            VALUES (?, ?, ?, ?, ?, '', 'reconcile', ?, ?)
            """,
            (
                "SN-TEST-01|1001|2026-06-10 16:20:00|0",
                "SN-TEST-01", "1001", 1001,
                "2026-06-10 16:20:00", "duplicate_session",
                "2026-06-10 08:00:00",
            ),
        )
        self.db.commit()

        self.assertEqual(attendance_punch.backfill_empty_sessions(self.db), 1)

        rows = self._attendance()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["session"], "Full Day")
        self.assertEqual(rows[0]["check_in"], "12:54")
        self.assertEqual(rows[0]["check_out"], "16:20")
        self.assertEqual(rows[0]["duration_minutes"], 146)  # 12:54-13:00 + 14:00-16:20

    def test_24_backfill_recomputes_session_and_resolves_conflict(self):
        # An open Morning row whose closing punch (17:00) was never applied
        # backfills into "Full Day" -- which collides with a pre-existing
        # Full Day row. The backfill must run the same conflict resolution as
        # apply_punch (keep the device-derived row, drop the preloaded one)
        # instead of crashing on UNIQUE(student_id, date, session).
        self.db.execute(
            "INSERT INTO attendance (student_id, date, session, check_in, check_out, duration_minutes) "
            "VALUES (1001, '2026-06-10', 'Full Day', '09:40', '18:00', 440)"
        )
        self.db.execute(
            "INSERT INTO attendance (student_id, date, session, check_in) "
            "VALUES (1001, '2026-06-10', 'Morning', '08:30')"
        )
        for hm, state in [("08:30:00", "applied"), ("17:00:00", "duplicate_session")]:
            self.db.execute(
                """
                INSERT INTO device_punches
                    (fingerprint, device_serial, user_id, student_id, punch_time,
                     status_code, source, state, captured_at)
                VALUES (?, ?, ?, ?, ?, '', 'reconcile', ?, ?)
                """,
                (
                    f"SN-TEST-01|1001|2026-06-10 {hm}|0",
                    "SN-TEST-01", "1001", 1001,
                    f"2026-06-10 {hm}", state, "2026-06-10 08:00:00",
                ),
            )
        self.db.commit()

        with self.assertLogs("studysync.attendance_punch", level="WARNING") as logs:
            self.assertEqual(attendance_punch.backfill_empty_sessions(self.db), 1)
        self.assertTrue(
            any("Session conflict reconciled" in line for line in logs.output),
            "expected a 'session conflict reconciled' warning from the backfill",
        )

        rows = self._attendance()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["session"], "Full Day")
        self.assertEqual(rows[0]["check_in"], "08:30")
        self.assertEqual(rows[0]["check_out"], "17:00")
        self.assertEqual(rows[0]["duration_minutes"], 450)  # 08:30-13:00 + 14:00-17:00


if __name__ == "__main__":
    unittest.main(verbosity=2)
