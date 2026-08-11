<#
.SYNOPSIS
    Rotates the StudySync API key in place.
.DESCRIPTION
    Generates a fresh STUDYSYNC_API_KEY, writes it to the live .env, and
    restarts the API service so the new key takes effect immediately. The
    old key stops working the moment the API comes back up.

    Run this after a key has been exposed (see the security audit: the key
    previously travelled over cleartext HTTP and landed in Caddy's access
    log). Every staff browser must then re-enter the new key in Settings.

    This does NOT touch Caddy's Caddyfile or the database, and it does not
    reinstall anything - it is safe to run on a live deployment.

    Usage (elevated):
        powershell -ExecutionPolicy Bypass -File rotate-key.ps1
.NOTES
    The key is printed to the console only - it is never written to a log
    file. install.ps1 -RotateKey does the same job at (re)install time and
    is the right choice when the machine is being reinstalled anyway.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$APP_DIR = "C:\ProgramData\StudySync"
$LOG_DIR = "$APP_DIR\logs\installer"
$installLog = Join-Path $LOG_DIR "rotate-key.log"
New-Item -ItemType Directory -Force -Path $LOG_DIR | Out-Null

function Write-Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | $msg"
    Write-Host $line
    Add-Content -Path $installLog -Value $line -Encoding UTF8
}

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "ERROR: must run as Administrator." -ForegroundColor Red
    exit 1
}

$envFile = "$APP_DIR\app\api\.env"
if (-not (Test-Path $envFile)) {
    Write-Host "ERROR: $envFile not found. Is StudySync installed?" -ForegroundColor Red
    exit 1
}

function New-ApiKey([int]$length = 48) {
    $chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'
    -join (1..$length | ForEach-Object { $chars[(Get-Random -Maximum $chars.Length)] })
}

# 1. Capture the current .env so the rotation only touches the key line and
#    never clobbers device-integration / operator settings.
$lines = Get-Content -Path $envFile
if (-not ($lines -match '^STUDYSYNC_API_KEY=')) {
    Write-Host "ERROR: no STUDYSYNC_API_KEY line in $envFile." -ForegroundColor Red
    exit 1
}

# 2. Generate and write the new key. Written to disk only, never logged.
$oldKey = ($lines | Select-String -Pattern '^STUDYSYNC_API_KEY=(.+)$').Matches[0].Groups[1].Value
$newKey = New-ApiKey
$rotated = $lines | ForEach-Object {
    if ($_ -match '^STUDYSYNC_API_KEY=') { "STUDYSYNC_API_KEY=$newKey" } else { $_ }
}
$rotated | Set-Content -Path $envFile -Encoding ASCII
Write-Log "Rotated STUDYSYNC_API_KEY in $envFile (old key is now invalid)."

# 3. Restart the API so the new key is read. Caddy just proxies and holds no
#    key, so it does not need a restart. sc.exe (not WinSW's `stop`) avoids the
#    unreliable-stop issue seen on some installs.
sc.exe stop StudySyncAPI | Out-Null
$deadline = (Get-Date).AddSeconds(60)
while ((Get-Date) -lt $deadline) {
    if ((Get-Service StudySyncAPI -ErrorAction SilentlyContinue).Status -eq 'Stopped') { break }
    Start-Sleep -Milliseconds 500
}
sc.exe start StudySyncAPI | Out-Null
Write-Log "StudySyncAPI restarted with the new key."

Write-Host ""
Write-Host "Key rotated. StudySync is running with the NEW key." -ForegroundColor Green
Write-Host "  Old key: no longer valid." -ForegroundColor Yellow
Write-Host "  New key: $newKey" -ForegroundColor Yellow
Write-Host ""
Write-Host "Staff must re-enter the new key ONCE per browser in Settings." -ForegroundColor Cyan
Write-Host "(This is the last time the key is shown; it is not written to any log.)" -ForegroundColor Cyan
