"""
zkteco/reconcile.py
--------------------
Periodic full-buffer reconciliation -- the completeness backstop.

A realtime transport is best-effort: a punch can be missed server-side
(crash, ingest bug, server down). Every punch is still sitting in the
device's ATTLOG buffer, though, and this loop is what guarantees it
eventually lands in the database regardless:

  * It reads the ENTIRE device buffer on a slow cadence
    (ZK_RECONCILE_INTERVAL, default 60s) and routes every record through
    the exact same capture_and_apply() ledger the poller/live use, so
    records that the live transport already handled become
    duplicate_transport no-ops and anything it MISSED gets applied.
  * It persists per-device health into the device_state table
    (last_reconcile_at, buffer size, ledger pending counts, ATTLOG fill %)
    so operators can see, after a restart, that the system is fully
    caught up.
  * After applying, it verifies that every pyzk record it pulled has a
    durable ledger write (see verify_pyzk_vs_db) -- any mismatch is logged
    as "reconcile verify mismatch" without killing the pass. A past-day
    lone check-in is a legitimate 'pending' ledger state (its attendance
    row materializes only when its check-out punch lands), so it verifies
    as healthy rather than as an anomaly.
  * BUFFER MANAGEMENT: when the ATTLOG fills past ZK_BUFFER_CLEAR_PERCENT
    (default 95) it archives the whole buffer into a dated offline database
    (device_punches_YYYY-MM-DD.db, see zkteco/archive.py) and clears the
    device -- but ONLY after verification passes with zero issues and
    ZK_BUFFER_AUTO_CLEAR is enabled. The archive is keyed by ledger
    fingerprint, so a same-day re-run upserts instead of duplicating, and
    the ledger pruner can then drop the archived rows (the cleared device
    can never re-serve them). Clearing is the point of no return and is
    deliberately gated: an unverified record is never destroyed.

It runs alongside the pyzk poller (where it's a redundant safety net and
the status keeper) or the pyzk live listener (where it is the only buffer
reader).
"""

import asyncio
import json
import logging
from datetime import datetime

from attendance_punch import build_fingerprint
from database import get_connection
from zkteco.archive import mark_cleared, write_archive
from zkteco.config import (
    buffer_alert_percent,
    buffer_auto_clear_enabled,
    buffer_clear_percent,
    device_config,
    reconcile_interval,
)
from zkteco.device import (
    ZkError,
    clear_attendance,
    device_serial,
    memory_usage,
)
from zkteco.sync import sync_attendance_from_device

logger = logging.getLogger("zkteco.reconcile")


def _ledger_stats(db) -> dict:
    """Per-state counts over the whole device_punches ledger."""
    pending = db.execute(
        "SELECT COUNT(*) FROM device_punches WHERE state = 'pending'"
    ).fetchone()[0]
    by_state = {}
    for row in db.execute(
        "SELECT state, COUNT(*) AS n FROM device_punches GROUP BY state"
    ):
        by_state[row["state"]] = row["n"]
    total = sum(by_state.values())
    return {
        "ledger_pending": pending,
        "ledger_total": total,
        "ledger_applied": by_state.get("applied", 0),
        "ledger_duplicate_transport": by_state.get("duplicate_transport", 0),
        "ledger_duplicate_debounced": by_state.get("duplicate_debounced", 0),
        "ledger_duplicate_session": by_state.get("duplicate_session", 0),
        "ledger_unknown_student": by_state.get("unknown_student", 0),
    }


def verify_pyzk_vs_db(db, logs, serial: str) -> dict:
    """
    Verify that every pyzk record pulled from the device produced a durable
    database write.

    For each record the device reported, rebuild its exact ledger
    fingerprint (same inputs capture_and_apply() used) and confirm the
    device_punches row exists -- a missing row means the record was fetched
    but never durably written, exactly the class of bug reconcile exists to
    catch. A row still in state 'pending' is NOT an anomaly: under the
    session completion rule that is a past-day lone check-in legitimately
    awaiting its check-out punch. Malformed records (no user_id, unparsed
    timestamp) are reported too.

    Never raises: any anomaly is returned in the report so the pass keeps
    running. Returns {"verified", "issue_count", "issues"} with issues
    capped to the first 20 for readability.
    """
    verified = 0
    issues = []
    for log in logs:
        uid = log.get("user_id")
        ts = log.get("timestamp")
        if uid is None or not isinstance(ts, datetime):
            issues.append(
                {
                    "user_id": str(uid),
                    "timestamp": str(ts),
                    "issue": "malformed device record",
                }
            )
            continue
        try:
            fingerprint = build_fingerprint(serial, uid, ts, log.get("status"))
        except Exception as e:  # pragma: no cover - defensive
            issues.append(
                {
                    "user_id": str(uid),
                    "timestamp": str(ts),
                    "issue": f"fingerprint failed: {e}",
                }
            )
            continue
        row = db.execute(
            "SELECT state FROM device_punches WHERE fingerprint = ?", (fingerprint,)
        ).fetchone()
        if row is None:
            issues.append(
                {
                    "user_id": str(uid),
                    "timestamp": str(ts),
                    "issue": "no ledger row written",
                }
            )
        else:
            # Any existing row -- including 'pending', a past-day lone
            # check-in legitimately awaiting its check-out punch -- counts
            # as a durable write.
            verified += 1
    return {"verified": verified, "issue_count": len(issues), "issues": issues[:20]}


def archive_and_clear_attlog(
    config,
    logs: list,
    serial: str,
    verify: dict,
    *,
    force: bool = False,
) -> dict:
    """
    Archive the pulled ATTLOG into today's dated archive DB, then clear the
    device buffer. The order is fixed and non-negotiable: archive BEFORE
    clear, and never clear unless every record has a durable write.

    Refuses to clear when verification reports any issue (a fetched record
    with no durable DB write must not be destroyed), or when
    ZK_BUFFER_AUTO_CLEAR is disabled -- unless ``force`` is True (the
    explicit POST /api/zkteco/attendance/clear operator endpoint). A buffer
    that is already empty is a no-op success.

    Returns a dict with: buffer_archived, buffer_cleared, archive_path,
    archive_count, remaining_records, buffer_capacity, buffer_status
    ("ok" | "full_verify_failed" | "full_auto_clear_disabled" |
    "archive_failed" | "clear_failed" | "clear_partial").
    """
    result = {
        "buffer_archived": False,
        "buffer_cleared": False,
        "archive_path": None,
        "archive_count": 0,
        "remaining_records": 0,
        "buffer_capacity": 0,
        "buffer_status": "not_attempted",
    }
    if not force and not buffer_auto_clear_enabled():
        result["buffer_status"] = "full_auto_clear_disabled"
        return result
    if verify["issue_count"]:
        result["buffer_status"] = "full_verify_failed"
        logger.error(
            "ZKTeco buffer: refusing to clear -- verification found %s of %s "
            "records with no durable write. First issues: %s",
            verify["issue_count"],
            len(logs),
            verify["issues"],
        )
        return result
    if not logs:
        result["buffer_status"] = "ok"
        return result

    try:
        mem = memory_usage(config)
    except ZkError as e:
        logger.warning("ZKTeco buffer: could not read memory sizes: %s", e)
        mem = {}
    result["buffer_capacity"] = mem.get("records_capacity") or 0

    archive = write_archive(serial, logs, capacity=result["buffer_capacity"])
    if archive["count"] == 0:
        result["buffer_status"] = "archive_failed"
        logger.error("ZKTeco buffer: archive produced 0 rows for %s records.", len(logs))
        return result
    result["buffer_archived"] = True
    result["archive_path"] = archive["path"]
    result["archive_count"] = archive["count"]

    try:
        remaining = clear_attendance(config)
    except ZkError as e:
        result["buffer_status"] = "clear_failed"
        logger.error(
            "ZKTeco buffer: %s records archived to %s but the device clear "
            "failed (%s); the buffer stays full and will retry next pass.",
            result["archive_count"],
            archive["path"],
            e,
        )
        return result

    result["remaining_records"] = remaining
    if remaining:
        result["buffer_status"] = "clear_partial"
        logger.error(
            "ZKTeco buffer: %s records archived to %s but %s records remain "
            "on the device after clearing; retrying next pass.",
            result["archive_count"],
            archive["path"],
            remaining,
        )
        return result

    mark_cleared(archive["path"], serial, datetime.utcnow().isoformat())
    result["buffer_cleared"] = True
    result["buffer_status"] = "ok"
    logger.info(
        "ZKTeco buffer: archived %s records to %s and cleared the device.",
        result["archive_count"],
        archive["path"],
    )
    return result


def _evaluate_buffer(config, logs: list, serial: str, verify: dict) -> dict:
    """
    Decide whether this pass must archive + clear the device buffer and act.

    Reads the ATTLOG fill % (records / capacity). Below the clear threshold
    this just reports the fill level; at or above it the pass archives and
    clears (subject to the verify + auto-clear gates in
    archive_and_clear_attlog). Never raises: device failures surface as a
    "unknown" buffer status so the reconcile tally is still persisted.
    """
    try:
        mem = memory_usage(config)
    except ZkError as e:
        logger.warning("ZKTeco buffer: could not read memory sizes: %s", e)
        return {
            "buffer_capacity": None,
            "buffer_count": None,
            "buffer_fill_percent": None,
            "buffer_status": "unknown",
        }

    capacity = mem.get("records_capacity") or 0
    records = mem.get("records") or 0
    fill = round(records * 100.0 / capacity, 1) if capacity else None
    out = {
        "buffer_capacity": capacity,
        "buffer_count": records,
        "buffer_fill_percent": fill,
        "buffer_status": "ok",
    }

    alert = buffer_alert_percent()
    if fill is not None and fill >= alert:
        out["buffer_status"] = "warning"
        logger.warning("ZKTeco buffer at %s%% (alert threshold %s%%).", fill, alert)

    if fill is None or fill < buffer_clear_percent():
        return out

    result = archive_and_clear_attlog(config, logs, serial, verify, force=False)
    if result["buffer_cleared"]:
        out["buffer_status"] = "ok"
    else:
        out["buffer_status"] = result["buffer_status"]
    out.update(result)
    return out


def _update_device_state(db, config, tally: dict) -> None:
    """Persist durable sync health for the device (survives restarts)."""
    serial = device_serial(config)
    stats = _ledger_stats(db)
    now = datetime.utcnow().isoformat()
    db.execute(
        """
        INSERT INTO device_state
            (device_serial, last_seen_at, last_reconcile_at, last_buffer_count,
             ledger_pending, last_result, buffer_capacity, buffer_status,
             oldest_buffer_ts, last_archive_path, last_archive_count,
             last_clear_at, clear_failures)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(device_serial) DO UPDATE SET
            last_seen_at = excluded.last_seen_at,
            last_reconcile_at = excluded.last_reconcile_at,
            last_buffer_count = excluded.last_buffer_count,
            ledger_pending = excluded.ledger_pending,
            last_result = excluded.last_result,
            buffer_capacity = excluded.buffer_capacity,
            buffer_status = excluded.buffer_status,
            oldest_buffer_ts = excluded.oldest_buffer_ts,
            last_archive_path = excluded.last_archive_path,
            last_archive_count = excluded.last_archive_count,
            last_clear_at = excluded.last_clear_at,
            clear_failures = excluded.clear_failures
        """,
        (
            serial,
            now,
            now,
            tally["pulled"],
            stats["ledger_pending"],
            json.dumps(tally),
            tally.get("buffer_capacity"),
            tally.get("buffer_status"),
            tally.get("oldest_buffer_ts"),
            tally.get("archive_path"),
            tally.get("archive_count"),
            tally.get("last_clear_at"),
            tally.get("clear_failures", 0),
        ),
    )
    db.commit()


def reconcile_once() -> dict:
    """
    One reconciliation pass over the full device buffer: read everything,
    apply anything not yet in the ledger/database, and persist device
    health. Returns the run tally.

    After the apply pass the run also verifies each pyzk record it pulled
    maps to a durable ledger write (see verify_pyzk_vs_db), reporting any
    mismatch as a "reconcile verify" warning instead of aborting the pass.
    The verify results are folded into the tally returned and persisted in
    device_state.

    When the ATTLOG buffer fills past ZK_BUFFER_CLEAR_PERCENT the pass
    archives the whole buffer into a dated offline archive and clears the
    device -- but only once verification is clean and ZK_BUFFER_AUTO_CLEAR
    is enabled (see archive_and_clear_attlog). Clearing is destructive and
    deliberately gated; everything else in this pass remains read-only
    against the device.
    """
    config = device_config()
    if config is None:
        return {}
    db = get_connection()
    try:
        tally, logs = sync_attendance_from_device(
            db, config, source="reconcile", return_logs=True
        )
        serial = device_serial(config)
        verify = verify_pyzk_vs_db(db, logs, serial)
        tally["verify_verified"] = verify["verified"]
        tally["verify_issue_count"] = verify["issue_count"]

        buffer = _evaluate_buffer(config, logs, serial, verify)
        tally.update(buffer)

        timestamps = [
            log["timestamp"]
            for log in logs
            if isinstance(log.get("timestamp"), datetime)
        ]
        oldest = min(timestamps).strftime("%Y-%m-%d %H:%M:%S") if timestamps else None
        tally["oldest_buffer_ts"] = None if buffer.get("buffer_cleared") else oldest

        if buffer.get("buffer_cleared"):
            tally["last_clear_at"] = datetime.utcnow().isoformat()
        prev_failures = db.execute(
            "SELECT clear_failures FROM device_state WHERE device_serial = ?",
            (serial,),
        ).fetchone()
        prev_failures = prev_failures["clear_failures"] if prev_failures else 0
        status = buffer.get("buffer_status")
        if buffer.get("buffer_cleared"):
            tally["clear_failures"] = 0
        elif status in ("clear_failed", "clear_partial"):
            tally["clear_failures"] = int(prev_failures or 0) + 1
        else:
            tally["clear_failures"] = int(prev_failures or 0)

        _update_device_state(db, config, tally)
        if verify["issue_count"]:
            logger.warning(
                "Reconcile verify mismatch: %s of %s pyzk records have no matching "
                "DB write. First issues: %s",
                verify["issue_count"],
                tally["pulled"],
                verify["issues"],
            )
        logger.info(
            "ZKTeco reconcile: pulled=%s imported=%s dup_transport=%s "
            "dup_debounced=%s unknown=%s verified=%s buffer=%s%%/status=%s",
            tally["pulled"],
            tally["imported"],
            tally["duplicate_transport"],
            tally["duplicate_debounced"],
            tally["unknown_students"],
            verify["verified"],
            buffer.get("buffer_fill_percent"),
            buffer.get("buffer_status"),
        )
        return tally
    finally:
        db.close()


def current_sync_status() -> dict:
    """
    Durable + live view of device sync health, for the sync-report
    endpoint: per-device state with the ledger breakdown and a
    fully_synced verdict.
    """
    db = get_connection()
    try:
        stats = _ledger_stats(db)
        fully_synced = stats["ledger_pending"] == 0
        open_sessions = db.execute(
            "SELECT COUNT(*) FROM attendance WHERE check_out IS NULL"
        ).fetchone()[0]
        row = db.execute(
            "SELECT * FROM device_state ORDER BY last_reconcile_at DESC LIMIT 1"
        ).fetchone()
        last_result = {}
        if row and row["last_result"]:
            try:
                last_result = json.loads(row["last_result"])
            except (TypeError, ValueError):
                last_result = {}
        status = {
            "device_serial": row["device_serial"] if row else None,
            "last_reconcile_at": row["last_reconcile_at"] if row else None,
            "last_buffer_count": row["last_buffer_count"] if row else None,
            "ledger_pending": stats["ledger_pending"],
            "ledger_total": stats["ledger_total"],
            "ledger_applied": stats["ledger_applied"],
            "ledger_duplicate_transport": stats["ledger_duplicate_transport"],
            "ledger_duplicate_debounced": stats["ledger_duplicate_debounced"],
            "ledger_duplicate_session": stats["ledger_duplicate_session"],
            "ledger_unknown_student": stats["ledger_unknown_student"],
            "open_sessions": open_sessions,
            "last_verify_verified": last_result.get("verify_verified", 0),
            "last_verify_issue_count": last_result.get("verify_issue_count", 0),
            "buffer_capacity": row["buffer_capacity"] if row else None,
            "buffer_status": row["buffer_status"] if row else None,
            "buffer_fill_percent": None,
            "oldest_buffer_ts": row["oldest_buffer_ts"] if row else None,
            "last_archive_path": row["last_archive_path"] if row else None,
            "last_archive_count": row["last_archive_count"] if row else None,
            "last_clear_at": row["last_clear_at"] if row else None,
            "clear_failures": (row["clear_failures"] or 0) if row else 0,
            "fully_synced": fully_synced,
        }
        if row and row["buffer_capacity"]:
            status["buffer_fill_percent"] = round(
                (row["last_buffer_count"] or 0) * 100.0 / row["buffer_capacity"], 1
            )
        return status
    finally:
        db.close()


async def zkteco_reconcile_loop(stop_event: asyncio.Event) -> None:
    """Reconcile the full device buffer every interval until stopped."""
    if device_config() is None:
        logger.info("ZKTeco reconciliation disabled: ZK_DEVICE_IP is not set.")
        return

    interval = reconcile_interval()
    logger.info("ZKTeco reconciliation started (every %ss).", interval)
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(reconcile_once)
        except ZkError as e:
            # Device unreachable -- nothing to do but retry next cycle.
            logger.warning("ZKTeco reconcile failed (device unreachable?): %s", e)
        except Exception:  # never let one bad cycle kill the loop
            logger.exception("ZKTeco reconcile crashed; retrying next cycle.")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue
    logger.info("ZKTeco reconciliation stopped.")
