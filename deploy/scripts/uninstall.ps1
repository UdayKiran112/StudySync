<#
.SYNOPSIS
    Remove the StudySync deployment from this machine.
.DESCRIPTION
    Stops and removes both Windows services, removes the firewall rule and
    scheduled tasks, removes the desktop shortcut, and deletes
    C:\ProgramData\StudySync. A final database backup is written to the
    user's Documents folder BEFORE anything is deleted (the backup is made
    into $APP_DIR\backups first, then copied out to Documents so the
    recursive delete below can never destroy the only copy of the data).
.NOTES
    Requires elevation. Deletes the database after backing it up - handle
    the backup carefully.
#>
[CmdletBinding()]
param(
    [switch]$Yes
)
$ErrorActionPreference = "Stop"
$APP_DIR = "C:\ProgramData\StudySync"

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "ERROR: must run as Administrator." -ForegroundColor Red
    exit 1
}

if (-not $Yes) {
    Write-Host "About to REMOVE StudySync from this machine." -ForegroundColor Red
    $answer = Read-Host "Type REMOVE to confirm"
    if ($answer -ne "REMOVE") { Write-Host "Aborted."; exit 0 }
}

# --- Final backup ---------------------------------------------------------
# backup.exe writes into $APP_DIR\backups, which lives INSIDE the tree that
# is deleted below. Copy the newest backup (or, as a fallback, the live
# database itself) to the user's Documents folder FIRST so the recursive
# delete can never destroy the only copy of the data.
$docsDir = [Environment]::GetFolderPath("MyDocuments")
if (-not $docsDir) { $docsDir = Join-Path $env:USERPROFILE "Documents" }
New-Item -ItemType Directory -Force -Path $docsDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$finalBackup = $null

if (Test-Path "$APP_DIR\scripts\backup.exe") {
    Write-Host "Creating final backup..."
    try {
        & "$APP_DIR\scripts\backup.exe"
        $latestZip = Get-ChildItem -Path "$APP_DIR\backups" -Filter "studysync_*.zip" -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($latestZip) {
            $destZip = Join-Path $docsDir "studysync-final-backup_$stamp.zip"
            Copy-Item -Path $latestZip.FullName -Destination $destZip -Force
            $finalBackup = $destZip
        }
    } catch {
        Write-Host "WARN: backup step failed: $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

if (-not $finalBackup) {
    if (Test-Path "$APP_DIR\data\library.db") {
        Write-Host "No backup zip produced; copying the live database directly instead."
        $destDb = Join-Path $docsDir "studysync-final-backup_$stamp.db"
        Copy-Item -Path "$APP_DIR\data\library.db" -Destination $destDb -Force
        $finalBackup = $destDb
    } else {
        Write-Host "WARNING: no database found to back up." -ForegroundColor Yellow
    }
}
if ($finalBackup) {
    Write-Host "Final backup saved OUTSIDE the app tree, to:" -ForegroundColor Green
    Write-Host "  $finalBackup" -ForegroundColor Green
}

foreach ($svc in @("studysync-api", "studysync-caddy")) {
    $bin = "$APP_DIR\config\winsw\$svc.exe"
    if (Test-Path $bin) {
        & $bin stop 2>$null | Out-Null
        & $bin uninstall 2>$null | Out-Null
    }
}

Get-ScheduledTask -TaskName "StudySyncNightly" -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
Get-ScheduledTask -TaskName "StudySyncServiceCheck" -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
Get-ScheduledTask -TaskName "StudySyncTray" -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
Get-Process -Name "studysync-tray" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
# Legacy task names from older installs (may have been removed by security software)
Get-ScheduledTask -TaskName "StudySync-Backup" -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
Get-ScheduledTask -TaskName "StudySync-HealthWatch" -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
Remove-NetFirewallRule -DisplayName "StudySync HTTP (port 80)" -ErrorAction SilentlyContinue
Remove-NetFirewallRule -DisplayName "StudySync mDNS (UDP 5353)" -ErrorAction SilentlyContinue
Remove-Item -Path "$env:USERPROFILE\Desktop\StudySync.url" -ErrorAction SilentlyContinue

# Remove the dedicated low-privilege service account created by install.ps1.
# It exists only to run the StudySync services; with the services gone there
# is nothing left for it to do (and leaving a password-carrying local user
# behind would be a lingering credential).
if (Get-LocalUser -Name "StudySyncSvc" -ErrorAction SilentlyContinue) {
    Write-Host "Removing StudySync service account..."
    Remove-LocalUser -Name "StudySyncSvc" -ErrorAction SilentlyContinue
}

Remove-Item -Path $APP_DIR -Recurse -Force
Write-Host "StudySync removed." -ForegroundColor Green
Write-Host "Your final database backup is at:" -ForegroundColor Green
Write-Host "  $finalBackup" -ForegroundColor Green
