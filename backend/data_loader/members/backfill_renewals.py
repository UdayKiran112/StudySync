#!/usr/bin/env python3
"""
Replay attendance auto-renewal math to backfill students.renewal_count.

The live attendance gateway (routers/students.py auto_renew_if_expired)
bumps renewal_count when a student physically checks in, but the data
loader has never replayed that logic. A freshly built database therefore
lands with every student at renewal_count = 0, and database.py's
per-connection status sync then flips long-standing members to 'Inactive'
even though their attendance history shows they kept coming back.

This step runs once, after attendance is loaded, and replays the same
whole-year math -- membership validity is always

    valid_until = join_date + (renewal_count + 1) whole years

-- using each student's MOST RECENT attendance date as the stand-in for
their last check-in. renewal_count is raised just enough (capped at the
same MAX_AUTO_RENEWS the runtime uses) for valid_until to cover that
date, which is exactly what the runtime would have done at the moment of
the check-in.

A student whose last attendance falls within RECENT_ATTENDANCE_WINDOW_DAYS
of today is treated as a CURRENT member: their membership is extended to
cover today (not just the last attendance date), so a join-date-anniversary
edge case can't mark an actively-attending student 'Inactive' a few days
after their most recent visit. Status is then reconciled against today with
the same CASE formula database.py applies on every API connection, so the
on-disk state matches what the API would compute without any server
round-trip.

Only attendance-bearing students are ever renewed; a student with no
attendance history keeps renewal_count 0.
"""

import argparse
import sqlite3
from datetime import date, timedelta
from pathlib import Path

# Mirrors routers/students.py MAX_AUTO_RENEWS: one whole-year step per
# check-in is far more than any real membership needs, but cap it anyway so
# a pathological join_date (e.g. a 1990s data-entry typo) cannot balloon
# renewal_count in a runaway loop.
MAX_AUTO_RENEWS = 365

# A student who attended within this many days of the backfill run is a
# current member: their membership is extended to cover today.
RECENT_ATTENDANCE_WINDOW_DAYS = 90


def add_years(iso_date: str, n: int) -> date:
    """join_date + n whole years, clamped to Feb 28 in non-leap years."""
    d = date.fromisoformat(iso_date)
    try:
        return d.replace(year=d.year + n)
    except ValueError:  # Feb 29 in a non-leap year
        return d.replace(year=d.year + n, day=28)


def renewals_needed(join_date: str, last_attendance: str) -> int:
    """
    Smallest renewal_count such that
    join_date + (renewal_count + 1) years >= last_attendance.
    """
    needed = 0
    while add_years(join_date, needed + 1) < date.fromisoformat(last_attendance):
        needed += 1
    return needed


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, type=Path)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    today = date.today().isoformat()
    recent_cutoff = date.today() - timedelta(days=RECENT_ATTENDANCE_WINDOW_DAYS)

    last_attendance = {
        r["student_id"]: r["last_date"]
        for r in conn.execute(
            "SELECT student_id, MAX(date) AS last_date FROM attendance GROUP BY student_id"
        ).fetchall()
    }

    students = conn.execute(
        "SELECT student_id, join_date, renewal_count FROM students"
    ).fetchall()

    renewed = 0
    recent_renewed = 0
    capped = 0
    for row in students:
        last = last_attendance.get(row["student_id"])
        if last is None or last < row["join_date"]:
            continue
        last_d = date.fromisoformat(last)
        if last_d >= recent_cutoff:
            # Current member -- extend to cover today so the membership
            # can't lapse between the most recent visit and this run.
            needed = renewals_needed(row["join_date"], today)
            recent_renewed += 1
        else:
            needed = renewals_needed(row["join_date"], last)
        if needed > row["renewal_count"]:
            new_count = min(needed, MAX_AUTO_RENEWS)
            if new_count != needed:
                capped += 1
            conn.execute(
                "UPDATE students SET renewal_count = ? WHERE student_id = ?",
                (new_count, row["student_id"]),
            )
            renewed += 1

    # Reconcile status vs today with the exact formula database.py uses.
    conn.execute(
        """UPDATE students
        SET status = CASE
            WHEN date(join_date, '+' || (renewal_count + 1) || ' years') < ?
            THEN 'Inactive' ELSE 'Active' END
        """,
        (today,),
    )
    conn.commit()

    counts = {
        r["status"]: r["n"]
        for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM students GROUP BY status"
        ).fetchall()
    }
    print(f"Students with renewal_count raised: {renewed}")
    print(f"Current-members (attended within {RECENT_ATTENDANCE_WINDOW_DAYS} days): {recent_renewed}")
    print(f"At MAX_AUTO_RENEWS cap: {capped}")
    print(f"Status after backfill: {counts}")
    conn.close()


if __name__ == "__main__":
    main()
