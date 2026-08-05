<#
.SYNOPSIS
    Remove the StudySync deployment from this machine.
.DESCRIPTION
    Stops and removes both Windows services, removes the firewall rule and
    scheduled tasks, removes the desktop shortcut, and deletes
    C:\ProgramData\StudySync. A final database backup is written to the
    user's Documents folder before anything is deleted.
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

if (Test-Path "$APP_DIR\scripts\backup.exe") {
    Write-Host "Creating final backup..."
    & "$APP_DIR\scripts\backup.exe"
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
# Legacy task names from older installs (may have been removed by security software)
Get-ScheduledTask -TaskName "StudySync-Backup" -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
Get-ScheduledTask -TaskName "StudySync-HealthWatch" -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
Remove-NetFirewallRule -DisplayName "StudySync HTTP (port 80)" -ErrorAction SilentlyContinue
Remove-Item -Path "$env:USERPROFILE\Desktop\StudySync.url" -ErrorAction SilentlyContinue

Remove-Item -Path $APP_DIR -Recurse -Force
Write-Host "StudySync removed." -ForegroundColor Green
