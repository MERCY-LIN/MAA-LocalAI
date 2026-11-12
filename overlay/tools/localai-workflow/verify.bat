@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0localai.ps1" verify
exit /b %errorlevel%
