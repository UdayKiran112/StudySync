# StudySync Operations

Day-to-day runbooks for the machine running StudySync (the server). All commands
run from an **elevated** (Administrator) PowerShell on that machine.

## Quick health check

```powershell
powershell -ExecutionPolicy Bypass -File C:\ProgramData\StudySync\scripts\diagnostics.ps1
```

Produces `%TEMP%\studysync-diagnostics_<stamp>.zip` with service state, log
tails, firewall rule, scheduled tasks, network info, and a health-check result.
Hand this zip to whoever is diagnosing.

One-liner checks:

```powershell
Get-Service StudySyncAPI, StudySyncCaddy                 # both should be Running
(Invoke-WebRequest http://localhost -UseBasicParsing).StatusCode   # 200
Get-Content C:\ProgramData\StudySync\logs\health\health.log -Tail 5   # last line HEALTHY
```

## Is it healthy?

`healthcheck.exe` (every 5 minutes via task `StudySyncServiceCheck`, or run it
manually):

```powershell
C:\ProgramData\StudySync\scripts\healthcheck.exe
```

- Exit 0 = `HEALTHY`, logged to `logs\health\health.log`.
- Non-zero = something was down; it logs `UNHEALTHY: ...` and attempts to
  restart the offending service itself.
- If the health log shows repeated `UNHEALTHY` lines, run diagnostics and check
  `logs\api\api.log` and `config\winsw\*.err.log`.

## Restarting the app

```powershell
& C:\ProgramData\StudySync\config\winsw\studysync-api.exe restart
& C:\ProgramData\StudySync\config\winsw\studysync-caddy.exe restart
```

(Services also restart automatically on crash, 3 attempts with backoff, and on
reboot — both are `Automatic`.)

## Where the logs live

| Log | Path |
| --- | --- |
| Backend | `logs\api\api.log` (rotating) |
| Caddy access | `logs\caddy\access.log` |
| WinSW wrapper | `config\winsw\*.out.log` / `*.err.log` / `*.wrapper.log` |
| Backups | `logs\backup\backup.log` |
| Health watch | `logs\health\health.log` |
| System tray | `logs\tray\tray.log` |
| Installer | `logs\installer\install.log`, `inno-install.log`, `inno-uninstall.log`, `update.log` |

## System-tray monitor

A tray icon (like the Bluetooth/McAfee icons) sits in the notification area of
this PC, started at every logon by the `StudySyncTray` scheduled task (elevated,
so it can restart services without a UAC prompt). The icon color reflects overall
health:

- **Green** — all three services (`StudySyncAPI`, `StudySyncCaddy`,
  `Bonjour Service`) running.
- **Amber** — a service is starting/stopping, or a state is unknown.
- **Red** — a service is stopped or not installed.

Clicking the icon opens a small status window listing each service with its live
state and a **Restart** button per service; the tray menu also has **Restart All
Stopped**. The icon refreshes every 5 seconds; `logs\tray\tray.log` records
start/stop and any errors. If the icon is missing, run
`C:\ProgramData\StudySync\scripts\studysync-tray.exe` (or re-run the installer,
which recreates the task and starts it).

## Backup

Automatic nightly at 02:00 (`StudySyncNightly` → `backup.exe`). Backups land in
`C:\ProgramData\StudySync\backups\studysync_<timestamp>.zip` and are pruned
after **30 days** (`STUDYSYNC_BACKUP_RETENTION_DAYS`).

The backup is made through SQLite's online-backup API, so it is consistent even
while the API is running — no need to stop services.

Manual backup:

```powershell
C:\ProgramData\StudySync\scripts\backup.exe
```

## Restore

Restore **requires stopping the API** first (the script refuses while it runs):

```powershell
sc stop StudySyncAPI
C:\ProgramData\StudySync\scripts\restore.exe               # lists backups, prompts
# or pick a specific one:
C:\ProgramData\StudySync\scripts\restore.exe C:\ProgramData\StudySync\backups\studysync_2026-08-05_020000.zip
sc start StudySyncAPI
```

- The pre-restore database is kept at `data\library.db.pre-restore`.
- If the restore fails mid-way, the previous database is restored automatically.

## Shipping a database change (new seed data)

Made edits in the database (students, books, marks, ...) and want fresh installs
to start with them? No rebuild of the code is needed - just:

1. Update the database (either `backend\library.db` in this repo, or the live
   install at `C:\ProgramData\StudySync\data\library.db`).
2. **Double-click `deploy\make-installer.cmd`** (or run
   `deploy\make-installer.ps1`). It picks the newest database, verifies
   integrity, copies it into the package as the seed, and rebuilds
   `deploy\installer\output\StudySync-Setup.exe` - nothing else.
3. Hand out the new `StudySync-Setup.exe`. A fresh install seeds from it.

> Re-running the installer on a machine that already has data never overwrites
> that data - the seed only applies when no database exists yet. For CODE
> changes use `build-package.ps1` + `build-installer.ps1` instead.

## Update (new version of the app)

Ship the updated package (produced by `build-package.ps1`) to the machine, then:

```powershell
powershell -ExecutionPolicy Bypass -File C:\ProgramData\StudySync\scripts\update.ps1 -PackageDir C:\path\to\new\package
```

What it does: stops services → safety backup → swaps `app\config\scripts` →
recreates `.env` with the SAME API key and DB path → starts services.

**Data is never touched** — `data\library.db`, backups, and logs survive.

> Schema changes: if the database schema changed, run the migration with the NEW
> code BEFORE `update.ps1` restarts the API (update.ps1 pauses after the swap;
> do not modify that behavior casually).

If staff already have `StudySync-Setup.exe`, simply re-running it performs the
same in-place update (the installer is idempotent).

## Uninstall / moving to another machine

Best path: **uninstall via Control Panel → StudySync** (runs `uninstall.ps1
-Yes`). It makes a final backup, removes services, scheduled tasks, firewall
rule, desktop shortcut, then deletes `C:\ProgramData\StudySync`.

Manual equivalent:

```powershell
C:\ProgramData\StudySync\scripts\uninstall.ps1 -Yes
```

> Uninstall DELETES the database after backing it up to `backups\`. Copy that
> backup elsewhere before uninstalling if you still need the data (e.g. moving
> to a new server).

## Access from other PCs on the LAN

The app is reachable by a stable name, `http://studysync.local`, on every device.
How the device resolves it depends on the OS:

| Device | Address to use | Why |
| --- | --- | --- |
| Apple (iPhone/iPad/Mac), Android | `http://studysync.local` | Native mDNS (Bonjour) resolver built into the OS |
| Windows PC | `http://studysync.local` | After installing Apple Bonjour (below) |
| Windows PC (no Bonjour) | `http://<server-name>` (e.g. `http://Myth`) or `http://<server-IP>` | NetBIOS / IP fallback |
| This server | `http://localhost` | Loopback |

- The server advertises `studysync.local` via mDNS (UDP 5353) from the API
  service. Firewall rules `StudySync HTTP (port 80)` + `StudySync mDNS
  (UDP 5353)` allow inbound on **all network profiles** (Private, Domain, and
  Public), and the installer also switches any Public network to Private
  (best-effort) — so LAN access works no matter how Windows classifies the
  network.
- The advertisement is **self-healing**: the API re-resolves the machine's IP
  every 60 s and re-registers the name if it changed. Moving the laptop to
  another Wi-Fi (different IP) is picked up within a minute with no restart.
- **Windows PCs need Apple Bonjour** to resolve `*.local` names (Windows' built-in
  DNS client only resolves its own hostname). The installer auto-installs it on
  the server and keeps a copy at `C:\ProgramData\StudySync\tools\Bonjour64.msi`.
  For each Windows staff PC: double-click that MSI (or
  `\\<server-name>\C$\ProgramData\StudySync\tools\Bonjour64.msi`) once, then
  `http://studysync.local` resolves. If a PC lacks Bonjour, use `http://Myth`.
- Staff enter the API key once per browser in Settings.

### Using it on another network (demo / venue)

Everything needed for LAN access is configured by the installer itself, so a
fresh install on a venue machine is automatic. When **moving the already-set-up
laptop** to another Wi-Fi:

1. Connect the laptop to the new Wi-Fi.
2. Wait ~1 min for the mDNS advertisement to re-register with the new IP.
3. Other devices on that network open `http://studysync.local` (Bonjour
   installed on Windows PCs). No service restart or IP lookup needed.
4. Exception: some **public/guest Wi-Fi has AP (client) isolation** — the router
   blocks device-to-device traffic, so no device on the network can reach the
   laptop. There is nothing software can do about that; use a normal LAN/guest
   network without isolation for the demo.

## Troubleshooting cheat-sheet

| Symptom | Likely cause / fix |
| --- | --- |
| `http://localhost` won't load | `StudySyncCaddy` down → `healthcheck.exe`, check `config\winsw\studysync-caddy.err.log` |
| API 500s / `/api/*` fails but page loads | `StudySyncAPI` down → check `logs\api\api.log`, `config\winsw\studysync-api.err.log` |
| Backend crashes with matplotlib `KeyboardInterrupt` | `data\mplcache` missing/unwritable or `MPLCONFIGDIR` env dropped; recreate the dir, restart service |
| Service stuck "marked for deletion" (never starts) | A previous uninstall raced the SCM. Reboot the machine, then re-run the installer |
| A scheduled task keeps disappearing | Security software deleting SYSTEM tasks; re-run the installer to recreate `StudySyncNightly`/`StudySyncServiceCheck` (user principal) |
| Tray icon missing after install | Task `StudySyncTray` runs at logon; re-log on or run `scripts\studysync-tray.exe` manually |
| Tray icon red | A service is stopped — click the icon and use **Restart** (needs the tray to run elevated, i.e. from the scheduled task) |
| Tray "Restart" does nothing | The tray was launched manually without admin; re-run the installer or restart it from task `StudySyncTray` |
| API returns 401 | Browser's saved key no longer matches `app\api\.env` → re-enter the key in Settings |
| Port 80 conflict on install | Another web server on the machine; installer does not stop foreign processes |

## Support

Collect the diagnostics bundle (`diagnostics.ps1`) plus `logs\api\api.log` and
`config\winsw\*.err.log` when reporting an issue.
