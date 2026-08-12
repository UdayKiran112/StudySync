# StudySync Deployment

How StudySync is built and deployed to a Windows machine. Staff deployment is a
single double-click on `StudySync-Setup.exe`; this document covers how that
installer is produced and what it does.

## Architecture

```
Browser (this PC or any LAN PC)
   |
   |  http://studysync.local / http://<this-PC-IP>     port 80
   v
Caddy  (service: StudySyncCaddy)
   |  serves static React build (app\frontend)
   |  reverse-proxies /api/*  ->  127.0.0.1:8000
   v
FastAPI / Uvicorn, PyInstaller-bundled  (service: StudySyncAPI)
   |  advertises "studysync.local" over mDNS (UDP 5353) — through Apple
   |  Bonjour when it is running, or its own responder otherwise
   v
SQLite database (WAL)   C:\ProgramData\StudySync\data\library.db
```

The API service advertises the LAN name **`http://studysync.local`** over mDNS
(no PC rename needed). The advertisement self-heals: if the machine's IP changes
(another Wi-Fi, DHCP), it re-registers within ~60 s. Apple/Android devices
resolve it natively; Windows PCs need Apple Bonjour (kept at `tools\Bonjour64.msi`
for staff PCs). On a server that already runs Apple's Bonjour Service, the API
publishes the name *through* Bonjour using the Bonjour client API (dnssd.dll),
so Bonjour keeps owning UDP 5353 and the two coexist instead of fighting. Only
when no Bonjour is installed does the API run its own mDNS responder for the
name. See OPERATIONS.md.

No Python, Node, or npm is needed on the target machine. Everything ships inside
the installer as compiled executables (PyInstaller bundle + Caddy binary).

## Directory layout on the server

Everything lives under `C:\ProgramData\StudySync`:

| Path | Purpose |
| --- | --- |
| `app\api\` | Backend exe + PyInstaller `_internal`, `.env` (API key, DB path) |
| `app\frontend\` | Built React SPA |
| `app\caddy\` | Caddy binary + `Caddyfile` |
| `config\winsw\` | WinSW service wrappers (`studysync-api.exe`, `studysync-caddy.exe` + XML). The XMLs carry the service-account password |
| `data\library.db` | The database (NEVER edited by an update/install) |
| `data\mplcache\` | Matplotlib font cache (must be writable; frozen exe crashes without it) |
| `backups\` | Nightly `studysync_<timestamp>.zip` backups |
| `scripts\` | `backup.exe`, `restore.exe`, `healthcheck.exe`, `install.ps1`, `update.ps1`, `uninstall.ps1`, `rotate-key.ps1` |
| `tools\` | `Bonjour64.msi` (Apple Bonjour for Windows - mDNS name resolution) |
| `logs\` | `api\`, `caddy\`, `winsw\`, `backup\`, `health\`, `installer\`. Caddy's access log strips API keys/authorization headers |

## Windows services

| Service | Wrapper | Runs |
| --- | --- | --- |
| `StudySyncAPI` | `studysync-api.exe` (WinSW) | backend exe, binds 127.0.0.1:8000 |
| `StudySyncCaddy` | `studysync-caddy.exe` (WinSW) | Caddy on port 80, depends on API |

Both are `Automatic` + `delayedAutoStart`. If they fail they auto-restart up to
3 times (10/20/30 s backoff). Both log through WinSW to `config\winsw\*.log`.

### Service account

Both services run as the dedicated **low-privilege local account `StudySyncSvc`**
— never `LocalSystem`. A compromise of the API or Caddy process is therefore
contained to the StudySync tree (`C:\ProgramData\StudySync`) and cannot read
other users' files, secrets, or the OS. The account is created by
`install.ps1` with a random password, which is stored in the WinSW XMLs
(`config\winsw\*.xml`) and in the Service Control Manager's credential store.

- The password is **stable across installs/updates** (`install.ps1` /
  `update.ps1` re-read it from the existing XML and re-inject it after any file
  swap, then re-assert it via `sc.exe config ... obj= .\StudySyncSvc`). This is
  what keeps the SCM registration working without uninstalling/reinstalling
  services (which risks the "marked for deletion" race below).
- `install.ps1` strips the inherited `BUILTIN\Users` read ACL from the whole
  tree and grants access only to SYSTEM, Administrators, the installing user,
  and `StudySyncSvc` (read-only on the app, write on `data\` / `logs\` /
  `backups\`). This is what keeps logs and the WinSW XMLs (which hold the
  service password) out of every local user's reach.
- `uninstall.ps1` removes the account (only if no other StudySync service still
  references it).

## Scheduled tasks

| Task | Schedule | Runs |
| --- | --- | --- |
| `StudySyncNightly` | daily 02:00 | `backup.exe` (30-day local + Google Drive mirror) |
| `StudySyncServiceCheck` | every 5 min | `healthcheck.exe` (restarts a down service; auto-restores a missing/corrupt database from backup) |

Tasks run as the installing (admin) user, `Highest`, battery-safe. Note: on some
machines security software silently deletes SYSTEM-run tasks, so tasks are NOT
registered under SYSTEM. The names `StudySyncNightly` / `StudySyncServiceCheck`
are chosen to survive that filtering.

## Backup & disaster recovery

- **Local retention**: `backup.exe` keeps the newest `studysync_*.zip` files in
  `C:\ProgramData\StudySync\backups\` and deletes anything older than
  `STUDYSYNC_BACKUP_RETENTION_DAYS` (default **30**). A disk-space guard prunes
  oldest-first if free space drops below 1 GiB.
- **Google Drive mirror (optional)**: if `GOOGLE_CREDS_FILE` (the same
  service-account JSON used for Sheets) and `GOOGLE_DRIVE_FOLDER_ID` are set in
  `app\api\.env`, every nightly run uploads any local backups missing from the
  Drive folder (idempotent, so missed uploads self-heal) and prunes remote
  copies older than the same 30-day window. The Drive folder must be **shared
  with the service-account email** (Editor). Drive failures never affect the
  local backup — they are logged as warnings only.
- **Auto-restore**: `healthcheck.exe` (every 5 min, elevated) checks the
  database with `PRAGMA integrity_check`. If the database is missing or corrupt
  it automatically: stops `StudySyncAPI` → preserves the broken file as
  `data\library.db.corrupt-<ts>` → extracts the newest local backup (falling
  back to the newest copy on Google Drive) → verifies the result → restarts the
  service. A 24-hour cooldown (`STUDYSYNC_AUTO_RESTORE_COOLDOWN_HOURS`) prevents
  restore loops, and a failed restore rolls back to the pre-restore database.
  See `logs\health\health.log` for the full audit trail.

## API key

- Stored in `app\api\.env` as `STUDYSYNC_API_KEY=<value>`.
- Generated on first install (48 chars, `A-Z a-z 0-9 - _`).
- **Preserved** on every reinstall/update — staff browsers keep working.
- Every `/api/*` route requires the header `X-API-Key: <value>`; a missing or
  wrong key returns 401 (constant-time comparison via `secrets.compare_digest`).
- Staff enter the key ONCE per browser in the app's Settings screen.
- **Rotation** (after any suspicion of exposure): run `rotate-key.ps1` elevated
  on the server (or re-run `install.ps1 -RotateKey`). The old key stops working
  immediately; the new key is printed to the console and must be re-entered in
  every browser's Settings. The key is never written to any log file — including
  the installer logs and the diagnostics bundle (which redacts it).

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
   - stops existing services (if WinSW binaries exist), also stopping the
     scheduled tasks and killing any running `healthcheck.exe` / `backup.exe`
     so the file copy can never hit a locked exe,
   - copies `app`, `config`, `scripts`, `tools` into `C:\ProgramData\StudySync`,
   - seeds `data\library.db` ONLY if none exists (the package carries the
     build machine's database as seed data, so a fresh venue install starts
     with the current students; existing data is never overwritten),
   - preserves/generates `.env`,
   - creates the `StudySyncSvc` low-privilege service account, injects its
     password into the WinSW XMLs, and hardens the ACLs on
     `C:\ProgramData\StudySync` (inherited `BUILTIN\Users` read access
     removed),
   - registers services if missing (existing registrations are only stopped,
     never uninstalled — see the warning below), and asserts the service
     account via `sc.exe config` on both registrations,
   - starts both services,
   - creates the inbound firewall rules `StudySync HTTP (port 80)` (TCP) and
     `StudySync mDNS (UDP 5353)` **for Private/Domain profiles only**, with
     port 80 additionally restricted to RFC1918 LAN addresses (`10/8`,
     `172.16/12`, `192.168/16`) + loopback. Public/cafe networks get no
     inbound access, and a machine behind a public IP cannot expose port 80
     to the internet. Windows is no longer asked to reclassify Public
     networks (the old "switch to Private" step is removed),
   - keeps Apple Bonjour (`tools\Bonjour64.msi`) on the server for Windows
     staff PCs. If that same PC also runs the server, install the MSI once and
     the API publishes `studysync.local` *through* Bonjour rather than running
     its own responder,
   - registers the two scheduled tasks,
   - writes the desktop shortcut.
5. Uninstall: Control Panel → StudySync runs `uninstall.ps1 -Yes`, which makes a
   final backup (the newest `backups\*.zip` is copied to the operator's
   `Documents\` folder FIRST, before the tree is deleted — the backup must
   never live only inside the folder being removed), removes
   services/tasks/firewall/shortcut, deletes the `StudySyncSvc` account, then
   deletes `C:\ProgramData\StudySync`. Bonjour is a shared Windows component
   (also used by iTunes etc.) and is intentionally left installed on any PC
   that has it.

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
- **A venue network Windows marks Public gets no LAN access.** The firewall
  rules are Private/Domain only by design; if devices cannot reach
  `studysync.local` on a venue Wi-Fi, change the network to a private profile
  (Settings → Network → that network → Private) or set a
  `New-NetFirewallRule -Profile Private,Domain` rule manually. Do NOT
  re-widen the shipped rules to `Any`.
- **After a key leak, rotate — never reuse.** `rotate-key.ps1` (or
  `install.ps1 -RotateKey`) generates a fresh key and restarts the API. The
  leaked value is dead the moment the service is back up.
