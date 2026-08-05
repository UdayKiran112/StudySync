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
    need NO build tools and NO Python. If backend\library.db exists it is
    bundled as the initial data, so a fresh install starts with the current
    students etc. (install.ps1 never overwrites an existing database).

.NOTES
    Prerequisites on the build machine:
      - node + npm (frontend build)
      - a Python 3.13 install (backend PyInstaller build)
      - internet access (binary downloads + pip)
#>
[CmdletBinding()]
param()

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
        --name studysync-api --onedir --paths . `
        --hidden-import ifaddr run_server.py | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
    # Scheduled-task helpers run as the interactive user; build backup,
    # healthcheck and the system-tray monitor as windowless (--noconsole) so
    # they never flash a Command Prompt window. restore.exe stays a console
    # app: it is interactive and prompts for the backup to restore.
    foreach ($name in @("backup", "healthcheck", "studysync-tray")) {
        & "$buildVenv\Scripts\pyinstaller.exe" --noconfirm --clean --noconsole `
            --distpath $dist --workpath (Join-Path $build "work") --specpath (Join-Path $build "spec") `
            --name $name --onefile (Join-Path $deploy "scripts\$name.py") | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for $name" }
    }
    & "$buildVenv\Scripts\pyinstaller.exe" --noconfirm --clean `
        --distpath $dist --workpath (Join-Path $build "work") --specpath (Join-Path $build "spec") `
        --name restore --onefile (Join-Path $deploy "scripts\restore.py") | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for restore" }
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
# Bonjour for Windows (Bonjour64.msi, Apple-signed) - needed so Windows PCs
# can resolve http://studysync.local. Stage it under deploy\tools (gitignored
# binary); install.ps1 installs it on the server and keeps a copy for staff PCs.
$bonjourMsi = Join-Path $deploy "tools\Bonjour64.msi"
if (-not (Test-Path $bonjourMsi)) {
    throw "Bonjour64.msi missing from deploy\tools. Get the Apple-signed MSI
  (2,682,368 bytes, MD5 8dcf5c9eaacdaf4568220d103f393dea) and save it as:
  $bonjourMsi"
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
foreach ($name in @("backup", "restore", "healthcheck", "studysync-tray")) {
    Copy-Item (Join-Path $dist "$name.exe") (Join-Path $package "scripts\$name.exe") -Force
}
foreach ($ps1 in @("install.ps1", "update.ps1", "uninstall.ps1", "diagnostics.ps1")) {
    Copy-Item (Join-Path $deploy "scripts\$ps1") (Join-Path $package "scripts") -Force
}

# Tools (Bonjour for Windows MSI - staff PCs need this to resolve
# http://studysync.local; install.ps1 also copies it to $APP_DIR\tools).
New-Item -ItemType Directory -Force -Path (Join-Path $package "tools") | Out-Null
Copy-Item $bonjourMsi (Join-Path $package "tools\Bonjour64.msi") -Force

# Seed database: if backend\library.db exists it is bundled as the initial
# data, so a fresh install on a venue/demo machine starts with the current
# students etc. install.ps1 only seeds when NO database exists yet, so a
# reinstall on a machine that already has data never overwrites it.
if (Test-Path (Join-Path $backend "library.db")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $package "data") | Out-Null
    Copy-Item (Join-Path $backend "library.db") (Join-Path $package "data\library.db") -Force
    Write-Host "Included existing database as seed data." -ForegroundColor Yellow
} else {
    Write-Host "No backend\library.db found - package will start with an empty database." -ForegroundColor Yellow
}

# ------------------------------------------------------------ 5. summary
Step "Done"
$size = (Get-ChildItem $package -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1MB
Write-Host "Package ready: $package ($([math]::Round($size,1)) MB)"
Write-Host "Deploy on a target machine by running, elevated:"
Write-Host "  powershell -ExecutionPolicy Bypass -File package\scripts\install.ps1"
