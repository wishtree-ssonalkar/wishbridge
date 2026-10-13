@echo off
rem Double-click once to set up Wishtree WishBridge on this computer.
rem Runs install.ps1 for this session only, without changing PowerShell's execution policy.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
pause
