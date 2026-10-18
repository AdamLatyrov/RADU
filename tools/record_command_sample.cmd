@echo off
cd /d "%~dp0.."
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "tools\record_voice_sample.py"
) else (
  py -3 "tools\record_voice_sample.py"
)
pause
