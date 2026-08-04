# StudySync

## Backend configuration

Set a staff API key before running the backend. Every `/api/*` endpoint
requires it in the `X-API-Key` request header (Swagger UI supports this via
its **Authorize** button).

```powershell
$env:STUDYSYNC_API_KEY = "replace-with-a-long-random-secret"
```

Optionally configure the allowed frontend origins:

```powershell
$env:STUDYSYNC_ALLOWED_ORIGINS = "http://localhost:5173,http://localhost:3000"
```

## Demo records

To populate every data module with 30 realistic sample records (including
30 students), run the following from the project root. The script is
idempotent: rerunning it adds only missing `DEMO` records and does not delete
your existing data.

```powershell
& .\study_sync\Scripts\python.exe .\backend\seed_demo_data.py
```

## ZKTeco biometric attendance device

The backend can pull attendance logs from a ZKTeco machine (e.g. the MB260)
using the `pyzk` library and write them into the same `attendance` table the
front desk uses. Configure the device **server-side** with environment
variables (never via the client):

```powershell
$env:ZK_DEVICE_IP = "192.168.1.201"   # required -- IP/hostname of the scanner
$env:ZK_DEVICE_PORT = "4370"          # optional, default 4370
$env:ZK_COMM_KEY = "0"                # optional, default 0 (set on the device)
$env:ZK_DEVICE_TIMEOUT = "30"         # optional, default 30 seconds
$env:ZK_POLL_INTERVAL = "3"           # optional, seconds between auto-syncs
$env:ZK_PUNCH_DEBOUNCE_MINUTES = "5"  # optional, ignore accidental double-taps
```

As soon as `ZK_DEVICE_IP` is set, the backend runs a background poller that
syncs the device every `ZK_POLL_INTERVAL` seconds (default 3) with **no
manual action**, and the attendance page live-refreshes every 5 seconds so
new records just appear. Each swipe is applied the moment it is read: the
**first punch of the day becomes an open check-in** (shown on the page right
away, no check-out yet), and the **next punch closes it** as the check-out,
with the session (Morning / Afternoon / Full Day) and duration computed the
same way as the front-desk flow. The device buffer is cleared automatically
after every successful sync, so nothing accumulates on the scanner.

An accidental double-tap is ignored: a punch that lands within
`ZK_PUNCH_DEBOUNCE_MINUTES` (default 5, `0` disables) of the student's
previous punch for the same day is skipped, so a second scan a second later
can't create a bogus 1-minute session or re-open one.

All endpoints live under `/api/zkteco` and require the same `X-API-Key`
header as every other staff endpoint:

- `GET /api/zkteco/status` — connectivity probe
- `GET /api/zkteco/info` — name, firmware, serial number, MAC, time
- `GET /api/zkteco/users` — users enrolled on the device
- `GET /api/zkteco/attendance?since=YYYY-MM-DD` — raw swipe logs
- `POST /api/zkteco/attendance/sync`
  — pull the buffer, apply each swipe as a check-in or check-out, and clear
  the device buffer. Returns a tally: `pulled`, `imported`, `duplicates`,
  `unknown_students`, `incomplete` (always 0 — lone punches are stored as
  open sessions, never dropped).

Device `user_id`s are matched to `students.student_id` numerically, so
enroll students on the scanner with the same numeric ID they have in
StudySync.

## API tests

The backend integration suite covers students, books, subscriptions,
attendance, digital and offline library usage, exams and marks, quizzes and
scores, and the dashboard. It runs against a temporary database.

```powershell
& .\study_sync\Scripts\python.exe -m unittest discover -s backend/tests -v
```
A modern Windows desktop application for managing study centers and libraries with student attendance, digital &amp; offline library usage, exams, quizzes, reports, and Google Sheets synchronization. Built using Python, PySide6, and SQLite.
