"""
wipe_device_buffer.py
---------------------
One-shot operator script: empty the ZKTeco device's ATTLOG buffer.

DESTRUCTIVE. This permanently erases every attendance log still sitting
on the device (~99,900 records on the venue MB360). Use it ONLY as the
"wipe, then go live" reset step, and only when all of these hold:

  * Another system (e.g. a venue attendance server) has synced what it
    needs from the buffer, so wiping it loses nothing it still requires;
    this script never connects to that system or touches its database.
  * StudySync is running read-only against the device (ZK_CLEAR_BUFFER=0)
    so the wipe is a deliberate one-time act, not something the poller
    does every 30 seconds.
  * You want StudySync to start capturing from a clean buffer.

Pass --backup to download every record to a CSV before clearing, so the
history survives even after the device forgets it.

Usage:
    ZK_DEVICE_IP=<device ip> python wipe_device_buffer.py --backup prewipe.csv --yes

Exit codes: 0 = done (or nothing to wipe), 1 = device unreachable,
2 = not configured, 3 = aborted (confirmation missing).
"""

import argparse
import csv
import sys

from zkteco.config import device_config
from zkteco.device import ZkError, clear_attendance, device_info, device_serial, list_attendance, memory_usage


def main() -> int:
    parser = argparse.ArgumentParser(description="Erase the device's attendance buffer.")
    parser.add_argument(
        "--backup",
        metavar="CSV",
        help="download every buffered record to this CSV before clearing",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="confirm the destructive wipe",
    )
    args = parser.parse_args()

    config = device_config()
    if config is None:
        print("error: ZK_DEVICE_IP is not set (see zkteco/config.py)", file=sys.stderr)
        return 2

    try:
        info = device_info(config)
        serial = device_serial(config)
        usage = memory_usage(config)
    except ZkError as e:
        print(f"error: cannot reach the device: {e}", file=sys.stderr)
        return 1

    print("Device      :", info.get("device_name"))
    print("Serial      :", serial)
    print("Buffer      :", usage.get("records"), "/", usage.get("records_capacity"))

    if usage.get("records", 0) == 0:
        print("Buffer already empty -- nothing to wipe.")
        return 0

    try:
        logs = list_attendance(config)
    except ZkError as e:
        print(f"error: could not read the buffer: {e}", file=sys.stderr)
        return 1

    print("ATTLOG rows :", len(logs))
    if not logs:
        print("Buffer already empty -- nothing to wipe.")
        return 0

    if args.backup:
        with open(args.backup, "w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["user_id", "timestamp", "status", "uid"])
            for log in logs:
                writer.writerow(
                    [log["user_id"], log["timestamp"], log["status"], log["uid"]]
                )
        print(f"Backup written: {args.backup} ({len(logs)} rows)")

    if not args.yes:
        print()
        print("WARNING: this will PERMANENTLY erase the above records from the")
        print("device buffer. Anything StudySync has not yet synced will only")
        print("survive via --backup.")
        print("Re-run with --yes to proceed, or CTRL-C to abort.")
        return 3

    try:
        clear_attendance(config)
    except ZkError as e:
        print(f"error: wipe failed: {e}", file=sys.stderr)
        return 1

    try:
        after = memory_usage(config).get("records")
    except ZkError:
        after = "unknown"
    print("Wipe complete. Records now on device:", after)
    return 0


if __name__ == "__main__":
    sys.exit(main())
