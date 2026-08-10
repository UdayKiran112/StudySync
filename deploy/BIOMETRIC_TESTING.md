# StudySync Biometric Attendance — Testing Guide (pyzk / ZKTeco MB360)

A complete, step-by-step guide for testing the ZKTeco biometric attendance
integration on a deployed StudySync system. StudySync connects **out** to the
device over pyzk (TCP 4370) and pulls its attendance buffer — the old ADMS
HTTP-push mode has been removed.

| Transport | Who connects to whom | Port | Direction |
| --- | --- | --- | --- |
| **pyzk (pull/poll/live)** | StudySync connects **out** to the **device** | **4370** (TCP) | Server → Device |

> **Which mode should you use?** `poll` (the default) wakes up every
> `ZK_POLL_INTERVAL` seconds and reads the device buffer; `live` holds one
> persistent pyzk `live_capture()` connection open so a punch registers the
> instant it happens; a periodic full-buffer **reconcile** pass is the
> completeness backstop for both. Run one of `poll`/`live` per device (a ZKTeco
> device usually tolerates only one open session at a time).

---

## 0. What you need before you start

- The StudySync server PC, powered on, with the **StudySyncAPI** and
  **StudySyncCaddy** services running (check below).
- The ZKTeco MB360 device, powered on and on the **same network** as the
  server (same Wi-Fi or same Ethernet LAN).
- A student whose **device PIN equals their `student_id`** in StudySync (the
  device looks up punches by the PIN you enroll, and StudySync matches that
  against `students.student_id`).
- The API key. On this machine it is in
  `C:\ProgramData\StudySync\app\api\.env` (line `STUDYSYNC_API_KEY=...`).
  Every `/api/*` request below needs it in the `X-API-Key` header.

---

## 1. Verify the server is up

Open PowerShell (any user) on the server and run:

```powershell
Get-Service -Name "StudySyncAPI","StudySyncCaddy" | Select-Object Name, Status
```

Both must show **Running**. Then check the web root and API:

```powershell
Invoke-WebRequest -Uri "http://localhost/" -UseBasicParsing          # → 200
Invoke-WebRequest -Uri "http://localhost/api/students?search=a" `
  -Headers @{ "X-API-Key" = "<YOUR_API_KEY>" } -UseBasicParsing      # → 200
```

> `<YOUR_API_KEY>` = the value from `.env`, e.g.
> `POAk5w-uvRP4Vbn59Fbxe2a6d3KD890D_BRT8cgBnyfGkxBU` on this machine.

If a service is stopped, start it (elevated):

```powershell
Start-Process powershell -Verb RunAs -ArgumentList `
  '-Command','Set-Service StudySyncAPI -Status Running; Set-Service StudySyncCaddy -Status Running'
```

---

## 2. Find the device's IP and its comm key

### 2.1 Device IP

On the MB360 touchscreen: `Menu → System Info → Network → IP Address` (e.g.
`192.168.1.201`). Confirm it's reachable from the server:

```powershell
Test-NetConnection 192.168.1.201 -Port 4370
```

`TcpTestSucceeded : True` = the port is open (the device's "TCP/IP" comm must
be enabled in its Comm menu for this).

### 2.2 Comm key

On the device: `Menu → Comm → Comm Key`. The default is `0`. Whatever is set
here **must** match `ZK_COMM_KEY` on the server. A mismatch shows up as
`502 ZKTeco device error` in the API response (see Section 5.3).

---

## 3. Configure the backend

On the server, edit `C:\ProgramData\StudySync\app\api\.env` (elevated editor)
and add:

```ini
ZK_DEVICE_IP=192.168.1.201
ZK_DEVICE_PORT=4370
ZK_COMM_KEY=0
```

Restart the API service (elevated):

```powershell
Restart-Service StudySyncAPI
```

> The background **poller** (default mode) now connects to the device every
> `ZK_POLL_INTERVAL` (default 3) seconds, pulls new swipes, writes them, and
> clears the device buffer. You can also trigger a pull manually (5.5).

---

## 4. How the comm key is handled (pyzk + MB360)

The comm key is **not** sent in the clear. pyzk's `connect()` sends an
unauthenticated connect; the device answers "Unauthenticated"; pyzk then sends
`CMD_AUTH` with `make_commkey(comm_key, session_id)` — the standard ZKTeco
XOR-scramble from `commpro.c`. StudySync wires this up via
`ZK_COMM_KEY` → `zkteco/config.py` → `build_zk(password=...)`. So:

- `ZK_COMM_KEY` = the numeric comm key shown on the device.
- Leave it `0` unless you changed it on the device.
- A wrong key = `502` + log line; a correct key = device answers.

---

## 5. Verify the device is reachable through the API

All `/api/zkteco/*` endpoints need `X-API-Key`. From the server:

```powershell
$h = @{ "X-API-Key" = "<YOUR_API_KEY>" }

# Cheap connectivity probe → {"ok": true}
Invoke-RestMethod -Uri "http://localhost/api/zkteco/status" -Headers $h

# Device self-description (name, firmware, serial, MAC, device time)
Invoke-RestMethod -Uri "http://localhost/api/zkteco/info" -Headers $h

# Everyone enrolled on the device
Invoke-RestMethod -Uri "http://localhost/api/zkteco/users" -Headers $h

# Current memory usage vs capacity
Invoke-RestMethod -Uri "http://localhost/api/zkteco/memory" -Headers $h

# Live-capture listener status (only meaningful in ZK_ATTENDANCE_MODE=live)
Invoke-RestMethod -Uri "http://localhost/api/zkteco/live/status" -Headers $h
```

Expected responses: `status` → `{"ok": true}`; `info` → populated fields; any
device error returns **HTTP 502** with `{"detail": "ZKTeco device error"}`
(reason is in `C:\ProgramData\StudySync\logs\api\api.log`).

---

## 6. Test an actual punch via pyzk

1. Enroll a student on the device with PIN = their `student_id` (e.g. `1`).
2. Have them swipe once. Within ~3–5 seconds the poller picks it up.
3. Verify the attendance row:

   ```powershell
   Invoke-RestMethod -Uri "http://localhost/api/attendance?student_id=1" -Headers $h
   ```

4. Or pull **and write** the device buffer manually:

   ```powershell
   Invoke-RestMethod -Uri "http://localhost/api/zkteco/attendance/sync" -Method Post -Headers $h
   ```

   Returns `{pulled, imported, duplicates, unknown_students, renewed, incomplete}`.
5. Read the raw buffer **without** clearing it (does not touch the DB):

   ```powershell
   Invoke-RestMethod -Uri "http://localhost/api/zkteco/attendance?since=2026-08-07" -Headers $h
   ```

**Expected punch behaviour (two punches → one row):**

- **1st swipe** of the day → new row: `check_in` set, `check_out = null`
  (session labelled "Morning" if before 13:00, else "Afternoon").
- **2nd swipe** → same row gets `check_out`, `session` finalized (may become
  "Full Day"), and `duration_minutes` computed (lunch 13:00–14:00 excluded).
- A **3rd swipe** re-opens (afternoon split), a 4th closes it again — punches
  alternate in/out.

> The poller clears the device buffer after a successful write, and every
> physical punch is recorded in the `device_punches` ledger exactly once, so a
> re-read can never create a duplicate row.

---

## 7. Environment-variable reference

Set these in the backend `.env` — dev: `backend\.env`; production:
`C:\ProgramData\StudySync\app\api\.env` (restart `StudySyncAPI` after edits).

| Variable | Default | Meaning |
| --- | --- | --- |
| `ZK_INTEGRATION` | `pyzk` | `pyzk` / `none` — whether the device integration is mounted. Legacy `both`/`adms` values are accepted and mean `pyzk`. |
| `ZK_DEVICE_IP` | *(unset)* | Device IP. Unset = integration disabled. |
| `ZK_DEVICE_PORT` | `4370` | Device TCP port. |
| `ZK_COMM_KEY` | `0` | Device comm key (Section 4). |
| `ZK_DEVICE_TIMEOUT` | `30` | Seconds to wait for a device reply. |
| `ZK_ATTENDANCE_MODE` | `poll` | `poll` (periodic buffer pull) or `live` (persistent `live_capture`) or `both`. One clearing reader at a time. |
| `ZK_POLL_INTERVAL` | `3` | Seconds between poller cycles (poll mode). |
| `ZK_LIVE_RECONNECT_SECONDS` | `5` | Reconnect backoff for live mode. |
| `ZK_CLEAR_BUFFER` | `1` | `0` makes poll/reconcile read-only (for a device another system also drains). |
| `ZK_RECONCILE_INTERVAL` | `60` | Seconds between full-buffer reconciliation passes. |
| `ZK_PUNCH_DEBOUNCE_MINUTES` | `1` | A swipe within this many minutes of the student's previous swipe that day is ignored as a double-tap (`0` disables). |

---

## 8. Pre-flight checklist for the live test

- [ ] `StudySyncAPI` and `StudySyncCaddy` services **Running**.
- [ ] `http://localhost/` returns 200.
- [ ] `ZK_DEVICE_IP` set in `.env`; service restarted.
- [ ] Device and server on the **same network**.
- [ ] `Test-NetConnection <device-ip> -Port 4370` succeeds.
- [ ] `ZK_COMM_KEY` matches the device's Comm key.
- [ ] Test student's device PIN == `students.student_id`.
- [ ] `GET /api/zkteco/sync-report` shows `fully_synced: true`.

---

## 9. Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `/api/zkteco/*` returns **503** | `ZK_DEVICE_IP` not set | Add it to `.env`, restart `StudySyncAPI`. |
| `/api/zkteco/*` returns **502** | Device unreachable, or comm-key mismatch | Check log; `Test-NetConnection ... -Port 4370`; verify `ZK_COMM_KEY`. |
| `imported: 0, unknown_students: N` | Device PIN has no matching student | Enroll with PIN == `student_id`. |
| `imported: 0, duplicates: N` on a fresh punch | Punch within the 1-min debounce of a previous one | Wait >1 min or set `ZK_PUNCH_DEBOUNCE_MINUTES=0`. |
| Punch creates one row per swipe instead of in/out pairs | Device buffer cleared externally between reads, or two transports reading the same device | Use one transport (`ZK_ATTENDANCE_MODE`); check the `duplicates` tally. |
| Logs show "poll crashed; retrying" every cycle | A single conflicting punch could not be applied | Should not happen after the session-conflict fix; check `api.log` for the `session conflict reconciled` line and the details around it. |
| After an update the device stops working | `.env` lost its `ZK_*` lines | Update/install now preserves `ZK_*` lines; re-add if a very old installer ran. |

**Logs to check:**
- API: `C:\ProgramData\StudySync\logs\api\api.log` (rotating; look for
  `ZKTeco poll ...`, `ZKTeco reconcile ...`, `session conflict reconciled`).
- Caddy: `C:\ProgramData\StudySync\logs\caddy\access.log`.

---

## 10. Cleaning up test data

A swipe from a real student that you only made to test **auto-renews a lapsed
membership** (incrementing `renewal_count` and setting status `Active`) and
creates attendance rows. To remove a test row and undo a test renewal:

```powershell
# run against the production DB (elevated if needed)
sqlite3 "C:\ProgramData\StudySync\data\library.db" "DELETE FROM attendance WHERE student_id=1 AND date='2026-08-07';"
```

and restore `renewal_count` / `status` to what they were before the test (copy
them from a pre-test backup in `C:\ProgramData\StudySync\backups\`). Prefer
testing with a dedicated throwaway student (e.g. `student_id = 9999`) so
production data stays untouched.
