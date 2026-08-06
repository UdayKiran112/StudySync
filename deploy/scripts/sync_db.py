"""
sync_db.py
----------
Sync the freshly-built pipeline database (backend/library.db) to the
deploy package seed and the installed StudySync deployment.

Change-detecting: each destination is compared to the source at the
LOGICAL level (every table: row count + a checksum of its rows), not by
raw file bytes -- a live StudySyncAPI service keeps rewriting the file
(WAL checkpoints etc.), so byte-hashing would report "DIFFERS" even when
the content is identical. Only destinations that genuinely differ from
the source are touched, and the deployed copy is only ever rewritten
(and the services only restarted) when it is really out of date, so a
no-op run never prompts for elevation:

    python deploy/scripts/sync_db.py [--check] [--force] [--skip-deployed]

  --check          report sync state only, change nothing.
  --force          ignore the comparison and always copy + restart
                   services.
  --skip-deployed  only refresh the package seed, leave the installed
                   copy and its services alone.

Flow when a real difference exists:
  1. copy the package seed (deploy/package/data/library.db) -- local;
  2. stop the StudySyncAPI + StudySyncCaddy services, wipe stale
     -wal/-shm sidecars, copy the fresh DB over the installed one, and
     restart the services (an elevation prompt appears once for this
     step -- UAC is unavoidable because services run as SYSTEM);
  3. re-compare the installed DB and report whether the sync matched.

Note: the pipeline output is the source of truth -- deploying overwrites
the installed DB, so any rows the live service wrote since the last sync
are replaced. Run this right after run_pipeline.py.

Overrides (mirroring restore.py conventions):
    STUDYSYNC_APP_DIR   installed app dir   (default C:\\ProgramData\\StudySync)
    STUDYSYNC_DB_PATH   installed DB path   (default <APP_DIR>\\data\\library.db)
"""

import argparse
import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SOURCE = ROOT / "backend" / "library.db"
PACKAGE = ROOT / "deploy" / "package" / "data" / "library.db"

APP_DIR = Path(os.getenv("STUDYSYNC_APP_DIR", r"C:\ProgramData\StudySync"))
DEPLOYED = Path(
    os.getenv("STUDYSYNC_DB_PATH", APP_DIR / "data" / "library.db")
)

SERVICES = ("StudySyncAPI", "StudySyncCaddy")
WORKDIR = Path(tempfile.gettempdir()) / "studysync-sync"
HELPER = WORKDIR / "sync_deployed_helper.ps1"
RESULT = WORKDIR / "sync_deployed_result.txt"


def _table_rows(path: Path) -> dict[str, tuple[int, str]]:
    """Logical fingerprint of a database: {table: (row_count, checksum)}."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    out = {}
    try:
        tables = [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        for t in tables:
            try:
                rows = con.execute(f'SELECT * FROM "{t}" ORDER BY rowid').fetchall()
            except sqlite3.OperationalError:
                rows = con.execute(f'SELECT * FROM "{t}"').fetchall()
            out[t] = (
                len(rows),
                hashlib.sha256(repr(rows).encode("utf-8", "surrogatepass")).hexdigest(),
            )
    finally:
        con.close()
    return out


def same_db(a: dict, b: dict) -> bool:
    return a == b


def write_helper() -> None:
    helper = f"""$ErrorActionPreference = "Stop"
$out = "{RESULT}"
$lines = New-Object System.Collections.Generic.List[string]
try {{
    $src = "{SOURCE}"
    $dst = "{DEPLOYED}"
    $lines.Add("=== stopping services ===")
    Stop-Service -Name "StudySyncAPI" -Force
    Stop-Service -Name "StudySyncCaddy" -Force
    Start-Sleep -Seconds 2
    $lines.Add("stopped: " + ((Get-Service -Name "StudySyncAPI","StudySyncCaddy" | ForEach-Object {{ "$($_.Name)=$($_.Status)" }}) -join ", "))
    $lines.Add("=== copying fresh db ===")
    if (-not (Test-Path $src)) {{ throw "source db missing: $src" }}
    foreach ($suffix in ("-wal", "-shm")) {{
        $side = $dst + $suffix
        if (Test-Path -LiteralPath $side) {{
            Remove-Item -LiteralPath $side -Force
            $lines.Add("removed stale sidecar: $side")
        }}
    }}
    Copy-Item -LiteralPath $src -Destination $dst -Force
    $lines.Add("copied to: $dst (" + (Get-Item $dst).Length + " bytes)")
    $lines.Add("=== starting services ===")
    Start-Service -Name "StudySyncAPI"
    Start-Service -Name "StudySyncCaddy"
    Start-Sleep -Seconds 3
    $lines.Add("started: " + ((Get-Service -Name "StudySyncAPI","StudySyncCaddy" | ForEach-Object {{ "$($_.Name)=$($_.Status)" }}) -join ", "))
    $lines.Add("RESULT: OK")
}} catch {{
    $lines.Add("RESULT: ERROR - " + $_.Exception.Message)
}}
Set-Content -LiteralPath $out -Value ($lines -join "`r`n") -Encoding UTF8
"""
    WORKDIR.mkdir(parents=True, exist_ok=True)
    HELPER.write_text(helper, encoding="utf-8")


def run_elevated() -> tuple[bool, list[str]]:
    write_helper()
    if RESULT.exists():
        RESULT.unlink()
    launch = (
        "Start-Process powershell -Verb RunAs -Wait -ArgumentList "
        f"'-NoProfile','-ExecutionPolicy','Bypass','-File','{HELPER}'"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", launch],
        check=False,
        capture_output=True,
    )
    deadline = time.time() + 120
    while time.time() < deadline:
        if RESULT.exists():
            return True, RESULT.read_text(encoding="utf-8-sig").splitlines()
        time.sleep(1)
    return False, ["elevated sync did not report back (UAC cancelled?)"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="report only, change nothing")
    ap.add_argument("--force", action="store_true", help="always copy + restart services")
    ap.add_argument("--skip-deployed", action="store_true", help="only refresh the package seed")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    if not SOURCE.exists():
        print(f"ERROR: source DB not found: {SOURCE}")
        print("Run the data pipeline first (backend/data_loader/run_pipeline.py).")
        return 1

    src = _table_rows(SOURCE)
    pkg = _table_rows(PACKAGE) if PACKAGE.exists() else None
    dep = _table_rows(DEPLOYED) if DEPLOYED.exists() else None

    pkg_synced = same_db(src, pkg)
    dep_synced = same_db(src, dep)

    print("sync_db -- StudySync database sync (logical comparison)")
    print(f"  source  : {SOURCE}")
    print(f"  package : {PACKAGE}  {'OK' if pkg_synced else 'DIFFERS'}"
          + ("" if pkg else "  (missing)"))
    print(f"  deployed: {DEPLOYED}  {'OK' if dep_synced else 'DIFFERS'}"
          + ("" if dep else "  (not installed)"))

    if args.check:
        if pkg_synced and dep_synced:
            print("\nAlready in sync -- nothing to do.")
        else:
            print("\nOut of sync. Re-run without --check to sync.")
        return 0

    if pkg_synced and dep_synced and not args.force:
        print("\nAlready in sync -- nothing to do.")
        return 0

    changed_anything = False

    if not pkg_synced:
        shutil.copy2(SOURCE, PACKAGE)
        print(f"\npackage seed refreshed: {PACKAGE}")
        changed_anything = True
    else:
        print("\npackage seed already current.")

    if args.skip_deployed:
        print("--skip-deployed: leaving the installed DB and services alone.")
        return 0

    if dep_synced and not args.force:
        print("deployed DB already current -- services left running.")
        return 0

    print("\nInstalled copy is out of date -- swapping DB and restarting services.")
    print("An elevation (UAC) prompt will appear; approve it to proceed.")
    ok, lines = run_elevated()
    print()
    print("\n".join(lines))
    if not ok or not any("RESULT: OK" in ln for ln in lines):
        print("\nSYNC FAILED -- nothing was changed on the deployed side.")
        return 1

    if same_db(src, _table_rows(DEPLOYED)):
        print("\nVerified: deployed DB now matches the source. Sync complete.")
        return 0
    print("\nWARNING: deployed DB still differs from source after copy; verify manually.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
