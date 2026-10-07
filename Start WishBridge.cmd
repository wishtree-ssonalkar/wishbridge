@echo off
rem Double-click to open the Wishtree WishBridge app in your browser.
rem Create a desktop shortcut to this file for one-click access.
cd /d "%~dp0"
if not exist ".venv\Scripts\wishbridge.exe" (
  echo WishBridge is not installed in this folder yet.
  echo Run:  python -m venv .venv
  echo       .venv\Scripts\python.exe -m pip install -e ".[ai,ui]"
  pause
  exit /b 1
)
".venv\Scripts\wishbridge.exe" ui
pause
