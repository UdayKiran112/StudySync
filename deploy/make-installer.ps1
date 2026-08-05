<#
.SYNOPSIS
    Ship the current database into StudySync-Setup.exe - fast.
.DESCRIPTION
    Use this after you change the database. It:
      1. picks the newest database (backend\library.db or the live install at
         C:\ProgramData\StudySync\data\library.db, or -Db if given)
      2. verifies integrity and checkpoints any pending WAL data
      3. copies it into deploy\package\data as the seed DB (and refreshes
         backend\library.db so the repo seed stays current)
      4. recompiles the installer ONLY - no frontend/backend rebuild

    Output: deploy\installer\output\StudySync-Setup.exe
    A fresh install then starts with your updated data.

.PARAMETER DbPath
    Optional: explicit path to the database to ship. Default: the newest of
    the two standard locations above.

.NOTES
    For CODE changes use build-package.ps1 + build-installer.ps1 instead.
#>
[CmdletBinding()]
param(
    [string]$DbPath = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$deploy = $PSScriptRoot
$package = Join-Path $deploy "package"
$backend = Join-Path $root "backend"
$liveDb = "C:\ProgramData\StudySync\data\library.db"

function Write-Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }

# ------------------------------------------------------------ find python
function Get-Python {
    $venvPy = Join-Path $deploy "build\build-venv\Scripts\python.exe"
    if (Test-Path $venvPy) { return $venvPy }
    return "python"
}
$py = Get-Python

# --------------------------------------------------------------- find db
$source = ""
if ($DbPath) {
    if (-not (Test-Path $DbPath)) { throw "Database not found: $DbPath" }
    $source = $DbPath
} else {
    $candidates = @($backend, $liveDb) | ForEach-Object { Join-Path $_ "library.db" } | Where-Object { Test-Path $_ }
    if (-not $candidates) { throw "No database found. Update backend\library.db (or the live install DB) first." }
    $source = $candidates | Sort-Object LastWriteTime -Descending | Select-Object -First 1
}
$stamp = (Get-Item $source).LastWriteTime
Write-Step "Using database: $source (updated $stamp)"

# ------------------------------------------------------- checkpoint + verify
Write-Step "Checking database integrity"
$wal = "$source-wal"
if ((Test-Path $wal) -and (Get-Item $wal).Length -gt 0) {
    Write-Host "WAL file present - checkpointing so no data is left behind..." -ForegroundColor Yellow
    & $py -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); print('checkpoint busy=%d' % c.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0])" $source
    if ($LASTEXITCODE -ne 0) { throw "WAL checkpoint failed" }
}
$integrity = & $py -c "import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute('PRAGMA integrity_check').fetchone()[0])" $source
if ($LASTEXITCODE -ne 0) { throw "Could not open database with SQLite" }
if ($integrity -ne "ok") { throw "Integrity check FAILED: $integrity - fix the database before shipping it." }
Write-Host "Integrity: $integrity"

# ---------------------------------------------------------------- refresh
Write-Step "Refreshing seed database"
$backendSeed = Join-Path $backend "library.db"
if ((Resolve-Path $source).Path -ne (Resolve-Path $backendSeed).Path) {
    Copy-Item $source $backendSeed -Force
    Write-Host "Copied to repo seed: $backendSeed"
} else {
    Write-Host "Repo seed already current: $backendSeed"
}

$packageSeed = Join-Path $package "data\library.db"
New-Item -ItemType Directory -Force -Path (Split-Path $packageSeed) | Out-Null
Copy-Item $source $packageSeed -Force
Write-Host "Copied to package seed: $packageSeed"

# ---------------------------------------------------------------- installer
Write-Step "Rebuilding StudySync-Setup.exe (installer only)"
& powershell -ExecutionPolicy Bypass -File (Join-Path $deploy "build-installer.ps1")
if ($LASTEXITCODE -ne 0) { throw "Installer build failed (exit $LASTEXITCODE)" }

$out = Join-Path $deploy "installer\output\StudySync-Setup.exe"
Write-Host "`nDone. Fresh installs will seed with the database you shipped." -ForegroundColor Green
Write-Host "Hand out: $out" -ForegroundColor Green
