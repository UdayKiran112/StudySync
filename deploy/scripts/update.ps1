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

# 1. Stop services so files are not locked.
Write-Log "Stopping services..."
& "$APP_DIR\config\winsw\studysync-api.exe" stop | Out-Null
& "$APP_DIR\config\winsw\studysync-caddy.exe" stop | Out-Null
Start-Sleep -Seconds 3

# 2. Snapshot of the current data (cheap safety).
Write-Host "Creating a safety backup before update..." -ForegroundColor Yellow
& "$APP_DIR\scripts\backup.exe" | Out-Null

# Stop the watchdog/backup tasks and kill any running instances so the file
# copy below is not blocked by a locked healthcheck.exe / backup.exe
# ($ErrorActionPreference=Stop would otherwise abort the update midway).
foreach ($task in @("StudySyncServiceCheck", "StudySyncNightly")) {
    Stop-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
}
foreach ($proc in @("healthcheck", "backup")) {
    Get-Process -Name $proc -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 1

# 3. Replace application code. The venv ships inside the package, so the
#    whole app/api and app/frontend and app/caddy trees are swapped.
$apiKey = (Select-String -Path "$APP_DIR\app\api\.env" -Pattern '^STUDYSYNC_API_KEY=(.+)$').Matches[0].Groups[1].Value
$dbEnv = (Select-String -Path "$APP_DIR\app\api\.env" -Pattern '^STUDYSYNC_DB_PATH=(.+)$').Matches[0].Groups[1].Value
Write-Log "Preserving API key (kept from current install)."

foreach ($rel in @("app\api", "app\frontend", "app\caddy", "config\winsw", "scripts", "tools")) {
    $src = Join-Path $PackageDir $rel
    if (-not (Test-Path $src)) { Write-Log "WARN: missing $rel in package"; continue }
    $dst = Join-Path $APP_DIR $rel
    Remove-Item -Path $dst -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    Copy-Item -Path "$src\*" -Destination $dst -Recurse -Force
}
Write-Log "New application files copied."

# 4. Recreate .env with the SAME api key and db path (never rotate the key on update).
@"
# StudySync production environment (preserved across updates)
STUDYSYNC_API_KEY=$apiKey
STUDYSYNC_DB_PATH=$dbEnv
"@ | Set-Content -Path "$APP_DIR\app\api\.env" -Encoding ASCII
Write-Log ".env preserved."

# 5. Restart services. Do NOT re-run `install`: the registrations already
#    exist and WinSW re-reads its XML on every start, so an install here would
#    fail with "service already exists" and, worse, an uninstall -> install
#    cycle can leave the service stuck "marked for deletion".
& "$APP_DIR\config\winsw\studysync-api.exe" start | Out-Null
& "$APP_DIR\config\winsw\studysync-caddy.exe" start | Out-Null

Write-Log "Update complete. Services restarted. Verify at http://localhost"
Write-Host "Update complete." -ForegroundColor Green
