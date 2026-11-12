@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0localai.ps1" doctor
exit /b %errorlevel%
