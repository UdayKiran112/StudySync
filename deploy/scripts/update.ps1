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
    # A write failure here must never abort the update (a protected/empty DACL
    # on the log file after a partial hardening run should not block recovery).
    try {
        Add-Content -Path $LOG -Value $line -Encoding UTF8 -ErrorAction Stop
    } catch {
        Write-Host "WARN: could not write update log ($LOG): $($_.Exception.Message)" -ForegroundColor Yellow
    }
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
# Also capture the WinSW service-account password from the current XMLs: the
# package ships the XML templates with a placeholder password, and the running
# services keep their account/password in the Service Control Manager, so the
# password must be re-injected unchanged or the services will fail to start.
$apiKeyMatch = Select-String -Path "$APP_DIR\app\api\.env" -Pattern '^STUDYSYNC_API_KEY=(.+)$'
if (-not $apiKeyMatch) {
    Write-Host "ERROR: STUDYSYNC_API_KEY not found in .env. Aborting; no changes made." -ForegroundColor Red
    exit 1
}
$apiKey = $apiKeyMatch.Matches[0].Groups[1].Value
$dbMatch = Select-String -Path "$APP_DIR\app\api\.env" -Pattern '^STUDYSYNC_DB_PATH=(.+)$'
if (-not $dbMatch) {
    Write-Host "ERROR: STUDYSYNC_DB_PATH not found in .env. Aborting; no changes made." -ForegroundColor Red
    exit 1
}
$dbEnv = $dbMatch.Matches[0].Groups[1].Value
$extraLines = Get-Content -Path "$APP_DIR\app\api\.env" | Where-Object {
    $_ -match '^\s*(ZK_|GOOGLE_|STUDYSYNC_(ALLOWED_ORIGINS|HOST|PORT))' -and $_ -notmatch '^\s*#'
}
Write-Log "Preserving API key (kept from current install)."

$svcAccount = "StudySyncSvc"
$svcPass = $null
foreach ($xml in @("$APP_DIR\config\winsw\studysync-api.xml", "$APP_DIR\config\winsw\studysync-caddy.xml")) {
    if (Test-Path $xml) {
        $pwMatch = Select-String -Path $xml -Pattern '<password>(.+)</password>' -ErrorAction SilentlyContinue | Select-Object -First 1
        $pw = if ($pwMatch) { $pwMatch.Matches.Groups[1].Value } else { $null }
        if ($pw -and $pw -ne "__STUDYSYNC_SVC_PASSWORD__") { $svcPass = $pw; break }
    }
}
if (-not $svcPass) {
    # Upgrade from a pre-hardening install: the old WinSW XMLs have no
    # <serviceaccount> block and no password. Migrate in place instead of
    # aborting: create the account (or refresh its password), and let the
    # sc.exe config step below move both services off LocalSystem.
    Write-Host "No StudySyncSvc credential found - upgrading from a pre-hardening install. Creating the service account now (services will be migrated off LocalSystem)." -ForegroundColor Yellow
    $chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'
    $svcPass = -join (1..32 | ForEach-Object { $chars[(Get-Random -Maximum $chars.Length)] })
    $secure = ConvertTo-SecureString $svcPass -AsPlainText -Force
    if (Get-LocalUser -Name $svcAccount -ErrorAction SilentlyContinue) {
        Set-LocalUser -Name $svcAccount -Password $secure -PasswordNeverExpires $true -UserMayChangePassword $false
    } else {
        New-LocalUser -Name $svcAccount -Password $secure `
            -FullName "StudySync Service Account" `
            -Description "Runs the StudySync API and Caddy services" `
            -AccountNeverExpires -PasswordNeverExpires -UserMayNotChangePassword | Out-Null
    }
}
Write-Log "Preserving service account '$svcAccount' credentials (kept from current install)."

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

# 3a. Ensure the WinSW wrapper log dir exists. The wrapper writes its own
#     console logs (.out.log/.err.log) to <logpath>, which the XMLs set to
#     logs\winsw (NOT config\winsw - hardening makes that dir read-only for
#     the service account, which previously crashed the wrapper the moment it
#     tried to open its log). It must exist before the ACL pass below so it is
#     covered by the writable (Modify) set rather than silently inherited.
New-Item -ItemType Directory -Force -Path "$APP_DIR\logs\winsw" | Out-Null

# 3b. Re-inject the service-account password into the freshly copied WinSW
#     XMLs (the package carries the placeholder). The password is not logged.
Set-Content -Path "$APP_DIR\config\winsw\studysync-api.xml" -Value ((Get-Content -Path "$APP_DIR\config\winsw\studysync-api.xml" -Raw) -replace '<password>__STUDYSYNC_SVC_PASSWORD__</password>', "<password>$svcPass</password>") -Encoding ASCII -NoNewline
Set-Content -Path "$APP_DIR\config\winsw\studysync-caddy.xml" -Value ((Get-Content -Path "$APP_DIR\config\winsw\studysync-caddy.xml" -Raw) -replace '<password>__STUDYSYNC_SVC_PASSWORD__</password>', "<password>$svcPass</password>") -Encoding ASCII -NoNewline
Write-Log "WinSW XMLs re-configured to run services as '$svcAccount'."

# 3c. Re-apply the ACL hardening. update.ps1 swaps the whole tree on every run,
#     so the freshly copied files inherit the (possibly still open) parent ACL.
#     Set ACLs directly on EVERY object: grant exactly SYSTEM, Administrators,
#     the installing user (for the scheduled tasks), and the service account
#     (read-only on the app, write on data/logs/backups), with inheritance
#     disabled so the inherited BUILTIN\Users read access (and any stale
#     inherited ACE from a legacy pre-hardening tree) can never come back.
#
#     This is done with the .NET ACL API rather than icacls: `icacls /grant:r
#     ... /T` with (OI)(CI) flags writes an inherit-only ACE on FILES (it grants
#     the file itself nothing), and `icacls /inheritance:r /T` then removes
#     every inherited ACE - together they leave files with an empty DACL that
#     locks the whole tree. Setting the DACL on each object directly (as the
#     owner, after takeown) is deterministic and immune to that trap.
$system      = "NT AUTHORITY\SYSTEM"
$admins      = "BUILTIN\Administrators"
$interactive = "$env:USERDOMAIN\$env:USERNAME"
$svc         = "$env:COMPUTERNAME\$svcAccount"
$writableRel = @("data", "logs", "backups")
# Native takeown writes to stderr even on success; with
# $ErrorActionPreference=Stop PowerShell 5.1 turns any stderr line into a
# terminating error, so run it with Continue and check $LASTEXITCODE.
$eap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& takeown /f $APP_DIR /r /d y /a 2>$null | Out-Null
$takeExit = $LASTEXITCODE
$ErrorActionPreference = $eap
if ($takeExit -ne 0) { throw "ACL hardening failed: takeown exit $takeExit" }

$all = @(Get-Item $APP_DIR) + @(Get-ChildItem -Path $APP_DIR -Recurse -Force -ErrorAction SilentlyContinue)
$fixed = 0; $failed = 0
foreach ($item in $all) {
    $path = $item.FullName
    try {
        $isDir = $item.PSIsContainer
        $inWritable = $false
        foreach ($rel in $writableRel) {
            $base = Join-Path $APP_DIR $rel
            if ($path -eq $base -or $path.StartsWith($base + "\")) { $inWritable = $true; break }
        }
        $svcRights = if ($inWritable) { [System.Security.AccessControl.FileSystemRights]::Modify }
                     else { [System.Security.AccessControl.FileSystemRights]::ReadAndExecute }
        if ($isDir) {
            $sec = New-Object System.Security.AccessControl.DirectorySecurity
            $inherit = [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
        } else {
            $sec = New-Object System.Security.AccessControl.FileSecurity
            $inherit = [System.Security.AccessControl.InheritanceFlags]::None
        }
        $prop = [System.Security.AccessControl.PropagationFlags]::None
        $sec.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($system, [System.Security.AccessControl.FileSystemRights]::FullControl, $inherit, $prop, [System.Security.AccessControl.AccessControlType]::Allow)))
        $sec.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($admins, [System.Security.AccessControl.FileSystemRights]::FullControl, $inherit, $prop, [System.Security.AccessControl.AccessControlType]::Allow)))
        $sec.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($interactive, [System.Security.AccessControl.FileSystemRights]::FullControl, $inherit, $prop, [System.Security.AccessControl.AccessControlType]::Allow)))
        $sec.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($svc, $svcRights, $inherit, $prop, [System.Security.AccessControl.AccessControlType]::Allow)))
        $sec.SetAccessRuleProtection($true, $false)
        Set-Acl -Path $path -AclObject $sec -ErrorAction Stop
        $fixed++
    } catch {
        $failed++
        Write-Log "WARN: ACL set failed on $path : $($_.Exception.Message)"
    }
}
if ($failed -gt 0) { throw "ACL hardening failed on $failed of $($all.Count) objects under $APP_DIR" }
Write-Log "ACLs hardened on $APP_DIR ($fixed objects; inherited BUILTIN\Users read access removed)."

# 3d. Recreate the firewall rules so a pre-hardening install (rules on ALL
#     profiles, any remote address) is tightened on update. Port 80 is
#     restricted to Private/Domain profiles and RFC1918 LAN + loopback, so a
#     Public/cafe network gets no inbound access and a machine behind a public
#     IP cannot expose port 80 to the internet.
Remove-NetFirewallRule -DisplayName "StudySync HTTP (port 80)" -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName "StudySync HTTP (port 80)" -Direction Inbound -Protocol TCP `
    -LocalPort 80 -Action Allow -Profile Private, Domain `
    -RemoteAddress 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 127.0.0.0/8 | Out-Null
Remove-NetFirewallRule -DisplayName "StudySync mDNS (UDP 5353)" -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName "StudySync mDNS (UDP 5353)" -Direction Inbound -Protocol UDP `
    -LocalPort 5353 -Action Allow -Profile Private, Domain | Out-Null
Write-Log "Firewall rules restricted to Private/Domain (port 80: RFC1918 LAN + loopback only)."

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
# Assert the service account on both registrations. A pre-hardening install
# may still be running as LocalSystem, and only the Service Control Manager
# (not the XML) decides which account the process runs under; sc.exe config
# migrates it in place - no uninstall/install, so no "marked for deletion"
# race. It also re-syncs the SCM credential with the (unchanged) password.
foreach ($svcName in @("StudySyncAPI", "StudySyncCaddy")) {
    & sc.exe config $svcName obj= ".\$svcAccount" password= $svcPass | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "sc.exe config $svcName failed (exit $LASTEXITCODE)" }
}
Write-Log "Both services confirmed running as '$svcAccount'."

sc.exe start StudySyncAPI | Out-Null
Start-Sleep -Seconds 4
sc.exe start StudySyncCaddy | Out-Null

# 5b. Refresh the watchdog's execution limit. Newer packages allow up to 15
#     minutes so an auto-restore that has to pull a backup down from Google
#     Drive can finish; the old 2-minute cap could kill healthcheck mid-run.
#     The task itself survives (same name, same exe path) - only the settings
#     object is replaced.
$checkTask = Get-ScheduledTask -TaskName "StudySyncServiceCheck" -ErrorAction SilentlyContinue
if ($checkTask) {
    Set-ScheduledTask -TaskName "StudySyncServiceCheck" -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 15)) | Out-Null
    Write-Log "StudySyncServiceCheck execution limit updated to 15 min."
} else {
    Write-Log "WARN: StudySyncServiceCheck task not found (skipping execution-limit refresh)."
}

# Restart the tray monitor if installed (the scheduled task re-registers it at
# next logon; starting it now picks up the new exe immediately).
if (Test-Path "$APP_DIR\scripts\studysync-tray.exe") {
    Start-Process "$APP_DIR\scripts\studysync-tray.exe" -WindowStyle Hidden
}

Write-Log "Update complete. Services restarted. Verify at http://localhost"
Write-Host "Update complete." -ForegroundColor Green
