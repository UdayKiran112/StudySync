# StudySync - Installation & Google Setup Guide

This guide covers two things:

1. **Part A** - installing StudySync on a Windows PC (the server / front-desk
   machine).
2. **Part B/C/D** - enabling the optional **Google Sheets sync** and **Google
   Drive backup mirror**: creating the Google Cloud project, service account,
   spreadsheet and Drive folder, and wiring them into the `.env` file.

Everything in Part B-D is optional. StudySync runs fully offline without
Google - only add it if you want staff to also see the data in a spreadsheet
or want backups mirrored off-site.

---

## Part A - Installation

### A.1 What you need before you start

| Machine | Needs |
| --- | --- |
| Build machine (this repo) | Node.js + npm, the build venv, and the binaries in `deploy\bin` / `deploy\tools` |
| Target PC | Windows 10/11, **admin rights**, free port 80, ~1.5 GB disk |

### A.2 Build the installer (once, on the dev/build machine)

```powershell
powershell -ExecutionPolicy Bypass -File deploy\build-package.ps1    # builds deploy\package
powershell -ExecutionPolicy Bypass -File deploy\build-installer.ps1  # builds deploy\installer\output\StudySync-Setup.exe
```

### A.3 Install on the target PC

**Option 1 - installer (recommended).** Copy
`deploy\installer\output\StudySync-Setup.exe` to the PC (USB / network share)
and double-click it **as administrator**. It installs everything silently:

- `StudySyncAPI` + `StudySyncCaddy` Windows services (run as the
  low-privilege `StudySyncSvc` account)
- Fresh API key + `.env` at `C:\ProgramData\StudySync\app\api\.env`
  (preserved across re-installs)
- Seed database (only when no database exists - existing data is never touched)
- Firewall rules for port 80 + mDNS (Private/Domain networks, LAN-only),
  **Apple Bonjour** (installed automatically for `http://studysync.local`),
  nightly backup + health-watchdog scheduled tasks, tray monitor, desktop
  shortcut.

**Option 2 - package folder.** Copy `deploy\package\` to the PC and run:

```powershell
powershell -ExecutionPolicy Bypass -File C:\path\to\package\scripts\install.ps1
```

### A.4 After installation

1. Confirm it's up: `http://localhost` on the server PC loads the app.
2. Set the venue Wi-Fi to **Private** in Windows Settings, or other devices
   can't reach the machine (firewall is Private/Domain-only by design).
3. Every staff browser enters the **API key** once in the app's Settings
   screen (the key lives in `C:\ProgramData\StudySync\app\api\.env`).
4. Other devices open `http://studysync.local` (Windows PCs need Bonjour -
   now installed automatically by the installer).

Updates: re-run the same `StudySync-Setup.exe` - it is idempotent and keeps
data and the API key.

---

## Part B - Create the Google Cloud things (outside StudySync)

These steps happen in the Google Cloud Console and Google Drive - no StudySync
changes yet. Budget ~10 minutes.

### B.1 Create a Google Cloud project

1. Go to <https://console.cloud.google.com> and sign in.
2. Click the project dropdown (top bar) → **New Project** → name it (e.g.
   `studysync`) → **Create** → select it.

### B.2 Enable the APIs

1. **APIs & Services → Library** and enable **both**:
   - **Google Sheets API**
   - **Google Drive API**
   (Click in, hit **Enable**.)

### B.3 Create a service account + download the JSON key

1. **APIs & Services → Credentials → Create Credentials →
   Service Account**.
2. Name it (e.g. `studysync-sync`), click **Create and Continue**, then
   **Done**.
3. In the service-account list, click the account → **Keys** tab →
   **Add Key → Create new key → JSON → Create**.
   A `*.json` file downloads. It contains the service-account **email** field
   `client_email` - you will need this in the next steps.

Keep this JSON file secret - it is the password to your service account.

### B.4 Create and share the Google Sheet (for the sync)

1. In Google Drive, create a blank **Google Sheets** spreadsheet
   (File → New → Google Sheets).
2. Copy the **spreadsheet ID**: it is the long string in the URL
   `https://docs.google.com/spreadsheets/d/<ID>/edit`.
3. Click **Share** → add the service-account email (`client_email` from
   step B.3) with role **Editor** → Send.
   (It can send a notification to a non-existent inbox; just close the dialog.)

### B.5 Create and share the Drive folder (for the backup mirror)

1. In Google Drive, create a folder (e.g. `studysync-backups`).
2. Open the folder and copy the **folder ID** from the URL:
   `https://drive.google.com/drive/folders/<ID>`.
3. Click **Share** → add the service-account email with role **Editor**.

> The same service-account email must be shared on BOTH the spreadsheet and
> the folder.

---

## Part C - Configure the environment file

### C.1 Where the file lives

On the installed server:

```
C:\ProgramData\StudySync\app\api\.env
```

The installer generates it and it is **preserved across installs/updates**:
install.ps1 keeps any existing `GOOGLE_*` / `ZK_*` lines, so you configure
Google once and it survives re-installs.

### C.2 Place the credentials file

Copy the JSON key from step B.3 to:

```
C:\ProgramData\StudySync\app\api\credentials.json
```

(`GOOGLE_CREDS_FILE` resolves relative to this folder when unqualified - the
API service's working directory is `C:\ProgramData\StudySync\app\api`.)

### C.3 Add the variables

Edit `.env` (as Administrator) and add:

```ini
# --- Google Sheets cloud sync ---
GOOGLE_SPREADSHEET_ID=<the long ID from B.4>
GOOGLE_CREDS_FILE=credentials.json
# Optional: cells per single API write (default 100000)
# STUDYSYNC_SHEETS_MAX_CELLS_PER_REQUEST=100000

# --- Google Drive backup mirror ---
GOOGLE_DRIVE_FOLDER_ID=<the folder ID from B.5>
# Optional: how many days of backups to keep (local + remote), default 30
# STUDYSYNC_BACKUP_RETENTION_DAYS=30
```

Only lines that start with `GOOGLE_`/`STUDYSYNC_` and are not blank/comments
need to be present - keep the rest of the file as-is.

### C.4 Restart the API (applies Sheets changes)

```powershell
& C:\ProgramData\StudySync\config\winsw\studysync-api.exe restart
```

> The Drive backup mirror does **not** need the restart: the scheduled
> `StudySyncNightly` task runs `backup.exe`, which reads the same `.env` file
> itself on every run.

---

## Part D - Verify

### D.1 Google Sheets sync (manual test)

From PowerShell (or the app):

```powershell
$key = (Select-String -Path C:\ProgramData\StudySync\app\api\.env -Pattern '^STUDYSYNC_API_KEY=(.+)').Matches.Groups[1].Value
Invoke-RestMethod -Method Post -Uri http://localhost/api/sync -Headers @{ 'X-API-Key' = $key }
```

Each module gets its own worksheet (tab): attendance, libraries, exams,
quizzes, etc. Status is per-sheet; history at `GET /api/sync/history`.

### D.2 Google Drive backup (test the mirror)

```powershell
C:\ProgramData\StudySync\scripts\backup.exe
```

Check `C:\ProgramData\StudySync\logs\backup\backup.log` for `Uploaded ... to
Drive`, and confirm the zip appears in the shared Drive folder.

### D.3 Watchdog restore (optional behaviour)

If the local database is ever missing/corrupt, `healthcheck.exe` (every 5
min) auto-restores from the newest local backup, falling back to the newest
copy on Google Drive.

---

## Part E - Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `POST /api/sync` fails "not set" | `GOOGLE_SPREADSHEET_ID` missing in `.env`, or the API wasn't restarted since editing - see C.4 |
| "Service-account key not found" | `credentials.json` not at `C:\ProgramData\StudySync\app\api\credentials.json`, or `GOOGLE_CREDS_FILE` points elsewhere |
| Sheets error 403 / access denied | The spreadsheet is not shared with the service-account email (Editor) - check B.4 |
| Drive upload fails 403 | The folder is not shared with the service-account email (Editor) - check B.5 |
| `Google API` error, never `Drive sync: all local backups already uploaded` | Check `logs\backup\backup.log`; Drive failures log a warning and never affect the local backup |
| Wrong key in `.env` | Always restart the API after editing `.env` (Sheets picks config up at request time) |
| API returns 401 in browsers | The saved browser key no longer matches `.env` - re-enter it in Settings, or run `scripts\rotate-key.ps1` |

The full operational runbook (backups, restore, updates, LAN access, health
check) is in `deploy/OPERATIONS.md`.