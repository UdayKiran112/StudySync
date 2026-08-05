<#
.SYNOPSIS
    Builds the complete StudySync deployment package.
.DESCRIPTION
    Run on a build machine that has Node.js, npm, Python and the project
    dependencies available. Produces a self-contained folder:
        deploy\package\
    which is copied onto target machines by install.ps1 (or wrapped into an
    Inno Setup installer). The package contains the compiled backend exe, the
    production frontend build, Caddy, WinSW, and all scripts - target machines
    need NO build tools and NO Python.

.PARAMETER IncludeDatabase
    Include the existing backend\library.db as the initial data (use when
    deploying a machine that should start with the current data).

.NOTES
    Prerequisites on the build machine:
      - node + npm (frontend build)
      - a Python 3.13 install (backend PyInstaller build)
      - internet access (binary downloads + pip)
#>
[CmdletBinding()]
param(
    [switch]$IncludeDatabase
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent   # repo root
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$deploy = $PSScriptRoot
$build = Join-Path $deploy "build"
$dist = Join-Path $build "dist"
$package = Join-Path $deploy "package"
$bin = Join-Path $deploy "bin"

function Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }

# ------------------------------------------------------------ 1. frontend
Step "Building production frontend"
Push-Location $frontend
try {
    npm install --no-audit --no-fund | Out-Null
    npm run build | Out-Null
} finally {
    Pop-Location
}
if (-not (Test-Path (Join-Path $frontend "dist\index.html"))) { throw "Frontend build produced no dist\index.html" }

# ------------------------------------------------------------- 2. backend
Step "Building backend (PyInstaller onedir)"
# Reuse the venv produced by the first-ever build; install pyinstaller if missing.
$buildVenv = Join-Path $build "build-venv"
if (-not (Test-Path (Join-Path $buildVenv "Scripts\pyinstaller.exe"))) {
    throw "No build venv found at $buildVenv. Create it once with:
  python -m venv $buildVenv
  $buildVenv\Scripts\pip install -r backend\requirements.txt pyinstaller"
}
Push-Location $backend
try {
    & "$buildVenv\Scripts\pyinstaller.exe" --noconfirm --clean `
        --distpath $dist --workpath (Join-Path $build "work") --specpath (Join-Path $build "spec") `
        --name studysync-api --onedir --paths . run_server.py | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
    foreach ($name in @("backup", "restore", "healthcheck")) {
        & "$buildVenv\Scripts\pyinstaller.exe" --noconfirm --clean `
            --distpath $dist --workpath (Join-Path $build "work") --specpath (Join-Path $build "spec") `
            --name $name --onefile (Join-Path $deploy "scripts\$name.py") | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for $name" }
    }
} finally {
    Pop-Location
}

# ------------------------------------------------------------ 3. binaries
Step "Ensuring WinSW and Caddy binaries"
if (-not (Test-Path (Join-Path $bin "WinSW-x64.exe"))) {
    throw "WinSW-x64.exe missing from deploy\bin. Download:
  https://github.com/winsw/winsw/releases  (WinSW-x64.exe -> deploy\bin\WinSW-x64.exe)"
}
if (-not (Test-Path (Join-Path $bin "caddy.exe"))) {
    throw "caddy.exe missing from deploy\bin. Download:
  https://caddyserver.com/download  (Windows amd64 -> deploy\bin\caddy.exe)"
}

# -------------------------------------------------------------- 4. package
Step "Assembling package at $package"
if (Test-Path $package) { Remove-Item $package -Recurse -Force }
New-Item -ItemType Directory -Force -Path (Join-Path $package "app\api") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $package "app\frontend") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $package "app\caddy") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $package "config\winsw") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $package "scripts") | Out-Null

# API: compiled exe + _internal. Deliberately NO .env in the package:
# install.ps1 generates it (preserving any existing key), so a reinstall
# never clobbers a live key with a placeholder.
Copy-Item (Join-Path $dist "studysync-api\*") (Join-Path $package "app\api") -Recurse -Force

# Frontend build
Copy-Item (Join-Path $frontend "dist\*") (Join-Path $package "app\frontend") -Recurse -Force

# Caddy
Copy-Item (Join-Path $bin "caddy.exe") (Join-Path $package "app\caddy\caddy.exe") -Force
Copy-Item (Join-Path $deploy "caddy\Caddyfile") (Join-Path $package "app\caddy\Caddyfile") -Force

# WinSW (renamed to match the XML filenames)
Copy-Item (Join-Path $bin "WinSW-x64.exe") (Join-Path $package "config\winsw\studysync-api.exe") -Force
Copy-Item (Join-Path $bin "WinSW-x64.exe") (Join-Path $package "config\winsw\studysync-caddy.exe") -Force
Copy-Item (Join-Path $deploy "winsw\studysync-api.xml") (Join-Path $package "config\winsw") -Force
Copy-Item (Join-Path $deploy "winsw\studysync-caddy.xml") (Join-Path $package "config\winsw") -Force

# Scripts (compiled exes + source scripts for reference)
foreach ($name in @("backup", "restore", "healthcheck")) {
    Copy-Item (Join-Path $dist "$name.exe") (Join-Path $package "scripts\$name.exe") -Force
}
foreach ($ps1 in @("install.ps1", "update.ps1", "uninstall.ps1", "diagnostics.ps1")) {
    Copy-Item (Join-Path $deploy "scripts\$ps1") (Join-Path $package "scripts") -Force
}

# Seed database (optional)
if ($IncludeDatabase) {
    if (Test-Path (Join-Path $backend "library.db")) {
        New-Item -ItemType Directory -Force -Path (Join-Path $package "data") | Out-Null
        Copy-Item (Join-Path $backend "library.db") (Join-Path $package "data\library.db") -Force
        Write-Host "Included existing database as seed data." -ForegroundColor Yellow
    } else {
        Write-Host "WARN: -IncludeDatabase requested but backend\library.db not found." -ForegroundColor Yellow
    }
}

# ------------------------------------------------------------ 5. summary
Step "Done"
$size = (Get-ChildItem $package -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1MB
Write-Host "Package ready: $package ($([math]::Round($size,1)) MB)"
Write-Host "Deploy on a target machine by running, elevated:"
Write-Host "  powershell -ExecutionPolicy Bypass -File package\scripts\install.ps1"
