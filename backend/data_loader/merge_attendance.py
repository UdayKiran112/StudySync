#!/usr/bin/env python3
"""
merge_attendance.py
===================

Preserve the existing attendance + device-punch data across a pipeline
rebuild, then merge the freshly-loaded attendance with what the database
already had -- never overwriting, never duplicating.

The pipeline wipes and rebuilds backend/library.db from schema.sql on every
run (run_pipeline.py step 4), so anything not reproducible from the cleaned
CSVs is lost: most importantly attendance OLDER than the CSV window (the
cleaned attendance.csv only covers 2025-04-01 onward -- every 2024 row
exists only in the database) and every device_punches row (no loader ever
writes that table; it is populated live by the ZK fingerprint device).

This script is a two-step companion to run_pipeline.py:

    python merge_attendance.py --mode snapshot --db library.db --out backup.json
    python merge_attendance.py --mode merge    --db library.db --in  backup.json

Step 1 (SNAPSHOT -- run BEFORE the rebuild): copy the current attendance and
device_punches rows into a JSON backup.

Step 2 (MERGE -- run AFTER the section loaders): replay that backup into the
freshly-rebuilt database.

MERGE RULES (the existing database is the source of truth; the pipeline may
only ADD what is genuinely missing, never overwrite a stored timing):
  * (student_id, date, session) already present -> the SAME event; keep the
    existing row exactly as-is and only fill its NULL check_in/check_out/
    duration_minutes from the backup row (COALESCE, never overwrite a real
    value).
  * Same student + same date, different session, but check_in AND check_out
    each within TOLERANCE_MINUTES of one existing row -> the same event that
    got re-bucketed (e.g. Morning <-> Full Day after a re-derivation); keep
    the existing row, fill NULLs.
  * Same student + same date, and the backup row's window already sits inside
    the union of the existing rows' windows (a single old Full Day row now
    split into Morning + Afternoon) -> already covered; skip and log for
    review.
  * Anything else -> genuinely missing; INSERT it with the backup's exact
    stored values (including NULL check_out for an open session).

Device punches are restored verbatim; rows whose student_id no longer
resolves to a student are skipped and logged.

SNAPSHOT SAFETY
---------------
If the existing database already looks rebuilt (students exist but ZERO
attendance rows) AND a previous snapshot file is still on disk, the script
aborts instead of overwriting it -- that state means a previous pipeline run
died between the rebuild and the attendance load, and the old snapshot is
the only surviving copy of the real data.
"""

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import common

# Same-event tolerance in minutes: two records of the same student/date are
# considered the same visit when their check_in/check_out each fall within
# this many minutes of each other.
TOLERANCE_MINUTES = 2

ATTENDANCE_COLS = [
    "student_id",
    "date",
    "session",
    "check_in",
    "check_out",
    "duration_minutes",
]
DEVICE_COLS = [
    "fingerprint",
    "device_serial",
    "user_id",
    "student_id",
    "punch_time",
    "status_code",
    "verify_method",
    "source",
    "state",
    "raw_record",
    "captured_at",
    "applied_at",
]


def _to_min(hhmm):
    if not hhmm:
        return None
    try:
        h, m = str(hhmm).split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def _within(a, b, tol=TOLERANCE_MINUTES):
    if a is None or b is None:
        return True
    return abs(a - b) <= tol


def _same_event(snap, cand):
    """Same student/date whose check_in AND check_out are each within the
    tolerance -- the same visit, regardless of which session bucket either
    side landed in."""
    if snap["student_id"] != cand["student_id"] or snap["date"] != cand["date"]:
        return False
    if snap["check_in"] is not None or cand["check_in"] is not None:
        if not _within(_to_min(snap["check_in"]), _to_min(cand["check_in"])):
            return False
    if snap["check_out"] is not None or cand["check_out"] is not None:
        if not _within(_to_min(snap["check_out"]), _to_min(cand["check_out"])):
            return False
    return True


def _covered(snap, other_rows):
    """True when the backup window already sits inside the union of the
    existing rows' windows for that student/date -- e.g. one old Full Day row
    now represented by separate Morning + Afternoon rows."""
    ins = [r["check_in"] for r in other_rows if r["check_in"] is not None]
    outs = [r["check_out"] for r in other_rows if r["check_out"] is not None]
    if not ins or not outs:
        return False
    snap_in, snap_out = _to_min(snap["check_in"]), _to_min(snap["check_out"])
    if snap_in is None or snap_out is None:
        return False
    min_in = min(_to_min(x) for x in ins)
    max_out = max(_to_min(x) for x in outs)
    return snap_in >= min_in - TOLERANCE_MINUTES and snap_out <= max_out + TOLERANCE_MINUTES


def _fill_nulls(conn, cand, snap):
    """Fill NULL columns of an existing row from the backup row only --
    never overwrite a value the new pipeline stored."""
    updates, params = [], []
    for col in ("check_in", "check_out", "duration_minutes"):
        if cand[col] is None and snap.get(col) is not None:
            updates.append(f"{col} = ?")
            params.append(snap[col])
    if updates:
        params.append(cand["attendance_id"])
        conn.execute(
            f"UPDATE attendance SET {', '.join(updates)} WHERE attendance_id = ?",
            params,
        )
        return True
    return False


def _write_empty(out_path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "captured_at": datetime.now().isoformat(timespec="seconds"),
                "attendance": [],
                "device_punches": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def do_snapshot(db_path, out_path):
    if not db_path.exists():
        print(f"No existing database at {db_path} -- nothing to preserve.")
        _write_empty(out_path)
        print(f"Wrote empty snapshot {out_path}")
        return

    conn = sqlite3.connect(db_path)
    try:
        try:
            n_students = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
        except sqlite3.OperationalError:
            n_students = 0

        attendance = [
            dict(zip(ATTENDANCE_COLS, r))
            for r in conn.execute(f"SELECT {', '.join(ATTENDANCE_COLS)} FROM attendance")
        ]
        devices = [
            dict(zip(DEVICE_COLS, r))
            for r in conn.execute(f"SELECT {', '.join(DEVICE_COLS)} FROM device_punches")
        ]
    except sqlite3.OperationalError as e:
        print(f"WARNING: could not read tables from {db_path}: {e}")
        conn.close()
        _write_empty(out_path)
        print(f"Wrote empty snapshot {out_path}")
        return
    conn.close()

    # Refuse to overwrite the last good snapshot when the DB already looks
    # rebuilt (a previous run died between rebuild and attendance load).
    if n_students > 0 and not attendance and out_path.exists():
        sys.exit(
            f"REFUSED to snapshot {db_path}: it has {n_students} students but ZERO "
            f"attendance rows (looks like a previous run died mid-rebuild). Keeping "
            f"the existing snapshot at {out_path} -- resolve manually before rerunning."
        )

    payload = {
        "schema_version": 1,
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "attendance": attendance,
        "device_punches": devices,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(
        f"Snapshot written to {out_path}: {len(attendance)} attendance rows, "
        f"{len(devices)} device punches."
    )


def do_merge(db_path, in_path):
    if not in_path.exists():
        print(f"No snapshot file at {in_path} -- nothing to merge (first run?).")
        return
    backup = json.loads(in_path.read_text(encoding="utf-8"))

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")

    new_rows = [
        {"attendance_id": r[0], "student_id": r[1], "date": r[2], "session": r[3],
         "check_in": r[4], "check_out": r[5], "duration_minutes": r[6]}
        for r in conn.execute(
            "SELECT attendance_id, student_id, date, session, check_in, check_out, "
            "duration_minutes FROM attendance"
        )
    ]
    by_key = {(r["student_id"], r["date"], r["session"]): r for r in new_rows}
    by_student_date = defaultdict(list)
    for r in new_rows:
        by_student_date[(r["student_id"], r["date"])].append(r)

    existing_student_ids = {
        r[0] for r in conn.execute("SELECT student_id FROM students")
    }

    counts = {
        "matched_same_bucket": 0,
        "matched_rebucketed": 0,
        "matched_coverage": 0,
        "inserted": 0,
        "nulls_filled": 0,
        "skipped_integrity": 0,
        "device_restored": 0,
        "device_skipped_fk": 0,
        "device_skipped_integrity": 0,
    }
    coverage_notes = []
    skip_notes = []

    for snap in backup.get("attendance", []):
        key = (snap["student_id"], snap["date"], snap["session"])
        cand = by_key.get(key)
        if cand is not None:
            counts["matched_same_bucket"] += 1
            if _fill_nulls(conn, cand, snap):
                counts["nulls_filled"] += 1
            continue

        date_rows = by_student_date.get((snap["student_id"], snap["date"]), [])
        others = [r for r in date_rows if r["session"] != snap["session"]]
        event = next((r for r in date_rows if _same_event(snap, r)), None)
        if event is not None:
            counts["matched_rebucketed"] += 1
            if _fill_nulls(conn, event, snap):
                counts["nulls_filled"] += 1
            continue

        if others and _covered(snap, others):
            counts["matched_coverage"] += 1
            coverage_notes.append(
                f"student {snap['student_id']}, {snap['date']}, {snap['session']} "
                f"({snap.get('check_in')}-{snap.get('check_out')}): already covered by "
                f"{len(others)} differently-bucketed row(s) -- skipped as a re-split visit"
            )
            continue

        try:
            conn.execute(
                "INSERT INTO attendance (student_id, date, session, check_in, "
                "check_out, duration_minutes) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    snap["student_id"],
                    snap["date"],
                    snap["session"],
                    snap.get("check_in"),
                    snap.get("check_out"),
                    snap.get("duration_minutes"),
                ),
            )
            counts["inserted"] += 1
        except sqlite3.IntegrityError as e:
            counts["skipped_integrity"] += 1
            skip_notes.append(
                f"attendance insert failed for student {snap['student_id']}, "
                f"{snap['date']}, {snap['session']}: {e} -> SKIPPED"
            )

    for p in backup.get("device_punches", []):
        if p.get("student_id") is not None and p["student_id"] not in existing_student_ids:
            counts["device_skipped_fk"] += 1
            skip_notes.append(
                f"device punch fingerprint={p.get('fingerprint')!r}: student_id "
                f"{p.get('student_id')} no longer in students -> SKIPPED"
            )
            continue
        try:
            conn.execute(
                f"INSERT INTO device_punches ({', '.join(DEVICE_COLS)}) "
                f"VALUES ({', '.join('?' * len(DEVICE_COLS))})",
                [p.get(c) for c in DEVICE_COLS],
            )
            counts["device_restored"] += 1
        except sqlite3.IntegrityError as e:
            counts["device_skipped_integrity"] += 1
            skip_notes.append(
                f"device punch fingerprint={p.get('fingerprint')!r} insert failed: "
                f"{e} -> SKIPPED"
            )

    conn.commit()

    total_attendance = conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0]
    total_devices = conn.execute("SELECT COUNT(*) FROM device_punches").fetchone()[0]
    conn.close()

    report = common.module_report_dir("attendance") / "attendance_merge_report.txt"
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open("w", encoding="utf-8") as f:
        f.write("ATTENDANCE MERGE REPORT -- pre-pipeline data replayed after rebuild\n")
        f.write("=" * 72 + "\n\n")
        f.write(f"Snapshot source: {in_path}\n")
        f.write(f"Snapshot captured: {backup.get('captured_at', '?')}\n")
        f.write(
            f"Backup rows: {len(backup.get('attendance', []))} attendance, "
            f"{len(backup.get('device_punches', []))} device punches\n\n"
        )
        f.write("Merge counts:\n")
        for k, v in counts.items():
            f.write(f"  {k}: {v}\n")
        f.write(f"\nTotal rows now in attendance: {total_attendance}\n")
        f.write(f"Total rows now in device_punches: {total_devices}\n")
        f.write(
            "\n=== VISITS ALREADY COVERED BY A RE-SPLIT (kept as-is, please review) ===\n"
        )
        f.write("\n".join(coverage_notes) + "\n" if coverage_notes else "(none)\n")
        f.write("\n=== SKIPPED ROWS (integrity / FK) ===\n")
        f.write("\n".join(skip_notes) + "\n" if skip_notes else "(none)\n")

    print("Merge counts:", counts)
    print(f"Total attendance now: {total_attendance} (was {len(new_rows)} before merge)")
    print(f"Total device punches now: {total_devices}")
    print(f"Report: {report}")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--mode", required=True, choices=("snapshot", "merge"))
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--out", type=Path, help="snapshot JSON to write (snapshot mode)")
    ap.add_argument("--in", dest="in_path", type=Path, help="snapshot JSON to replay (merge mode)")
    args = ap.parse_args()

    if args.mode == "snapshot":
        if not args.out:
            sys.exit("snapshot mode requires --out")
        do_snapshot(args.db, args.out)
    else:
        if not args.in_path:
            sys.exit("merge mode requires --in")
        do_merge(args.db, args.in_path)


if __name__ == "__main__":
    main()
