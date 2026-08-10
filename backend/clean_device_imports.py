"""
clean_device_imports.py
-----------------------
Go-live cleanup: remove the punch-derived data so StudySync starts fresh.

Run this as part of the "wipe the device buffer, then go live" rollout,
BEFORE pointing StudySync at the device again. It deletes:

  * device_state   -- per-device health bookkeeping
  * device_punches -- the raw-punch exactly-once ledger (every row here is
                      a device record; nothing else writes to it)
  * attendance     -- rows that came from device punches (and, unless
                      --keep-manual, EVERY attendance row)

Default behaviour wipes the whole attendance table (matching the wiped
device buffer). With --keep-manual, attendance rows whose check_in /
check_out match a device punch for the same student+date are removed while
rows with no matching punch (i.e. front-desk manual entries) survive.

Stop the API (or at least unset ZK_DEVICE_IP) before running so no live /
poll / reconcile loop writes in the middle of the wipe.

Usage:
    python clean_device_imports.py --yes                 # full clean
    python clean_device_imports.py --keep-manual --yes   # keep desk entries

Exit codes: 0 = done, 1 = aborted (no --yes).
"""

import argparse
import sys

from database import get_connection


def _count(db, table: str) -> int:
    return db.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Delete device-punch-derived data from the StudySync database."
    )
    parser.add_argument(
        "--keep-manual",
        action="store_true",
        help="keep attendance rows with no matching device punch (desk entries)",
    )
    parser.add_argument(
        "--yes", action="store_true", help="confirm the destructive cleanup"
    )
    args = parser.parse_args()

    db = get_connection()
    try:
        device_punches = _count(db, "device_punches")
        attendance = _count(db, "attendance")
        device_state = _count(db, "device_state")

        print("Will delete:")
        print(f"  device_punches : {device_punches}")
        print(f"  device_state   : {device_state}")
        print(f"  attendance     : {attendance}")
        if args.keep_manual:
            print("  (attendance rows matching a device punch only)")

        if not args.yes:
            print()
            print("WARNING: this permanently deletes the rows above from")
            print("the StudySync database. Stop the API first, then re-run")
            print("with --yes to proceed, or CTRL-C to abort.")
            return 1

        if args.keep_manual:
            db.execute(
                """
                DELETE FROM attendance
                WHERE attendance_id IN (
                    SELECT a.attendance_id
                    FROM attendance a
                    WHERE EXISTS (
                        SELECT 1 FROM device_punches p
                        WHERE p.student_id = a.student_id
                          AND p.state IN ('applied', 'duplicate_debounced',
                                          'duplicate_session')
                          AND date(p.punch_time) = a.date
                          AND (strftime('%H:%M', p.punch_time) = a.check_in
                               OR strftime('%H:%M', p.punch_time) = a.check_out)
                    )
                )
                """
            )
        else:
            db.execute("DELETE FROM attendance")

        db.execute("DELETE FROM device_punches")
        db.execute("DELETE FROM device_state")
        db.commit()

        print("Done. Attendance now:", _count(db, "attendance"))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
