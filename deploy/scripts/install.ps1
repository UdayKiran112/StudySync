<#
.SYNOPSIS
    Installs StudySync production services on a Windows machine.
.DESCRIPTION
    One-time installation. Copies the pre-built package to
    C:\ProgramData\StudySync, generates a fresh API key, registers the
    StudySync API + Caddy Windows services (running as a dedicated
    low-privilege local account, NOT LocalSystem), opens the firewall for
    port 80 (Private/Domain profiles, RFC1918 LAN addresses only), schedules
    automatic backups and a health watchdog, and starts everything.
    Designed to be run exactly the same way on every machine (staff repeat
    deployment = run this script once, elevated).
.PARAMETER PackageDir
    Path to the pre-built package folder (default: .\package next to this script).
.PARAMETER RotateKey
    Generate a fresh API key instead of reusing the one from a previous
    install. Use this after a key has leaked (see rotate-key.ps1). Staff
    browsers must re-enter the new key.
.NOTES
    Requires elevation. Run:  powershell -ExecutionPolicy Bypass -File install.ps1
#>
[CmdletBinding()]
param(
    [string]$PackageDir = (Join-Path (Split-Path $PSScriptRoot -Parent) "package"),
    [switch]$RotateKey
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

# ---------------------------------------------------------------- service account
# The API and Caddy services run as a DEDICATED low-privilege local account
# ("StudySyncSvc"), never LocalSystem. An RCE in either service must not equal
# full machine compromise. The account's random password lives in the WinSW
# XML files; install/update keep it stable so the Service Control Manager's
# stored credentials (set at service creation) stay valid without ever
# uninstall/reinstalling the registration (which can leave a service stuck
# "marked for deletion").
$SVC_ACCOUNT = "StudySyncSvc"

function Get-ServicePassword {
    # Reuse the password from a previous install if one is recorded in the
    # existing WinSW XML (this is what keeps the SCM credentials valid across
    # reinstalls). A fresh install generates one.
    foreach ($xml in @("$APP_DIR\config\winsw\studysync-api.xml", "$APP_DIR\config\winsw\studysync-caddy.xml")) {
        if (Test-Path $xml) {
            $pw = (Select-String -Path $xml -Pattern '<password>(.+)</password>' -ErrorAction SilentlyContinue | Select-Object -First 1).Matches.Groups[1].Value
            if ($pw -and $pw -ne "__STUDYSYNC_SVC_PASSWORD__") { return $pw }
        }
    }
    return (New-ApiKey -length 32)
}

function Ensure-ServiceAccount([string]$password) {
    $secure = ConvertTo-SecureString $password -AsPlainText -Force
    if (Get-LocalUser -Name $SVC_ACCOUNT -ErrorAction SilentlyContinue) {
        Set-LocalUser -Name $SVC_ACCOUNT -Password $secure -PasswordNeverExpires $true -UserMayNotChangePassword $true
        Write-Log "Service account '$SVC_ACCOUNT' exists; password refreshed (kept stable with the SCM registration)."
    } else {
        New-LocalUser -Name $SVC_ACCOUNT -Password $secure `
            -FullName "StudySync Service Account" `
            -Description "Low-privilege account that runs the StudySync API and Caddy services" `
            -AccountNeverExpires $true -PasswordNeverExpires $true -UserMayNotChangePassword $true | Out-Null
        Write-Log "Created dedicated low-privilege service account '$SVC_ACCOUNT'."
    }
}

function Set-ServiceAccountInXml([string]$xmlPath, [string]$password) {
    if (-not (Test-Path $xmlPath)) { return }
    $content = Get-Content -Path $xmlPath -Raw
    $content = $content -replace '<password>__STUDYSYNC_SVC_PASSWORD__</password>', "<password>$password</password>"
    Set-Content -Path $xmlPath -Value $content -Encoding ASCII -NoNewline
}

function Set-SecureAcls {
    # The package tree currently inherits C:\ProgramData's default ACL, which
    # gives BUILTIN\Users read access to EVERYTHING - including the Caddy
    # access log that previously leaked API keys and the WinSW XML that now
    # carries the service-account password. Strip the inherited ACEs and grant
    # access to exactly: SYSTEM, Administrators, the interactive installing
    # user (for the scheduled tasks) and the service account (read-only for
    # the app, write access to data/logs/backups).
    $system       = "NT AUTHORITY\SYSTEM"
    $admins       = "BUILTIN\Administrators"
    $interactive  = "$env:USERDOMAIN\$env:USERNAME"
    $svc          = "$env:COMPUTERNAME\$SVC_ACCOUNT"

    if (-not (Get-LocalUser -Name $SVC_ACCOUNT -ErrorAction SilentlyContinue)) {
        throw "Service account '$SVC_ACCOUNT' must exist before hardening ACLs."
    }

    & icacls $APP_DIR /inheritance:r /T /Q 2>$null | Out-Null
    & icacls $APP_DIR /grant:r "${system}:(OI)(CI)F" "${admins}:(OI)(CI)F" "${interactive}:(OI)(CI)F" "${svc}:(OI)(CI)RX" /T /Q 2>$null | Out-Null
    foreach ($writable in @("data", "logs", "backups")) {
        $p = Join-Path $APP_DIR $writable
        if (Test-Path $p) {
            & icacls $p /grant:r "${svc}:(OI)(CI)M" /T /Q 2>$null | Out-Null
        }
    }
    if ($LASTEXITCODE -ne 0) { throw "ACL hardening failed (icacls exit $LASTEXITCODE)" }
    Write-Log "ACLs hardened: inherited BUILTIN\Users access removed from $APP_DIR."
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

# The watchdog (every 5 min) and backup (nightly) may be mid-run right now and
# holding healthcheck.exe / backup.exe open. If the file copy below hits a
# locked exe, $ErrorActionPreference=Stop aborts the whole install midway
# (services stopped, firewall/tasks skipped, install.log truncated). Stop the
# tasks and kill any running instance so the copy can overwrite them. Silent on
# first install where the tasks/processes do not exist yet.
foreach ($task in @("StudySyncServiceCheck", "StudySyncNightly", "StudySyncTray")) {
    Stop-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
}
foreach ($proc in @("healthcheck", "backup", "studysync-tray")) {
    Get-Process -Name $proc -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 1

foreach ($rel in @("app\api", "app\frontend", "app\caddy", "config\winsw", "scripts", "tools")) {
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
foreach ($d in @("data", "backups", "logs\api", "logs\caddy", "logs\winsw", "logs\backup", "logs\health", "logs\tray", "logs\installer", "scripts")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $APP_DIR $d) | Out-Null
}
Write-Log "Data/log folders created."

# ---------------------------------------------- service account + ACLs
# Patch the WinSW XMLs with the service account's password (before the
# services are registered) and lock the tree down so BUILTIN\Users can no
# longer read logs/secrets. Runs after the file copy and folder creation so
# the ACLs cover everything, and before services start so the low-privilege
# account can write its data/logs from the first boot.
$svcPass = Get-ServicePassword
Ensure-ServiceAccount $svcPass
Set-ServiceAccountInXml "$APP_DIR\config\winsw\studysync-api.xml"  $svcPass
Set-ServiceAccountInXml "$APP_DIR\config\winsw\studysync-caddy.xml" $svcPass
Write-Log "WinSW XMLs configured to run services as '$SVC_ACCOUNT' (low-privilege)."
Set-SecureAcls

# ------------------------------------------------------ generate API key
$envFile = "$APP_DIR\app\api\.env"
$apiKey = $null
if ($RotateKey) {
    $apiKey = New-ApiKey
    Write-Log "Generating a FRESH API key (-RotateKey requested)."
} elseif (Test-Path $envFile) {
    $existing = (Select-String -Path $envFile -Pattern '^STUDYSYNC_API_KEY=(.+)$' -ErrorAction SilentlyContinue | Select-Object -First 1).Matches.Groups[1].Value
    if ($existing) { $apiKey = $existing }
}
if (-not $apiKey) {
    $apiKey = New-ApiKey
    Write-Log "Generated fresh API key (no existing key found)."
} else {
    Write-Log "Reusing existing API key (reinstall preserves staff Settings)."
}
# Preserve any device-integration / operator / sync settings from a previous
# .env (ZK_DEVICE_IP, ZK_COMM_KEY, ZK_INTEGRATION, GOOGLE_SPREADSHEET_ID,
# GOOGLE_CREDS_FILE, STUDYSYNC_ALLOWED_ORIGINS, STUDYSYNC_HOST/PORT, ...) so a
# ZKTeco device configured once, or a Google Sheets setup, keeps working after
# an install/update instead of silently losing its connection settings.
$extraLines = @()
if (Test-Path $envFile) {
    $extraLines = Get-Content -Path $envFile | Where-Object {
        $_ -match '^\s*(ZK_|GOOGLE_|STUDYSYNC_(ALLOWED_ORIGINS|HOST|PORT))' -and $_ -notmatch '^\s*#'
    }
}
@(
    "# StudySync production environment (auto-generated by install.ps1)"
    "STUDYSYNC_API_KEY=$apiKey"
    "STUDYSYNC_DB_PATH=$APP_DIR\data\library.db"
) + $extraLines | Set-Content -Path $envFile -Encoding ASCII
# NOTE: deliberately NOT logging the key value here. install.log is ACL-locked
# now, but a secret that never gets written is a secret that can never leak.
Write-Log "API key written to $envFile"

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

# Assert the service account on BOTH registrations. On a fresh install WinSW
# already created them with StudySyncSvc (via <serviceaccount> in the XML);
# on a reinstall/upgrade of a pre-hardening machine the registration may
# still be LocalSystem, and only the SCM (not the XML) decides which account
# the process runs under. sc.exe config migrates it in place - no
# uninstall/install, so no "marked for deletion" race. The SCM also grants
# "Log on as a service" automatically for the new account. The password is
# passed as an argument to sc.exe; output is suppressed so it is never
# echoed to the install log.
foreach ($svcName in @("StudySyncAPI", "StudySyncCaddy")) {
    & sc.exe config $svcName obj= ".\$SVC_ACCOUNT" password= $svcPass | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "sc.exe config $svcName failed (exit $LASTEXITCODE)" }
}
Write-Log "Both services configured to run as '$SVC_ACCOUNT'."

Write-Log "Starting StudySync API service..."
Invoke-WinSw $apiBin "start"
Write-Log "Starting StudySync Caddy service..."
Invoke-WinSw $caddyBin "start"

# ----------------------------------------------------------- firewall
# Deliberately NOT "all profiles / any remote address": that left the app
# (and the staff API key + student PII, which travelled over cleartext HTTP)
# reachable from any interface, including a WAN-facing one, on any network
# Windows marked Public. The rules now allow inbound traffic ONLY:
#   * on Private/Domain network profiles (a Public/cafe/venue network gets
#     no inbound access at all - that is the safe default), and
#   * from RFC1918 private LAN addresses plus loopback (never from the
#     internet). RemoteAddress filtering is what keeps a machine that sits
#     behind a public IP from exposing port 80 to the whole internet.
# Recreated on every run so the settings stay correct after a reinstall.
$ruleName = "StudySync HTTP (port 80)"
Remove-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP `
    -LocalPort 80 -Action Allow -Profile Private, Domain `
    -RemoteAddress 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 127.0.0.0/8 | Out-Null
Write-Log "Firewall rule created for inbound port 80 (Private/Domain profiles, RFC1918 LAN + loopback only)."

# mDNS (UDP 5353): lets devices resolve http://studysync.local by name. Without
# an inbound rule the responder may not see queries, so clients could not
# resolve the name even though the app advertises it. Restricted to
# Private/Domain like the HTTP rule.
$mdnsRule = "StudySync mDNS (UDP 5353)"
Remove-NetFirewallRule -DisplayName $mdnsRule -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName $mdnsRule -Direction Inbound -Protocol UDP `
    -LocalPort 5353 -Action Allow -Profile Private, Domain | Out-Null
Write-Log "Firewall rule created for inbound mDNS (UDP 5353, Private/Domain only)."

# ------------------------------------------------- Bonjour for Windows (client PCs only)
# The StudySync server advertises http://studysync.local itself over mDNS
# (the API's zeroconf responder), so it must NOT also run Apple Bonjour --
# two mDNS responders on one machine fight over UDP 5353 and break the name.
# Bonjour is only for OTHER Windows PCs on the LAN, which cannot resolve
# *.local without it (Windows' built-in DNS client only resolves the machine's
# own hostname). The MSI stays under $APP_DIR\tools so staff PCs can be set
# up from the server; it is deliberately NOT installed here. If Bonjour is
# already present on this server (installed for other reasons), the API skips
# its own mDNS advertisement rather than conflict with it.
$bonjourMsi = "$APP_DIR\tools\Bonjour64.msi"
if (Get-Service -Name "Bonjour Service" -ErrorAction SilentlyContinue) {
    Write-Log "Bonjour already installed on this server - the API will skip its own mDNS advertisement (see api.log)."
} elseif (Test-Path $bonjourMsi) {
    Write-Log "Bonjour MSI kept at $APP_DIR\tools\Bonjour64.msi for Windows staff PCs (not installed here - the server advertises mDNS itself)."
} else {
    Write-Log "WARN: Bonjour64.msi not found in tools; Windows staff PCs will need http://<server-IP> instead of http://studysync.local."
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

# Health watchdog every 5 minutes (elevated so it can restart a service and
# auto-restore the database from a backup when it is missing/corrupt). The
# long execution limit covers the rare case where the restore has to pull the
# newest backup down from Google Drive first.
$healthAction = New-ScheduledTaskAction -Execute "$APP_DIR\scripts\healthcheck.exe" -WorkingDirectory $APP_DIR
$healthTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
$healthSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
Register-ScheduledTask -TaskName "StudySyncServiceCheck" -Action $healthAction -Trigger $healthTrigger -Principal $taskPrincipal -Settings $healthSettings -Description "Every 5 min: verify StudySync services, restart if down, auto-restore a missing/corrupt database from backup" -Force | Out-Null
Write-Log "Scheduled task 'StudySyncServiceCheck' registered (every 5 minutes, $taskUser, battery-safe)."

# System-tray monitor at logon (elevated so it can restart services from the
# tray without a UAC prompt; windowless exe so nothing flashes). Shows live
# service status in the notification area like Bluetooth/McAfee icons.
$trayExe = "$APP_DIR\scripts\studysync-tray.exe"
if (Test-Path $trayExe) {
    $trayAction = New-ScheduledTaskAction -Execute $trayExe -WorkingDirectory $APP_DIR
    $trayTrigger = New-ScheduledTaskTrigger -AtLogOn
    $traySettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Days 1)
    Register-ScheduledTask -TaskName "StudySyncTray" -Action $trayAction -Trigger $trayTrigger -Principal $taskPrincipal -Settings $traySettings -Description "Show StudySync service status in the system tray" -Force | Out-Null
    Write-Log "Scheduled task 'StudySyncTray' registered (at logon, $taskUser)."
    Start-Process $trayExe -WindowStyle Hidden
    Write-Log "StudySync tray monitor started."
} else {
    Write-Log "WARN: studysync-tray.exe not found in package; tray monitor not installed."
}

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
Write-Host "  App URL : http://localhost   (LAN: http://studysync.local)" -ForegroundColor Green
Write-Host "  API key: $apiKey" -ForegroundColor Yellow
Write-Host "  Save the API key somewhere safe. Staff enter it ONCE per browser in Settings." -ForegroundColor Yellow
Write-Host "  Services run as the low-privilege '$SVC_ACCOUNT' account (not LocalSystem)." -ForegroundColor Cyan
Write-Host "  After a key leak: run scripts\rotate-key.ps1 (or re-run this script with -RotateKey)." -ForegroundColor Cyan
Write-Host "  LAN access: Apple/Android use http://studysync.local directly; Windows PCs need" -ForegroundColor Cyan
Write-Host "  Apple Bonjour (kept at $APP_DIR\tools\Bonjour64.msi - install it on each staff PC," -ForegroundColor Cyan
Write-Host "  NOT on this server, which advertises the name itself)." -ForegroundColor Cyan
