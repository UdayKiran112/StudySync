"""Unit tests for the exactly-once device-punch pipeline.

Exercises capture_and_apply()/apply_punch() directly against an in-memory
SQLite database (no HTTP, no hardware). Run from the project root with:
    & .\\study_sync\\Scripts\\python.exe -m unittest discover -s backend/tests -v

The hybrid punch model is exercised on BOTH paths:

  * TODAY's swipe is live presence: the first punch opens an attendance row
    immediately (check_in set, check_out NULL) so the student shows as
    present; the next punch closes it.
  * A PAST day only materializes an attendance row when its check-out punch
    lands -- a lone past-day check-in stays 'pending' in the device_punches
    ledger and never produces an open attendance row.

Fixed dates like 2026-06-10 are PAST days; ``today`` is the real current
date, which exercises the live-presence path.
"""

import os
import sqlite3
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import attendance_punch  # noqa: E402


def _dt(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def _today() -> str:
    return date.today().isoformat()


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

    # --- past days: session completion rule --------------------------------

    def test_01_two_punches_make_one_full_day(self):
        self.assertEqual(self._punch(1001, "2026-06-10 09:00:00")["outcome"], "checked_in")
        self.assertEqual(self._punch(1001, "2026-06-10 17:00:00")["outcome"], "checked_out")
        rows = self._attendance()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["session"], "Full Day")
        self.assertEqual(rows[0]["check_in"], "09:00")
        self.assertEqual(rows[0]["check_out"], "17:00")
        self.assertEqual(rows[0]["duration_minutes"], 420)  # lunch 13:00-14:00 excluded
        self.assertEqual([r["state"] for r in self._ledger()], ["applied", "applied"])

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
        self.assertEqual(
            [r["state"] for r in self._ledger()],
            ["applied", "applied", "applied", "applied"],
        )

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

    def test_05_past_lone_checkin_stays_pending_until_checkout(self):
        # 09:00 + 11:00 complete a Morning; the 15:00 check-in is a lone odd
        # punch that under the session completion rule produces NO attendance
        # row -- it stays 'pending' in the ledger as an open check-in waiting
        # for its check-out.
        for dt in ["2026-06-10 09:00:00", "2026-06-10 11:00:00", "2026-06-10 15:00:00"]:
            self._punch(1001, dt)
        rows = self._attendance()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["session"], "Morning")
        self.assertEqual(
            [r["state"] for r in self._ledger()],
            ["applied", "applied", "pending"],
        )

        # When the check-out punch lands, the pair materializes as one row
        # and BOTH ledger punches become applied.
        self.assertEqual(
            self._punch(1001, "2026-06-10 17:00:00")["outcome"], "checked_out"
        )
        rows = self._attendance()
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["session"] for r in rows], ["Morning", "Afternoon"])
        self.assertEqual(rows[1]["check_in"], "15:00")
        self.assertEqual(rows[1]["check_out"], "17:00")
        self.assertEqual(
            [r["state"] for r in self._ledger()],
            ["applied", "applied", "applied", "applied"],
        )

    # --- today: live presence ----------------------------------------------

    def test_06_today_single_punch_opens_row_immediately(self):
        # A swipe happening NOW is live presence: the row is opened right
        # away so the student shows as present, even before check-out.
        day = _today()
        result = self._punch(1001, f"{day} 09:00:00")
        self.assertEqual(result["outcome"], "checked_in")
        rows = self._attendance(day=day)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["check_in"], "09:00")
        self.assertIsNone(rows[0]["check_out"])
        self.assertEqual(self._ledger(day)[0]["state"], "pending")

    def test_07_today_next_punch_closes_the_open_row(self):
        day = _today()
        self._punch(1001, f"{day} 09:00:00")
        self.assertEqual(self._punch(1001, f"{day} 17:00:00")["outcome"], "checked_out")
        rows = self._attendance(day=day)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["session"], "Full Day")
        self.assertEqual(rows[0]["check_in"], "09:00")
        self.assertEqual(rows[0]["check_out"], "17:00")
        self.assertEqual(rows[0]["duration_minutes"], 420)
        # Both ledger punches applied: the open check-in (pending) and the
        # closing check-out.
        self.assertEqual([r["state"] for r in self._ledger(day)], ["applied", "applied"])

    def test_08_today_double_tap_after_checkin_is_not_a_checkout(self):
        day = _today()
        self.assertEqual(self._punch(1001, f"{day} 09:00:00")["outcome"], "checked_in")
        result = self._punch(1001, f"{day} 09:00:30")
        self.assertEqual(result["outcome"], "duplicate_debounced")
        rows = self._attendance(day=day)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["check_in"], "09:00")
        self.assertIsNone(rows[0]["check_out"])
        ledger = self._ledger(day)
        self.assertEqual(len(ledger), 2)
        self.assertEqual(ledger[0]["state"], "pending")  # still the open check-in
        self.assertEqual(ledger[1]["state"], "duplicate_debounced")
        self.assertEqual(ledger[1]["punch_time"], f"{day} 09:00:30")
        self.assertEqual(ledger[1]["raw_record"], None)

    def test_09_today_double_tap_after_checkout_does_not_reopen(self):
        day = _today()
        self._punch(1001, f"{day} 09:00:00")
        self._punch(1001, f"{day} 11:00:00")
        result = self._punch(1001, f"{day} 11:00:20")
        self.assertEqual(result["outcome"], "duplicate_debounced")
        rows = self._attendance(day=day)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["check_out"], "11:00")
        self.assertEqual(len(self._ledger(day)), 3)

    def test_10_today_open_row_left_overnight_closes_at_next_checkin(self):
        # Today's open row that never gets a check-out is closed at 23:59 of
        # its own day the next time the student checks in (tomorrow, which is
        # a PAST day to the punch engine).
        day = _today()
        self._punch(1001, f"{day} 09:00:00")
        next_day = (date.today() + timedelta(days=1)).isoformat()
        self.assertEqual(
            self._punch(1001, f"{next_day} 09:00:00")["outcome"], "checked_in"
        )
        prev = self._attendance(day=day)
        self.assertEqual(len(prev), 1)
        self.assertEqual(prev[0]["check_out"], "23:59")
        # The next-day check-in is a lone past-day punch: no attendance row,
        # just a pending ledger check-in.
        self.assertEqual(self._attendance(day=next_day), [])
        self.assertEqual(self._ledger(next_day)[0]["state"], "pending")

    # --- double-tap debounce (past days) -----------------------------------

    def test_11_legitimate_ten_minute_separation_is_not_debounced(self):
        self._punch(1001, "2026-06-10 09:00:00")
        self.assertEqual(self._punch(1001, "2026-06-10 09:10:00")["outcome"], "checked_out")
        self._punch(1001, "2026-06-10 15:00:00")
        self._punch(1001, "2026-06-10 17:00:00")
        rows = self._attendance()
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["duration_minutes"] for r in rows], [10, 120])

    def test_12_debounce_zero_disables_the_guard(self):
        os.environ["ZK_PUNCH_DEBOUNCE_MINUTES"] = "0"
        self._punch(1001, "2026-06-10 09:00:00")
        result = self._punch(1001, "2026-06-10 09:01:00")
        self.assertEqual(result["outcome"], "checked_out")
        rows = self._attendance()
        self.assertEqual(rows[0]["check_out"], "09:01")

    def test_25_past_debounced_double_tap_means_no_session_at_all(self):
        # The 6434-on-2024-09-03 pattern: 09:45 in / 12:40 out (Morning),
        # then 14:04 opens a pending Afternoon check-in and the 14:05 punch
        # is swallowed by the 1-minute debounce. The debounced punch can
        # never close a session, so the odd-count day contributes no
        # Afternoon row at all -- no open row to backfill either.
        for dt in ["2026-06-10 09:45:00", "2026-06-10 12:40:00",
                   "2026-06-10 14:04:00", "2026-06-10 14:05:00"]:
            self._punch(1001, dt)
        rows = self._attendance()
        self.assertEqual(len(rows), 1)  # only the completed Morning
        self.assertEqual(rows[0]["session"], "Morning")
        self.assertEqual(
            [r["state"] for r in self._ledger()],
            ["applied", "applied", "pending", "duplicate_debounced"],
        )

    # --- exactly-once transport deduplication ------------------------------

    def test_13_same_punch_via_two_transports_claimed_once(self):
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
        self.assertEqual(ledger[0]["state"], "pending")  # open past-day check-in
        # The source records the FIRST sighting only: appending one tag per
        # re-sight would grow a row into tens of KB on a punch that stays
        # visible in the never-cleared device buffer.
        self.assertEqual(ledger[0]["source"], "pyzk_poll")
        self.assertEqual(self._attendance(), [])  # no row until a check-out lands

    def test_14_same_punch_replayed_by_device_is_a_duplicate(self):
        self._punch(1001, "2026-06-10 09:00:00")
        result = self._punch(1001, "2026-06-10 09:00:00")
        self.assertEqual(result["outcome"], "duplicate_transport")
        self.assertEqual(self._attendance(), [])

    def test_15_unknown_user_is_never_fabricated(self):
        result = self._punch(99999, "2026-06-10 09:00:00")
        self.assertEqual(result["outcome"], "unknown_student")
        self.assertEqual(self._attendance(), [])
        ledger = self._ledger()
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0]["state"], "unknown_student")
        self.assertIsNone(ledger[0]["student_id"])

    def test_16_raw_record_is_preserved_in_ledger(self):
        raw = "4351\t2026-06-10 09:00:00\t0\t1\t\t0\t0\r\n"
        result = attendance_punch.capture_and_apply(
            self.db, "SN-TEST-01", 1001, _dt("2026-06-10 09:00:00"),
            "0", "1", raw, "pyzk_poll",
        )
        self.assertEqual(result["outcome"], "checked_in")
        self.assertEqual(self._ledger()[0]["raw_record"], raw)

    def test_24_past_reread_before_checkout_is_a_duplicate(self):
        # A past-day open check-in re-delivered by a second transport before
        # its check-out lands is not a second check-in. The punch lands
        # exactly on the recorded one, so the debounce guard (diff = 0)
        # catches it first.
        self.assertEqual(
            self._punch(1001, "2026-06-10 09:00:00")["outcome"], "checked_in"
        )
        result = attendance_punch.capture_and_apply(
            self.db, "SN-TEST-02", 1001, _dt("2026-06-10 09:00:00"),
            "0", "0", None, "pyzk_poll",
        )
        self.assertEqual(result["outcome"], "duplicate_debounced")
        self.assertEqual(self._attendance(), [])

    # --- session conflict reconciliation (pre-existing/preloaded data) -------

    def test_17_dayend_promotion_conflict_is_reconciled_not_a_crash(self):
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

    def test_18_past_lone_checkin_outside_preexisting_span_stays_pending(self):
        # A past-day check-in that falls OUTSIDE a preloaded row's span is
        # genuine new presence, but under the session completion rule it does
        # not open an attendance row -- it registers a pending open check-in
        # and leaves the preloaded row untouched.
        self.db.execute(
            "INSERT INTO attendance (student_id, date, session, check_in, check_out, duration_minutes) "
            "VALUES (1001, '2026-06-09', 'Full Day', '09:00', '17:00', 420)"
        )
        self.db.commit()

        self.assertEqual(
            self._punch(1001, "2026-06-09 08:30:00")["outcome"], "checked_in"
        )
        prev = self._attendance(day="2026-06-09")
        self.assertEqual(len(prev), 1)  # preloaded row kept, no stale open
        self.assertEqual(prev[0]["check_out"], "17:00")
        self.assertEqual(self._ledger(day="2026-06-09")[0]["state"], "pending")

    def test_19_punch_inside_preexisting_span_is_covered_not_a_new_session(self):
        # A punch covered by an existing row's span is not new presence.
        self.db.execute(
            "INSERT INTO attendance (student_id, date, session, check_in, check_out, duration_minutes) "
            "VALUES (1001, '2026-06-10', 'Morning', '09:00', '12:00', 180)"
        )
        self.db.commit()

        result = self._punch(1001, "2026-06-10 09:30:00")
        self.assertEqual(result["outcome"], "duplicate")

        rows = self._attendance()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["check_in"], "09:00")
        self.assertEqual(rows[0]["check_out"], "12:00")

        ledger = self._ledger()
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0]["state"], "duplicate_session")

    def test_20_reconcile_is_idempotent_on_reread(self):
        # Same pre-existing Full Day row as test_17 (punches outside the span).
        self.db.execute(
            "INSERT INTO attendance (student_id, date, session, check_in, check_out, duration_minutes) "
            "VALUES (1001, '2026-06-10', 'Full Day', '09:40', '18:00', 440)"
        )
        self.db.commit()

        self._punch(1001, "2026-06-10 09:00:00")
        self._punch(1001, "2026-06-10 17:00:00")
        before = self._attendance()

        # A second transport re-reads the same day: the 09:00 punch is now
        # covered by the materialized 09:00-17:00 span (duplicate); the
        # 17:00 punch lands exactly on the last recorded punch, so the
        # debounce guard (diff = 0) catches it first. Neither path re-runs
        # the resolution and nothing changes.
        for dt, expected in [
            ("2026-06-10 09:00:00", "duplicate"),
            ("2026-06-10 17:00:00", "duplicate_debounced"),
        ]:
            result = attendance_punch.capture_and_apply(
                self.db, "SN-TEST-02", 1001, _dt(dt), "0", "0", None, "pyzk_poll"
            )
            self.assertEqual(result["outcome"], expected)

        self.assertEqual(self._attendance(), before)

    def test_21_punch_outside_preexisting_span_opens_a_real_session(self):
        # A punch that falls OUTSIDE every existing row's span is genuine new
        # presence and still becomes a session when its check-out lands.
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

    # --- ledger retention pruner ----------------------------------------------

    def test_26_ledger_retention_settings(self):
        saved = os.environ.get("ZK_LEDGER_RETENTION_DAYS")
        os.environ.pop("ZK_LEDGER_RETENTION_DAYS", None)
        try:
            self.assertEqual(attendance_punch.ledger_retention_days(), 30)
            os.environ["ZK_LEDGER_RETENTION_DAYS"] = "7"
            self.assertEqual(attendance_punch.ledger_retention_days(), 7)
            os.environ["ZK_LEDGER_RETENTION_DAYS"] = "garbage"
            self.assertEqual(attendance_punch.ledger_retention_days(), 30)
            os.environ["ZK_LEDGER_RETENTION_DAYS"] = "0"
            self.assertEqual(attendance_punch.ledger_retention_days(), 1)  # clamped
            from zkteco.config import ledger_retention_days as config_retention
            self.assertEqual(
                config_retention(), attendance_punch.ledger_retention_days()
            )
        finally:
            if saved is None:
                os.environ.pop("ZK_LEDGER_RETENTION_DAYS", None)
            else:
                os.environ["ZK_LEDGER_RETENTION_DAYS"] = saved

    def test_27_pruner_removes_old_rows_keeps_recent_and_today(self):
        # An applied pair far outside retention, one inside retention, and
        # today's live pair (pending open check-in + applied check-out).
        self._punch(1001, "2024-01-31 09:00:00")
        self._punch(1001, "2024-01-31 17:00:00")  # applied pair (2 rows)
        self._punch(1001, "2026-06-10 09:00:00")  # within the 90-day window
        self._punch(1001, "2026-06-10 17:00:00")  # applied pair (2 rows)
        today = _today()
        self._punch(1001, today + " 09:00:00")    # pending (live presence)
        self._punch(1001, today + " 17:00:00")    # applied

        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM device_punches").fetchone()[0], 6
        )

        deleted = attendance_punch.prune_old_ledger_rows(self.db, retention_days=90)
        self.assertEqual(deleted, 2)  # only the 2024-01-31 pair

        rows = self.db.execute(
            "SELECT punch_time, state FROM device_punches ORDER BY punch_id"
        ).fetchall()
        self.assertEqual(len(rows), 4)
        kept_days = {r["punch_time"][:10] for r in rows}
        self.assertEqual(kept_days, {"2026-06-10", today})
        today_rows = [r for r in rows if r["punch_time"][:10] == today]
        self.assertEqual(len(today_rows), 2)  # today's punches always survive
        self.assertTrue(all(r["state"] == "applied" for r in today_rows))

    def test_28_pruner_never_deletes_pending_open_checkin(self):
        self._punch(1001, "2024-01-31 09:00:00")  # lone past-day check-in -> pending
        self.assertEqual(
            self.db.execute("SELECT state FROM device_punches").fetchone()["state"],
            "pending",
        )
        deleted = attendance_punch.prune_old_ledger_rows(self.db, retention_days=30)
        self.assertEqual(deleted, 0)
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM device_punches").fetchone()[0], 1
        )

    def test_29_pruner_honors_batch_size_and_drains_backlog(self):
        for day in ["2024-01-28", "2024-01-29", "2024-01-30"]:
            self._punch(1001, day + " 09:00:00")
            self._punch(1001, day + " 17:00:00")  # 6 applied rows
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM device_punches").fetchone()[0], 6
        )
        total = 0
        while True:
            batch = attendance_punch.prune_old_ledger_rows(
                self.db, retention_days=30, batch_size=2
            )
            self.assertLessEqual(batch, 2)
            total += batch
            if batch == 0:
                break
        self.assertEqual(total, 6)
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM device_punches").fetchone()[0], 0
        )


    def test_30_pruner_uses_buffer_min_ts_when_device_still_holds_records(self):
        # When the device still holds records, every applied/duplicate row
        # older than the oldest one on the device is safe to prune (a cleared
        # buffer can never re-serve it; the archive holds the raw record).
        # Rows NEWER than buffer_min_ts must survive for exactly-once dedup.
        for day in ["2024-01-31", "2026-05-01", "2026-06-10"]:
            self._punch(1001, day + " 09:00:00")
            self._punch(1001, day + " 17:00:00")  # 6 applied rows
        deleted = attendance_punch.prune_old_ledger_rows(
            self.db, retention_days=30, buffer_min_ts="2026-06-10 09:00:00"
        )
        self.assertEqual(deleted, 4)  # 2024-01-31 pair + 2026-05-01 pair

        rows = self.db.execute(
            "SELECT punch_time FROM device_punches ORDER BY punch_id"
        ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["punch_time"].startswith("2026-06-10") for r in rows))

    def test_31_pruner_buffer_min_ts_never_deletes_pending_or_today(self):
        self._punch(1001, "2024-01-31 09:00:00")  # lone past-day check-in -> pending
        self._punch(1001, "2024-02-01 09:00:00")
        self._punch(1001, "2024-02-01 17:00:00")  # applied pair (2 rows)
        today = _today()
        self._punch(1001, today + " 09:00:00")  # pending (live presence)
        deleted = attendance_punch.prune_old_ledger_rows(
            self.db, retention_days=30, buffer_min_ts="2026-01-01 00:00:00"
        )
        # Only the applied pair is older than the device's oldest record; the
        # pending lone check-in and today's punches are always protected.
        self.assertEqual(deleted, 2)
        remaining = self.db.execute(
            "SELECT punch_time, state FROM device_punches ORDER BY punch_id"
        ).fetchall()
        self.assertEqual(len(remaining), 2)
        self.assertTrue(all(r["state"] == "pending" for r in remaining))
        self.assertTrue(any(r["punch_time"].startswith(today) for r in remaining))


if __name__ == "__main__":
    unittest.main(verbosity=2)
