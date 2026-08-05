<#
.SYNOPSIS
    Collect a support bundle for troubleshooting.
.DESCRIPTION
    Produces a single zip in the temp folder containing service state, the
    last N log lines, firewall rule status, scheduled tasks, network info,
    and a health check run. Hand this zip to whoever is diagnosing an issue.
.NOTES
    No admin required for the diagnostics themselves; run elevated if the
    services should also be restarted by the bundled healthcheck.
#>
$ErrorActionPreference = "Continue"
$APP_DIR = "C:\ProgramData\StudySync"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$work = Join-Path $env:TEMP "studysync-diagnostics_$stamp"
New-Item -ItemType Directory -Force -Path $work | Out-Null

function Out-Report($name, $content) {
    if ($content) { $content | Set-Content -Path (Join-Path $work $name) -Encoding UTF8 }
}

Out-Report "00_health.txt" ((& "$APP_DIR\scripts\healthcheck.exe" 2>&1) -join "`n")

Out-Report "01_services.txt" ((& sc.exe query StudySyncAPI 2>&1) -join "`n") 
Add-Content -Path (Join-Path $work "01_services.txt") -Value (("`n" + ((& sc.exe query StudySyncCaddy 2>&1) -join "`n")))
Add-Content -Path (Join-Path $work "01_services.txt") -Value (("`n" + ((& sc.exe queryex StudySyncAPI 2>&1) -join "`n")))

Out-Report "02_processes.txt" ((Get-Process -Name "python","caddy","studysync-*" -ErrorAction SilentlyContinue | Select-Object Id, ProcessName, StartTime, Path | Format-Table -AutoSize | Out-String))

Out-Report "03_firewall.txt" ((Get-NetFirewallRule -DisplayName "StudySync HTTP*" -ErrorAction SilentlyContinue | Select-Object DisplayName, Enabled, Direction, Action | Format-Table -AutoSize | Out-String))

Out-Report "04_scheduled_tasks.txt" ((Get-ScheduledTask -TaskName "StudySync-*" -ErrorAction SilentlyContinue | Select-Object TaskName, State | Format-Table -AutoSize | Out-String))

Out-Report "05_network.txt" ((Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254*" } | Select-Object IPAddress, InterfaceAlias | Format-Table -AutoSize | Out-String))

$logDir = "$APP_DIR\logs"
if (Test-Path $logDir) {
    $target = Join-Path $work "logs"
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    Get-ChildItem -Path $logDir -Recurse -File | ForEach-Object {
        $tail = Get-Content -Path $_.FullName -Tail 500
        $tail | Set-Content -Path (Join-Path $target ($_.FullName.Replace($logDir + "\", "").Replace("\", "__")))
    }
}

Out-Report "06_versions.txt" (@(
    "OS: $([System.Environment]::OSVersion.VersionString)",
    "API key configured: $([bool](Select-String -Path "$APP_DIR\app\api\.env" -Pattern '^STUDYSYNC_API_KEY=.+' -ErrorAction SilentlyContinue))",
    "DB exists: $(Test-Path "$APP_DIR\data\library.db")",
    "DB size: $((Get-Item "$APP_DIR\data\library.db" -ErrorAction SilentlyContinue).Length) bytes"
) -join "`n")

$zip = Join-Path $env:TEMP "studysync-diagnostics_$stamp.zip"
Compress-Archive -Path "$work\*" -DestinationPath $zip -Force
Write-Host "Diagnostics bundle: $zip" -ForegroundColor Green
