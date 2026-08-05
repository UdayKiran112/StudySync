<#
.SYNOPSIS
    Installs StudySync production services on a Windows machine.
.DESCRIPTION
    One-time installation. Copies the pre-built package to
    C:\ProgramData\StudySync, generates a fresh API key, registers the
    StudySync API + Caddy Windows services, opens the firewall for port 80,
    schedules automatic backups and a health watchdog, and starts everything.
    Designed to be run exactly the same way on every machine (staff repeat
    deployment = run this script once, elevated).
.PARAMETER PackageDir
    Path to the pre-built package folder (default: .\package next to this script).
.NOTES
    Requires elevation. Run:  powershell -ExecutionPolicy Bypass -File install.ps1
#>
[CmdletBinding()]
param(
    [string]$PackageDir = (Join-Path (Split-Path $PSScriptRoot -Parent) "package")
)

$ErrorActionPreference = "Stop"
$APP_DIR = "C:\ProgramData\StudySync"
$LOG_DIR = "$APP_DIR\logs\installer"
$installLog = Join-Path $LOG_DIR "install.log"

function Write-Log($msg) {
    New-Item -ItemType Directory -Force -Path $LOG_DIR | Out-Null
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | $msg"
    Write-Host $line
    Add-Content -Path $installLog -Value $line -Encoding UTF8
}

function Assert-Admin {
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin) {
        Write-Host "ERROR: This script must run as Administrator." -ForegroundColor Red
        Write-Host "Right-click PowerShell and choose 'Run as administrator', then re-run." -ForegroundColor Yellow
        exit 1
    }
}

function New-ApiKey([int]$length = 48) {
    $chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'
    -join (1..$length | ForEach-Object { $chars[(Get-Random -Maximum $chars.Length)] })
}

function Invoke-WinSw([string]$binary, [string]$action, [bool]$failOnError = $true) {
    & $binary $action 2>&1 | Out-String | ForEach-Object { if ($_ -match "^\S") { Write-Log "  winsw[$action]: $_" } }
    if ($failOnError -and $LASTEXITCODE -ne 0) { throw "WinSW '$action' failed for $binary (exit $LASTEXITCODE)" }
}

# ---------------------------------------------------------------- checks
Assert-Admin
if (-not (Test-Path $PackageDir)) {
    Write-Host "ERROR: package folder not found: $PackageDir" -ForegroundColor Red
    Write-Host "Run build-package.ps1 first to produce the package." -ForegroundColor Yellow
    exit 1
}
New-Item -ItemType Directory -Force -Path $APP_DIR | Out-Null
Write-Log "Installing StudySync from $PackageDir to $APP_DIR"

# --------------------------------------------------------- copy package
# Stop existing services first so file replacement never hits locked DLLs
# (WinSW binaries may not exist yet on a first install - that is fine).
$apiBin   = "$APP_DIR\config\winsw\studysync-api.exe"
$caddyBin = "$APP_DIR\config\winsw\studysync-caddy.exe"
if (Test-Path $apiBin)   { Write-Log "Stopping existing StudySyncAPI service...";   Invoke-WinSw $apiBin "stop" $false }
if (Test-Path $caddyBin) { Write-Log "Stopping existing StudySyncCaddy service..."; Invoke-WinSw $caddyBin "stop" $false }

foreach ($rel in @("app\api", "app\frontend", "app\caddy", "config\winsw", "scripts")) {
    $src = Join-Path $PackageDir $rel
    if (-not (Test-Path $src)) { Write-Host "WARN: missing $rel in package" -ForegroundColor Yellow; continue }
    $dst = Join-Path $APP_DIR $rel
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    Copy-Item -Path "$src\*" -Destination $dst -Recurse -Force
}
Write-Log "Application files copied."

# ---------------------------------------------- seed database (optional)
$seedDb = Join-Path $PackageDir "data\library.db"
if (Test-Path $seedDb) {
    $dataDir = "$APP_DIR\data"
    New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
    $targetDb = Join-Path $dataDir "library.db"
    if (-not (Test-Path $targetDb)) {
        Copy-Item $seedDb $targetDb -Force
        Write-Log "Seeded existing database from package (no prior data present)."
    } else {
        Write-Log "Existing database found; seed skipped to protect data."
    }
}

# ------------------------------------------------- create data/log folders
foreach ($d in @("data", "backups", "logs\api", "logs\caddy", "logs\winsw", "logs\backup", "logs\health", "logs\installer", "scripts")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $APP_DIR $d) | Out-Null
}
Write-Log "Data/log folders created."

# ------------------------------------------------------ generate API key
$envFile = "$APP_DIR\app\api\.env"
$apiKey = $null
if (Test-Path $envFile) {
    $existing = (Select-String -Path $envFile -Pattern '^STUDYSYNC_API_KEY=(.+)$' -ErrorAction SilentlyContinue | Select-Object -First 1).Matches.Groups[1].Value
    if ($existing) { $apiKey = $existing }
}
if (-not $apiKey) {
    $apiKey = New-ApiKey
    Write-Log "Generated fresh API key (no existing key found)."
} else {
    Write-Log "Reusing existing API key (reinstall preserves staff Settings)."
}
@"
# StudySync production environment (auto-generated by install.ps1)
STUDYSYNC_API_KEY=$apiKey
STUDYSYNC_DB_PATH=$APP_DIR\data\library.db
"@ | Set-Content -Path $envFile -Encoding ASCII
Write-Log "API key written to $envFile"
Write-Log "API key: $apiKey"

# ------------------------------------------------------------ services
$apiBin   = "$APP_DIR\config\winsw\studysync-api.exe"
$caddyBin = "$APP_DIR\config\winsw\studysync-caddy.exe"

if (-not (Test-Path $apiBin) -or -not (Test-Path $caddyBin)) {
    Write-Host "ERROR: WinSW binaries missing in package. Re-run build-package.ps1." -ForegroundColor Red
    exit 1
}

function Ensure-WinSwService([string]$binary, [string]$serviceName) {
    # NEVER uninstall an existing registration during a reinstall. WinSW
    # re-reads its XML on every start and the ImagePath is a stable path, so a
    # stop (file swap) + start is all a reinstall needs. An uninstall ->
    # install cycle can race the Service Control Manager and leave the service
    # "marked for deletion" (delete-pending) forever, after which it can never
    # be started or reinstalled without a reboot.
    $existing = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Log "  $serviceName already registered - stopping for file swap."
        Invoke-WinSw $binary "stop" $false
    } else {
        Write-Log "  $serviceName not registered - installing new service."
        Invoke-WinSw $binary "install"
    }
}

Write-Log "Registering StudySync API service..."
Ensure-WinSwService $apiBin "StudySyncAPI"

Write-Log "Registering StudySync Caddy service..."
Ensure-WinSwService $caddyBin "StudySyncCaddy"

Write-Log "Starting StudySync API service..."
Invoke-WinSw $apiBin "start"
Write-Log "Starting StudySync Caddy service..."
Invoke-WinSw $caddyBin "start"

# ----------------------------------------------------------- firewall
$ruleName = "StudySync HTTP (port 80)"
if (-not (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP `
        -LocalPort 80 -Action Allow -Profile Private, Domain | Out-Null
    Write-Log "Firewall rule created for inbound port 80 (Private/Domain only)."
} else {
    Write-Log "Firewall rule already exists."
}

# -------------------------------------------------- scheduled tasks
# Tasks run as the installing (admin) user with an interactive logon.
# Note: on some machines security software silently deletes SYSTEM-run
# tasks, so a normal admin user is the reliable, portable choice. The task
# runs whenever this user is logged in; StartWhenAvailable catches missed
# runs (e.g. the machine was off at 02:00).
$taskUser = "$env:USERDOMAIN\$env:USERNAME"
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $taskUser -LogonType Interactive -RunLevel Highest

# Daily backup at 02:00
$backupAction = New-ScheduledTaskAction -Execute "$APP_DIR\scripts\backup.exe" -WorkingDirectory $APP_DIR
$backupTrigger = New-ScheduledTaskTrigger -Daily -At 2:00am
$backupSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName "StudySyncNightly" -Action $backupAction -Trigger $backupTrigger -Principal $taskPrincipal -Settings $backupSettings -Description "Nightly StudySync database backup" -Force | Out-Null
Write-Log "Scheduled task 'StudySyncNightly' registered (02:00 daily, $taskUser, battery-safe)."

# Health watchdog every 5 minutes (elevated so it can restart a service)
$healthAction = New-ScheduledTaskAction -Execute "$APP_DIR\scripts\healthcheck.exe" -WorkingDirectory $APP_DIR
$healthTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
$healthSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
Register-ScheduledTask -TaskName "StudySyncServiceCheck" -Action $healthAction -Trigger $healthTrigger -Principal $taskPrincipal -Settings $healthSettings -Description "Every 5 min: verify StudySync services and restart if down" -Force | Out-Null
Write-Log "Scheduled task 'StudySyncServiceCheck' registered (every 5 minutes, $taskUser, battery-safe)."

# ----------------------------------------------------- desktop shortcut
$desktop = [Environment]::GetFolderPath("Desktop")
$lnkPath = Join-Path $desktop "StudySync.url"
@"
[InternetShortcut]
URL=http://localhost
IconIndex=0
"@ | Set-Content -Path $lnkPath -Encoding ASCII
Write-Log "Desktop shortcut created: $lnkPath"

# --------------------------------------------------------------- done
Write-Log "Installation complete."
Write-Host "`nInstallation complete." -ForegroundColor Green
Write-Host "  App URL : http://localhost   (LAN: http://<this-PC-IP>)" -ForegroundColor Green
Write-Host "  API key: $apiKey" -ForegroundColor Yellow
Write-Host "  Save the API key somewhere safe. Staff enter it ONCE per browser in Settings." -ForegroundColor Yellow
