<#
.SYNOPSIS
    Update an installed StudySync deployment from a new package.
.DESCRIPTION
    Safely swaps the application code while preserving the database,
    configuration (API key), backups, and logs.

    Usage (elevated):
        powershell -ExecutionPolicy Bypass -File update.ps1 -PackageDir "C:\path\to\new\package"

    The package layout must match build-package.ps1 output. After the copy,
    both services are restarted. Data in $APP_DIR\data is never touched.
.NOTES
    If a database schema change is required, ship a migration step and run it
    from the new code BEFORE starting the API (update.ps1 pauses for this).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PackageDir
)

$ErrorActionPreference = "Stop"
$APP_DIR = "C:\ProgramData\StudySync"
$LOG_DIR = "$APP_DIR\logs\installer"
$LOG = Join-Path $LOG_DIR "update.log"
New-Item -ItemType Directory -Force -Path $LOG_DIR | Out-Null

function Write-Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | $msg"
    Write-Host $line
    Add-Content -Path $LOG -Value $line -Encoding UTF8
}

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "ERROR: must run as Administrator." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $PackageDir)) { Write-Host "ERROR: package not found: $PackageDir" -ForegroundColor Red; exit 1 }
if (-not (Test-Path "$APP_DIR\app\api\.env")) {
    Write-Host "ERROR: $APP_DIR\app\api\.env not found. Aborting before stopping anything; no changes made." -ForegroundColor Red
    exit 1
}

# Capture the API key, DB path and any device-integration / operator settings
# from the live .env BEFORE stopping anything, so a config problem aborts the
# update while the install is still fully untouched. The package ships no .env,
# so these values are written back after the swap (never rotate the key).
$apiKey = (Select-String -Path "$APP_DIR\app\api\.env" -Pattern '^STUDYSYNC_API_KEY=(.+)$').Matches[0].Groups[1].Value
$dbEnv = (Select-String -Path "$APP_DIR\app\api\.env" -Pattern '^STUDYSYNC_DB_PATH=(.+)$').Matches[0].Groups[1].Value
$extraLines = Get-Content -Path "$APP_DIR\app\api\.env" | Where-Object {
    $_ -match '^\s*(ZK_|STUDYSYNC_(ALLOWED_ORIGINS|HOST|PORT))' -and $_ -notmatch '^\s*#'
}
Write-Log "Preserving API key (kept from current install)."

# 1. Stop services so files are not locked. Use the Service Control Manager
#    (sc.exe) rather than WinSW's `stop` subcommand, which on some installs
#    fails with "Cannot stop '<name>' service on computer '.'" and leaves the
#    old processes running - a locked config\winsw\*.exe then aborts the copy
#    midway, leaving the install half-updated.
Write-Log "Stopping services..."
foreach ($svc in @("StudySyncAPI", "StudySyncCaddy")) {
    sc.exe stop $svc | Out-Null
}
# Poll until both are actually stopped (sc.exe returns before the stop
# completes); force the service and kill any leftover wrapped processes so the
# file copy below can never hit a locked exe.
$deadline = (Get-Date).AddSeconds(60)
while ((Get-Date) -lt $deadline) {
    $stillUp = @("StudySyncAPI", "StudySyncCaddy") |
        Where-Object { (Get-Service $_ -ErrorAction SilentlyContinue).Status -ne 'Stopped' }
    if (-not $stillUp) { break }
    Start-Sleep -Milliseconds 500
}
Stop-Service -Name StudySyncAPI, StudySyncCaddy -Force -ErrorAction SilentlyContinue
foreach ($proc in @("studysync-api", "studysync-caddy", "caddy")) {
    Get-Process -Name $proc -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2

# 2. Snapshot of the current data (cheap safety).
Write-Host "Creating a safety backup before update..." -ForegroundColor Yellow
& "$APP_DIR\scripts\backup.exe" | Out-Null

# Stop the watchdog/backup tasks and kill any running instances so the file
# copy below is not blocked by a locked healthcheck.exe / backup.exe
# ($ErrorActionPreference=Stop would otherwise abort the update midway).
foreach ($task in @("StudySyncServiceCheck", "StudySyncNightly", "StudySyncTray")) {
    Stop-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
}
foreach ($proc in @("healthcheck", "backup", "studysync-tray")) {
    Get-Process -Name $proc -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 1

# 3. Replace application code. The venv ships inside the package, so the
#    whole app/api and app/frontend and app/caddy trees are swapped.
foreach ($rel in @("app\api", "app\frontend", "app\caddy", "config\winsw", "scripts", "tools")) {
    $src = Join-Path $PackageDir $rel
    if (-not (Test-Path $src)) { Write-Log "WARN: missing $rel in package"; continue }
    $dst = Join-Path $APP_DIR $rel
    Remove-Item -Path $dst -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    Copy-Item -Path "$src\*" -Destination $dst -Recurse -Force
}
Write-Log "New application files copied."

# 4. Recreate .env with the SAME api key and db path (never rotate the key on
#    update), plus the preserved device/operator settings captured above.
@(
    "# StudySync production environment (preserved across updates)"
    "STUDYSYNC_API_KEY=$apiKey"
    "STUDYSYNC_DB_PATH=$dbEnv"
) + $extraLines | Set-Content -Path "$APP_DIR\app\api\.env" -Encoding ASCII
Write-Log ".env preserved."

# 5. Restart services via the Service Control Manager (sc.exe) rather than
#    WinSW's `start` subcommand, which is unreliable on some installs (same
#    class of bug as the `stop` failure above). Do NOT re-run `install`: the
#    registrations already exist and WinSW re-reads its XML on every start, so
#    an install here would fail with "service already exists" and, worse, an
#    uninstall -> install cycle can leave the service stuck "marked for
#    deletion".
sc.exe start StudySyncAPI | Out-Null
Start-Sleep -Seconds 4
sc.exe start StudySyncCaddy | Out-Null

# Restart the tray monitor if installed (task re-registers it at next logon;
# starting it now picks up the new exe immediately).
if (Test-Path "$APP_DIR\scripts\studysync-tray.exe") {
    Start-Process "$APP_DIR\scripts\studysync-tray.exe" -WindowStyle Hidden
}

Write-Log "Update complete. Services restarted. Verify at http://localhost"
Write-Host "Update complete." -ForegroundColor Green
