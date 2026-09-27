@echo off
setlocal
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "RUNTIME=%LOCALAPPDATA%\MeetingScribe\runtime\Scripts\pythonw.exe"
if exist "%~dp0runtime-path.txt" set /p RUNTIME=<"%~dp0runtime-path.txt"
if not exist "%RUNTIME%" set "RUNTIME=%LOCALAPPDATA%\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\MeetingScribe\runtime\Scripts\pythonw.exe"
if not exist "%RUNTIME%" (
  echo Please run Setup.cmd first.
  pause
  exit /b 1
)
start "" "%RUNTIME%" "%~dp0app.py"
