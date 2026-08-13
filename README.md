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

## Data pipeline (loading the full historical dataset)

`backend/data_loader/run_pipeline.py` rebuilds `backend/library.db` from
scratch and fills every module from the raw spreadsheet exports. It is the
one command that turns the messy source CSVs into a complete, consistent
database:

```powershell
# from the project root; requires a Python interpreter with `pandas`
python backend\data_loader\run_pipeline.py
```

It runs every stage as its own subprocess so a failure in one stage leaves
the pipeline and its reports inspectable. Useful flags:

- `--python <path>` — use a specific interpreter (e.g. the pandas-enabled
  one) for the sub-steps; defaults to the interpreter running the script.
- `--skip-clean` — skip the cleaning stage and reuse the last cleaned CSVs.

### How it works

The pipeline is dependency-safe: tables are filled in foreign-key order, so
a loader never references a student/exam that doesn't exist yet.

1. **Reset the review ledger** — clears the previous run's `needs a human`
   records (`reports/review/review_items.jsonl`).
2. **Clean** (`clean_student_data.py student_details.csv`) — repairs the
   messy daily activity export: fixes dates and 12-hour-clock slips,
   clamps out-of-operating-hours times, drops junk book IDs, and splits the
   file into one well-formed CSV per module:
   `attendance/attendance.csv`, `digital_library/digital_library.csv`,
   `offline_library/offline_library.csv`, `coaching/digital_class.csv`,
   `marks/offline_exam.csv`, `marks/quiz.csv`. Unfixable rows are written
   to per-section `error_log_*.log` files.
3. **Organize marks** (`organize_internal_marks.py`) — turns the raw
   internal-marks register into `marks/internal_marks_organized.csv`.
4. **Rebuild the DB** — deletes `backend/library.db` and recreates the
   schema, then loads students from
   `members/member_details.csv` (`load_members.py`). Every other table is
   a foreign key of `students`, so this always comes first.
5. **Load sections**, in order:
   `load_attendance` → `load_digital_library` → `load_offline_library` →
   `load_coaching` → `load_offline_exam` → `load_quiz` →
   `load_exam_marks` (last, because the marks register fills scores onto
   the exam rows that the offline-exam loader created).
6. **Render the manual-review report** and write the pipeline summary.

### Business rules baked into the loaders

- **Operating hours** — the library opens 09:00 and closes 18:00
  (`OPEN_TIME` / `CLOSE_TIME` in `common.py`). Check-ins before 09:00 are
  read as 12-hour-clock slips; check-outs past 18:00 are clamped.
- **Attendance sessions** — Morning if check-in is before 13:00, Afternoon
  otherwise, Full Day when the session spans lunch; the 13:00–14:00 lunch
  hour is excluded from the duration.
- **No out-time records are never loaded** — an attendance or digital
  library session that never recorded a check-out / out time is skipped by
  its loader (so no open `check_out`/`out_time` rows appear in the
  database) and written to the manual-review ledger instead for a human to
  complete.
- **Topic canonicalization** — the same real exam/quiz from different
  sources is matched by `(canonical topic, date)`, folding abbreviations
  and typos (`"Ari & Rea"` → `Arithmetic & Reasoning`) without guessing on
  ambiguous ones.
- **Student matching** — exact, word-order, then fuzzy name matching, with
  join-date and same-day activity evidence used to disambiguate
  same-named students. Anything still ambiguous is **skipped for review**,
  never guessed.

### Reports and logs

Every report and log the pipeline produces lives in the gitignored
`backend/data_loader/reports/` tree, one subfolder per module:

```
reports/
  members/          members_load_report.txt, members_gender_report.txt
  attendance/       attendance_load_report*.txt (+ clean-stage logs)
  digital_library/  digital_library_load_report.txt (+ logs)
  offline_library/  offline_library_load_report.txt (+ logs)
  coaching/         coaching_load_report.txt (+ logs)
  marks/            exam_marks_load_report*.txt, offline_exam_load_report.txt,
                    quiz_load_report.txt (+ logs)
  review/           review_items.jsonl + manual_review_report.txt
  pipeline_run_report.txt
```

The file to read first is
`reports/review/manual_review_report.txt`: a consolidated list of every row
the pipeline could not safely auto-correct, grouped by problem type
(unmatched/ambiguous student names, conflicting marks, non-numeric marks,
bad times, etc.) with a reference back to the source row.

## ZKTeco biometric attendance device

The backend can pull attendance logs from a ZKTeco machine (e.g. the MB260)
using the `pyzk` library and write them into the same `attendance` table the
front desk uses. Configure the device server-side in one of two ways:

- **Settings page (recommended):** open Settings → "ZKTeco attendance device",
  scan the network, and pick the machine. The selection is persisted in the
  database (`runtime_config`), so it survives restarts and update swaps, and it
  takes precedence over the `.env` value. The auto-heal poller also re-scans
  and re-points itself automatically after repeated connect failures.
- **Environment variables** (the seed / first-run value, used when discovery
  has never selected a device):

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
- `GET /api/zkteco/discover?subnet=CIDR` — scan the LAN for devices; confirmed
  hits first (optionally restrict with `subnet`, e.g. `192.168.0.0/24`)
- `GET /api/zkteco/device` — which device StudySync is set to (and why:
  discovered / env / none)
- `POST /api/zkteco/device` — `{"ip": "192.168.1.201"}` point StudySync at a
  device (the Settings "use this IP" action)
- `DELETE /api/zkteco/device` — forget the picked device, fall back to `.env`
- `POST /api/zkteco/attendance/sync`
  — pull the buffer and apply each swipe as a check-in or check-out. Returns a
  tally: `pulled`, `imported`, `duplicates`, `unknown_students`, `incomplete`
  (always 0 — lone punches are stored as open sessions, never dropped). The
  device buffer is **not** cleared (StudySync only reads; the exactly-once
  ledger makes re-reads no-ops). Explicit archive-and-clear is a separate
  endpoint: `POST /api/zkteco/attendance/clear`.

Device `user_id`s are matched to `students.student_id` numerically, so
enroll students on the scanner with the same numeric ID they have in
StudySync.

### Automatic sync (device → StudySync)

Once `ZK_DEVICE_IP` is set, StudySync pulls swipes from the device over
pyzk and applies them automatically, so no manual "Sync" click is needed:
a first swipe opens an attendance row and the next closes it. Three
background transports are available (see `ZK_ATTENDANCE_MODE` in
`deploy/config/app.env.example`):

- **poll** (default) — periodically reads the device's buffer
  (`zkteco/poller.py`).
- **live** — holds one persistent pyzk `live_capture()` connection open and
  reacts the instant the device reports a punch (`zkteco/live.py`).
- **reconcile** — a periodic full-buffer re-read (`zkteco/reconcile.py`)
  that is the completeness backstop for the other two.

`ZK_INTEGRATION` (default `pyzk`) selects whether the integration is wired
up at all: `pyzk` / `none`. The legacy values `both` and `adms` are
accepted and mean `pyzk` now that the ADMS push transport is removed.
Device sync health is at `GET /api/zkteco/sync-report` (API-key protected).

### Step-by-step testing guide

`deploy/BIOMETRIC_TESTING.md` is a complete walkthrough for testing the
integration on a deployed system (Windows service + Caddy on port 80): how
to find the machine's IP, configure pyzk in `.env`, verify with
`Test-NetConnection ... -Port 4370`, simulate the device with
PowerShell/curl, and clean up test data.

## API tests

The backend integration suite covers students, books, subscriptions,
attendance, digital and offline library usage, exams and marks, quizzes and
scores, and the dashboard. It runs against a temporary database.

```powershell
& .\study_sync\Scripts\python.exe -m unittest discover -s backend/tests -v
```
A modern Windows desktop application for managing study centers and libraries with student attendance, digital &amp; offline library usage, exams, quizzes, reports, and Google Sheets synchronization. Built using Python, PySide6, and SQLite.
