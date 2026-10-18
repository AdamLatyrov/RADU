@echo off
cd /d "%~dp0"
py -3 -m venv .venv
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_model.ps1"
if errorlevel 1 goto failed
echo.
echo Dependencies and the Russian Vosk model are installed.
pause
exit /b 0
:failed
echo Setup failed. Check that Python 3 is installed and available as py.
pause
exit /b 1
