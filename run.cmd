@echo off
title RADU V3 - Voice Command Diagnostics
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUNBUFFERED=1
set PYTHONUTF8=1
echo.
echo RADU V3 starting. This window shows microphone and recognition diagnostics.
echo.
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -u -m assistant.main
) else (
  py -3 -u -m assistant.main
)
if errorlevel 1 (
  echo.
  echo RADU V3 stopped with an error. The diagnostic output is above.
  pause
)
