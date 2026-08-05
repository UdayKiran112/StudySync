<#
.SYNOPSIS
    Builds the one-click StudySync-Setup.exe installer.
.DESCRIPTION
    Ensures Inno Setup is available (downloads + installs a portable copy to
    deploy\tools if needed), then compiles deploy\installer\studysync.iss.
    Requires deploy\package to exist (run build-package.ps1 first).

    Output: deploy\installer\output\StudySync-Setup.exe
.NOTES
    Run on a build machine (the same one used for build-package.ps1).
#>
$ErrorActionPreference = "Stop"
$deploy = $PSScriptRoot
$iss = Join-Path $deploy "installer\studysync.iss"
$package = Join-Path $deploy "package"

if (-not (Test-Path $package)) {
    Write-Host "ERROR: package not found. Run build-package.ps1 first." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $iss)) {
    Write-Host "ERROR: $iss not found." -ForegroundColor Red
    exit 1
}

# --- locate Inno Setup (ISCC.exe) ---
$candidates = @(
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe",
    (Join-Path $deploy "tools\Inno Setup 6\ISCC.exe")
)
$iscc = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $iscc) {
    Write-Host "Inno Setup not found. Downloading portable copy..." -ForegroundColor Yellow
    # Fetch the latest stable installer from jrsoftware.org and install it to
    # a tools folder under deploy (no admin needed).
    $tools = Join-Path $deploy "tools"
    New-Item -ItemType Directory -Force -Path $tools | Out-Null
    $installer = Join-Path $tools "innosetup-installer.exe"
    # Direct immutable GitHub release link (latest Inno Setup 6). Update the
    # tag if a newer version is released: https://github.com/jrsoftware/issrc/releases
    $dl = "https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe"
    Invoke-WebRequest -Uri $dl -OutFile $installer -UseBasicParsing
    $innoDir = Join-Path $tools "Inno Setup 6"
    $p = Start-Process -FilePath $installer -ArgumentList "/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART","/DIR=$innoDir" -Wait -PassThru
    if ($p.ExitCode -ne 0) {
        Write-Host "ERROR: Inno Setup install failed (exit $($p.ExitCode)). Install it manually from https://jrsoftware.org/isinfo.php" -ForegroundColor Red
        exit 1
    }
    $iscc = Join-Path $innoDir "ISCC.exe"
}

Write-Host "Using ISCC: $iscc"
Push-Location (Split-Path $iss -Parent)
try {
    & $iscc $iss
    if ($LASTEXITCODE -ne 0) { throw "ISCC failed (exit $LASTEXITCODE)" }
} finally {
    Pop-Location
}

$out = Join-Path $deploy "installer\output\StudySync-Setup.exe"
if (Test-Path $out) {
    Write-Host "`nInstaller built: $out" -ForegroundColor Green
    Write-Host "Hand StudySync-Setup.exe to staff - double-click installs everything." -ForegroundColor Green
} else {
    Write-Host "WARN: installer output not found at $out" -ForegroundColor Yellow
}
