@echo off
rem Double-click this after updating the database. It rebuilds
rem StudySync-Setup.exe with your latest data (no code rebuild).
powershell -ExecutionPolicy Bypass -File "%~dp0make-installer.ps1" %*
echo.
pause
