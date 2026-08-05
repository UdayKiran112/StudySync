# StudySync Deployment

How StudySync is built and deployed to a Windows machine. Staff deployment is a
single double-click on `StudySync-Setup.exe`; this document covers how that
installer is produced and what it does.

## Architecture

```
Browser (this PC or any LAN PC)
   |
   |  http://<this-PC-IP>          port 80
   v
Caddy  (service: StudySyncCaddy)
   |  serves static React build (app\frontend)
   |  reverse-proxies /api/*  ->  127.0.0.1:8000
   v
FastAPI / Uvicorn, PyInstaller-bundled  (service: StudySyncAPI)
   |
   v
SQLite database (WAL)   C:\ProgramData\StudySync\data\library.db
```

No Python, Node, or npm is needed on the target machine. Everything ships inside
the installer as compiled executables (PyInstaller bundle + Caddy binary).

## Directory layout on the server

Everything lives under `C:\ProgramData\StudySync`:

| Path | Purpose |
| --- | --- |
| `app\api\` | Backend exe + PyInstaller `_internal`, `.env` (API key, DB path) |
| `app\frontend\` | Built React SPA |
| `app\caddy\` | Caddy binary + `Caddyfile` |
| `config\winsw\` | WinSW service wrappers (`studysync-api.exe`, `studysync-caddy.exe` + XML) |
| `data\library.db` | The database (NEVER edited by an update/install) |
| `data\mplcache\` | Matplotlib font cache (must be writable; frozen exe crashes without it) |
| `backups\` | Nightly `studysync_<timestamp>.zip` backups |
| `scripts\` | `backup.exe`, `restore.exe`, `healthcheck.exe`, `install.ps1`, `update.ps1`, `uninstall.ps1` |
| `logs\` | `api\`, `caddy\`, `winsw\`, `backup\`, `health\`, `installer\` |

## Windows services

| Service | Wrapper | Runs |
| --- | --- | --- |
| `StudySyncAPI` | `studysync-api.exe` (WinSW) | backend exe, binds 127.0.0.1:8000 |
| `StudySyncCaddy` | `studysync-caddy.exe` (WinSW) | Caddy on port 80, depends on API |

Both are `Automatic` + `delayedAutoStart`. If they fail they auto-restart up to
3 times (10/20/30 s backoff). Both log through WinSW to `config\winsw\*.log`.

## Scheduled tasks

| Task | Schedule | Runs |
| --- | --- | --- |
| `StudySyncNightly` | daily 02:00 | `backup.exe` (30-day retention) |
| `StudySyncServiceCheck` | every 5 min | `healthcheck.exe` (restarts a down service) |

Tasks run as the installing (admin) user, `Highest`, battery-safe. Note: on some
machines security software silently deletes SYSTEM-run tasks, so tasks are NOT
registered under SYSTEM. The names `StudySyncNightly` / `StudySyncServiceCheck`
are chosen to survive that filtering.

## API key

- Stored in `app\api\.env` as `STUDYSYNC_API_KEY=<value>`.
- Generated on first install (48 chars, `A-Z a-z 0-9 - _`).
- **Preserved** on every reinstall/update — staff browsers keep working.
- Every `/api/*` route requires the header `X-API-Key: <value>`; a missing or
  wrong key returns 401 (constant-time comparison via `secrets.compare_digest`).
- Staff enter the key ONCE per browser in the app's Settings screen.

## Build pipeline (on the dev machine)

```
deploy\
  build-package.ps1     # compiles backend exe (PyInstaller), assembles deploy\package
  build-installer.ps1   # compiles StudySync-Setup.exe via Inno Setup
  package\              # staged output of build-package.ps1 (no .env inside)
  installer\output\StudySync-Setup.exe
  tools\Inno Setup 6\ISCC.exe   # Inno 6.7.3 (downloaded automatically if missing)
```

1. `powershell -ExecutionPolicy Bypass -File deploy\build-package.ps1`
   - Rebuilds the backend exe (includes the MPLCONFIGDIR bootstrap), collects
     frontend + Caddy + WinSW + scripts into `deploy\package`.
   - Validates the PowerShell scripts are pure ASCII.
   - The package intentionally contains NO `.env` — install/update generate it.
2. `powershell -ExecutionPolicy Bypass -File deploy\build-installer.ps1`
   - Runs `ISCC.exe deploy\installer\studysync.iss` → `StudySync-Setup.exe`.
3. Hand `StudySync-Setup.exe` to staff.

## What the installer does

The Inno script (`deploy\installer\studysync.iss`) is deliberately thin:

1. `PrepareToInstall` `taskkill /F`s any running `studysync-api.exe` /
   `studysync-caddy.exe` / `caddy.exe` so files are never locked (an abrupt
   kill marks the service Stopped; the script restarts it later).
2. `[Files]` stages the package into `{tmp}\package` (never directly into
   `{app}` — copying into the live folder caused a self-copy error).
3. `[Run]` executes `install.ps1 -PackageDir {tmp}\package` elevated, with
   output logged to `logs\installer\inno-install.log`.
4. `install.ps1` is the single source of truth. It:
   - stops existing services (if WinSW binaries exist),
   - copies `app`, `config`, `scripts` into `C:\ProgramData\StudySync`,
   - seeds `data\library.db` ONLY if none exists,
   - preserves/generates `.env`,
   - registers services if missing (existing registrations are only stopped,
     never uninstalled — see the warning below),
   - starts both services,
   - creates the inbound firewall rule `StudySync HTTP (port 80)` (Private +
     Domain only),
   - registers the two scheduled tasks,
   - writes the desktop shortcut.
5. Uninstall: Control Panel → StudySync runs `uninstall.ps1 -Yes`, which makes a
   final backup, removes services/tasks/firewall/shortcut, then deletes
   `C:\ProgramData\StudySync`.

## Critical operational warnings

- **Never uninstall-then-reinstall the services in-place.** WinSW reads its XML
  on every start and the ImagePath is stable, so an install/update only stops,
  swaps files, and starts. An uninstall → install cycle can race the Service
  Control Manager and leave the service stuck "marked for deletion"
  (delete-pending): it can then never start, and only a reboot clears it.
- **`C:\ProgramData\StudySync\data` and `backups` are sacred.** install/update
  never touch them.
- **MPLCONFIGDIR must stay writable.** The frozen backend crashes with a
  matplotlib `KeyboardInterrupt` if the font cache directory
  (`data\mplcache`) is invalid; the service XML sets it via `<env>`.
