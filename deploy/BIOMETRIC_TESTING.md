# StudySync Biometric Attendance — Testing Guide (ADMS & pyzk / ZKTeco MB360)

A complete, step-by-step guide for testing the ZKTeco biometric attendance
integration on a deployed StudySync system. It covers **both** transport modes
the backend supports:

| Mode | Who connects to whom | Port | Direction |
| --- | --- | --- | --- |
| **ADMS (push)** | The **device** connects **in** to StudySync over HTTP | **80** (via Caddy) | Device → Server |
| **pyzk (pull/poll)** | StudySync connects **out** to the **device** | **4370** (TCP) | Server → Device |

> **Which one should you use for an MB360?** **ADMS.** It is ZKTeco's built-in
> "Cloud Server" push protocol, keeps the device fully operational, and needs
> no per-device connection settings on the server. pyzk is an alternative that
> polls the device's buffer over TCP 4370.

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

## 2. Find the server's LAN IP (the address the device will dial)

The device cannot reach the server via `localhost` — it needs the server's
**LAN IP**.

### Method A — `ipconfig` (simplest)

```powershell
ipconfig
```

Look at your **active** adapter (Wi-Fi or Ethernet, NOT "VirtualBox" or
"Loopback"). Copy the **IPv4 Address**, e.g. `192.168.1.100`.

### Method B — PowerShell one-liner

```powershell
Get-NetIPAddress -AddressFamily IPv4 | Where-Object {
    $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' -and $_.PrefixOrigin -ne 'WellKnown'
} | Select-Object IPAddress, InterfaceAlias
```

### Method C — mDNS name (no IP needed)

The app advertises itself as **`http://studysync.local`** over mDNS (published
through the server's Apple Bonjour Service when it is running). Browsers on
iPhones/Android/Macs (and Windows with Bonjour installed) can just use that
name. **For the ADMS device you can also enter `studysync.local`** if its Cloud
Server Setting accepts a hostname — otherwise use the IP from Method A/B.

> Write the IP down; you'll type it into the device's Cloud Server Setting in
> Section 4.

---

## 3. Confirm the device can reach the server (network check)

From the server, you can *ping* the device, and from the device you can *browse*
the server. At minimum, verify the ADMS port on the server is reachable from
the device's side by opening this URL in the device's web browser (most MB360
firmwares have a small browser, or test from any phone/PC on the same Wi-Fi):

```
http://studysync.local/iclock/cdata?SN=TEST
```

(or `http://192.168.1.100/iclock/cdata?SN=TEST` if the firmware cannot resolve
the mDNS name). You should see a text block starting with `GET OPTION FROM:TEST`
(see Section 5 for what it means). If the page fails to load, the problem is
network/firewall, not the app.

**Firewall note (production):** the installer already opens **TCP port 80**
for all profiles (`StudySync HTTP (port 80)`), which is the ADMS path.
Port 4370 (pyzk) is **outbound from the server**, so no inbound rule is needed
on the server.

---

## 4. Part 1 — Test **ADMS** (device pushes to StudySync)

### 4.1 Find the device's IP and serial

On the MB360 touchscreen:
`Menu → System Info → Device Info` (serial number, e.g. `ZK1234567890`) and
`Menu → System Info → Network → IP Address` (the device's own IP).

The serial number matters only if you enable the allowlist
(`ZK_ADMS_ALLOWED_SERIALS`); leave it empty (default) to accept any serial for
the first test.

### 4.2 Configure the device's "Cloud Server Setting" (ADMS)

On the MB360:

1. `Menu` (enter the Admin password if asked; factory default is often `0` or
   blank).
2. Go to **Comm** (Communication).
3. Open **Cloud Server Setting** (sometimes labelled "ADMS", "TCP/IP", or
   "Push" depending on firmware).
4. Set:
   - **IP / Server Address** = `studysync.local` (if the firmware accepts a
     hostname) or the server's LAN IP from Section 2 (e.g. `192.168.1.100`).
   - **Port** = `80`.
   - **Encrypt / UseSSL** = Off (StudySync answers with `Encrypt=0`).
5. Save / Apply. The device immediately does a handshake to
   `http://192.168.1.100:80/iclock/cdata?...`

### 4.3 Watch the handshake arrive

Now, from any PC/phone on the same network, check the live status endpoint
(the `X-API-Key` header is required):

```powershell
Invoke-RestMethod -Uri "http://localhost/api/adms/status" `
  -Headers @{ "X-API-Key" = "<YOUR_API_KEY>" }
```

Expected: a `devices` object keyed by the device's serial, with
`last_handshake_at` filled in (a few seconds after the device saved the
settings). Also watch the API log:

```powershell
Get-Content "C:\ProgramData\StudySync\logs\api\api.log" -Tail 30
```

You should see lines like:

```
ADMS handshake from SN=ZK1234567890
```

> **Nothing appears?** The device can't reach the server. Re-check: same
> network, correct IP/port, Windows firewall allows inbound port 80
> (`Get-NetFirewallRule -DisplayName "StudySync*"`), and Caddy is proxying
> `/iclock/*` (see the note in Section 3).

### 4.4 Punch in / punch out on the device

Ask a student to scan their fingerprint/face on the MB360. Because the
handshake requested `Realtime=1`, the device **immediately** POSTs an ATTLOG
line to `/iclock/cdata?table=ATTLOG` for **every** punch.

Verify in real time:

```powershell
Invoke-RestMethod -Uri "http://localhost/api/adms/status" `
  -Headers @{ "X-API-Key" = "<YOUR_API_KEY>" }
```

Now `last_push_at` and `last_result` should update, e.g.:

```json
"last_result": { "pulled": 1, "imported": 1, "duplicates": 0, "unknown_students": 0, "renewed": 0 }
```

And the attendance row appears:

```powershell
Invoke-RestMethod -Uri "http://localhost/api/attendance?student_id=1" `
  -Headers @{ "X-API-Key" = "<YOUR_API_KEY>" }
```

**Expected punch behaviour (both punches → one row):**
- **1st swipe** of the day → new row: `check_in` set, `check_out = null`
  (session labelled "Morning" if before 13:00, else "Afternoon").
- **2nd swipe** → same row gets `check_out`, `session` finalized (may become
  "Full Day"), and `duration_minutes` computed (lunch 13:00–14:00 excluded).
- A **3rd swipe** re-opens (afternoon split), a 4th closes it again — punches
  alternate in/out.

### 4.5 What each field in an ADMS push means

A pushed ATTLOG line looks like this (tab-separated):

```
1<TAB>2026-08-07 09:30:00<TAB>0<TAB>1<TAB><TAB>0<TAB>0
```

| Column | Name | Meaning |
| --- | --- | --- |
| 1 | `PIN` | The device's enrolled ID — **must equal `students.student_id`** |
| 2 | `DateTime` | Device local time, `YYYY-MM-DD HH:MM:SS` |
| 3 | `Status` | Device check-in/out code (logged, **not trusted**; session state is derived from the database instead) |
| 4 | `Verify` | Verification method (1 = fingerprint, 4 = card, 15 = face, etc.) |
| 5–7 | `WorkCode` / reserved | Ignored |

A `last_result` of `unknown_students: N` means N punches had a PIN with no
matching `students.student_id` — fix the enrollment/PIN, not the code.

### 4.6 Simulate the device (no physical punch needed)

You can test the whole ADMS path from any PC/phone without touching the
device. This is exactly what the MB360 sends.

**PowerShell** (Windows):

```powershell
# 1) Handshake
Invoke-WebRequest -Uri "http://<SERVER-IP>/iclock/cdata?SN=TEST001" -UseBasicParsing

# 2) Push one ATTLOG line (a check-in for student 1 at 09:30 today)
$body = "1`t2026-08-07 09:30:00`t0`t1`t`t0`t0`r`n"
Invoke-WebRequest -Uri "http://<SERVER-IP>/iclock/cdata?SN=TEST001&table=ATTLOG" `
  -Method Post -Body $body -ContentType "text/plain" -UseBasicParsing
```

**curl** (Windows 10+, macOS, Linux):

```bash
# 1) Handshake
curl "http://<SERVER-IP>/iclock/cdata?SN=TEST001"

# 2) Push one ATTLOG line
curl -X POST "http://<SERVER-IP>/iclock/cdata?SN=TEST001&table=ATTLOG" \
     -d $'1\t2026-08-07 09:30:00\t0\t1\t\t0\t0\r\n'
```

Both must return **HTTP 200** and the literal body **`OK`**. Then check
`/api/adms/status` for the import tally. To simulate a **check-out**, send a
second line with a later time (e.g. `12:00:00`) for the same PIN/day.

> The device serial `TEST001` will show up in `/api/adms/status` in-memory
> diagnostics. It resets on service restart and is not written to the
> database. (One serial is reserved: `STUDYSYNC-HEALTHCHECK-PROBE` is used by
> the server's own healthcheck and is silently dropped — do not use it for a
> real device.)

---

## 5. Part 2 — Test **pyzk** (StudySync polls the device over TCP 4370)

> pyzk is optional. If you only use ADMS, skip this section. Both modes can be
> active at once (dedup is handled), but for a clean first test enable **one**
> mode at a time.

### 5.1 Find the device's IP and its comm key

- **Device IP:** `Menu → System Info → Network → IP Address` (e.g.
  `192.168.1.201`). Confirm it's reachable from the server:
  ```powershell
  Test-NetConnection 192.168.1.201 -Port 4370
  ```
  `TcpTestSucceeded : True` = the port is open (device's "TCP/IP" comm must be
  enabled in its Comm menu for this).
- **Comm key:** on the device, `Menu → Comm → Comm Key`. The default is `0`.
  Whatever is set here **must** match `ZK_COMM_KEY` on the server. pyzk sends
  the scrambled comm key automatically (see Section 5.3) — a mismatch shows up
  as `502 ZKTeco device error` in the API response.

### 5.2 Configure the backend

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

### 5.3 Why the comm key is handled (pyzk + MB360)

The comm key is **not** sent in the clear. pyzk's `connect()` sends an
unauthenticated connect; the device answers "Unauthenticated"; pyzk then sends
`CMD_AUTH` with `make_commkey(comm_key, session_id)` — the standard ZKTeco
XOR-scramble from `commpro.c`. StudySync wires this up via
`ZK_COMM_KEY` → `zkteco/config.py` → `build_zk(password=...)`. So:

- `ZK_COMM_KEY` = the numeric comm key shown on the device.
- Leave it `0` unless you changed it on the device.
- A wrong key = `502` + log line; a correct key = device answers.

### 5.4 Verify the device is reachable through the API

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

### 5.5 Test an actual punch via pyzk

1. Enroll a student on the device with PIN = their `student_id` (e.g. `1`).
2. Have them swipe once. Within ~3–5 seconds the poller picks it up.
3. Verify the attendance row (same as Section 4.4):
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

> The poller clears the device buffer after a successful write. If you also
> had ADMS pointed at the server you may see a swipe arrive twice — harmless,
> the duplicate guard (`apply_punch`) skips the second copy.

---

## 6. Environment-variable reference

Set these in the backend `.env` — dev: `backend\.env`; production:
`C:\ProgramData\StudySync\app\api\.env` (restart `StudySyncAPI` after edits).

| Variable | Default | Meaning |
| --- | --- | --- |
| `ZK_INTEGRATION` | `both` | `both` / `pyzk` / `adms` / `none` — which integration(s) the app mounts. |
| `ZK_DEVICE_IP` | *(unset)* | pyzk: device IP. Unset = pyzk disabled. |
| `ZK_DEVICE_PORT` | `4370` | pyzk: device TCP port. |
| `ZK_COMM_KEY` | `0` | pyzk: device comm key (Section 5.3). |
| `ZK_DEVICE_TIMEOUT` | `30` | Seconds to wait for a device reply. |
| `ZK_ATTENDANCE_MODE` | `poll` | `poll` (periodic buffer pull) or `live` (persistent `live_capture`). One at a time. |
| `ZK_POLL_INTERVAL` | `3` | Seconds between poller cycles (poll mode). |
| `ZK_LIVE_RECONNECT_SECONDS` | `5` | Reconnect backoff for live mode. |
| `ZK_PUNCH_DEBOUNCE_MINUTES` | `1` | A swipe within this many minutes of the student's previous swipe that day is ignored as a double-tap (`0` disables). |
| `ZK_ADMS_ALLOWED_SERIALS` | *(empty)* | Comma-separated device serials allowed to push ADMS data. Empty = accept any. Set once you've confirmed the real serial. |
| `ZK_ADMS_DELAY_SECONDS` | `10` | `Delay` sent to the device in the handshake (getrequest cadence). |
| `ZK_ADMS_ERROR_DELAY_SECONDS` | `30` | `ErrorDelay` — device retry wait after a failed push. |

---

## 7. Pre-flight checklist for the live test

- [ ] `StudySyncAPI` and `StudySyncCaddy` services **Running**.
- [ ] `http://localhost/` returns 200.
- [ ] ADMS: Caddy proxies `/iclock/*` (open
      `http://studysync.local/iclock/cdata?SN=TEST` — or the LAN IP if the
      firmware lacks mDNS — in a browser → shows `GET OPTION FROM:TEST` block).
- [ ] Device and server on the **same network**; firewall allows inbound TCP 80.
- [ ] Device's **Cloud Server Setting**: server address `studysync.local` (if
      the firmware accepts hostnames) or the LAN IP, port **80**.
- [ ] Test student's device PIN == `students.student_id`.
- [ ] (pyzk only) `Test-NetConnection <device-ip> -Port 4370` succeeds and
      `ZK_COMM_KEY` matches the device's Comm key.
- [ ] After the first handshake, `/api/adms/status` shows the device's serial.

---

## 8. Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `/api/adms/status` shows no device | Device can't reach server | Same network; correct IP/port 80; firewall `StudySync HTTP (port 80)` enabled; Caddy `/iclock/*` proxied. |
| Device shows "connection failed" / retries | Handshake blocked or `Encrypt=On` | Set Encrypt Off on device; verify step 7 checklist. |
| Push returns 200 but `imported: 0, unknown_students: N` | Device PIN has no matching student | Enroll with PIN == `student_id`. |
| `imported: 0, duplicates: N` on a fresh punch | Punch within the 1-min debounce of a previous one | Wait >1 min or set `ZK_PUNCH_DEBOUNCE_MINUTES=0`. |
| `/api/zkteco/*` returns **503** | `ZK_DEVICE_IP` not set | Add it to `.env`, restart `StudySyncAPI`. |
| `/api/zkteco/*` returns **502** | Device unreachable, or comm-key mismatch | Check log; `Test-NetConnection ... -Port 4370`; verify `ZK_COMM_KEY`. |
| Punch creates one row per swipe instead of in/out pairs | Poller is reading a device that also has ADMS pushing — dedup usually hides it; or buffer was cleared externally | Use one transport for the test; check `duplicates` tally. |
| After an update the device stops working | `.env` lost its `ZK_*` lines | Update/install now preserves `ZK_*`/`ADMS_*` lines; re-add if a very old installer ran. |

**Logs to check:**
- API: `C:\ProgramData\StudySync\logs\api\api.log` (rotating; look for
  `ADMS handshake from SN=...`, `ADMS punch applied:`, `ZKTeco poll ...`).
- Caddy: `C:\ProgramData\StudySync\logs\caddy\access.log` (see every
  `/iclock/*` hit with status codes).

---

## 9. Cleaning up test data

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
