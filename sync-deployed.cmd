@echo off
setlocal
cd /d "%~dp0"
set "PY=study_sync\Scripts\python.exe"
if exist "%PY%" (
    "%PY%" deploy\scripts\sync_db.py %*
) else (
    py -3 deploy\scripts\sync_db.py %*
)
endlocal
