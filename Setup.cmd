@echo off
setlocal
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "ROOT=%LOCALAPPDATA%\MeetingScribe"
if not exist "%ROOT%\runtime\Scripts\python.exe" (
  py -3.13 -m venv "%ROOT%\runtime"
  if errorlevel 1 goto fail
)
"%ROOT%\runtime\Scripts\python.exe" -m pip install -r requirements-lock.txt
if errorlevel 1 goto fail
"%ROOT%\runtime\Scripts\python.exe" configure_local.py
if errorlevel 1 goto fail
"%ROOT%\runtime\Scripts\python.exe" download_model.py large-v3
if errorlevel 1 goto fail
"%ROOT%\runtime\Scripts\python.exe" download_model.py turbo
if errorlevel 1 goto fail
echo Setup complete. Open Start.cmd.
pause
exit /b 0
:fail
echo Setup failed. Python 3.13 and an internet connection are required.
pause
exit /b 1
