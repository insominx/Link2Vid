@echo off
setlocal
rem Headless CLI for agents and scripts: doggybag <command> ... (see docs\headless-usage.md).
rem JSON goes to stdout, logs to stderr. The current directory is the default --out, so do not cd.
set "SCRIPT_DIR=%~dp0"

set "PYTHON=python"
if exist "%SCRIPT_DIR%.venv\Scripts\python.exe" (
  set "PYTHON=%SCRIPT_DIR%.venv\Scripts\python.exe"
)

rem check_python.bat reports on stdout; keep stdout for the JSON result.
call "%SCRIPT_DIR%packaging\check_python.bat" "%PYTHON%" 1>&2
if errorlevel 1 exit /b 6

if "%~1"=="" (
  "%PYTHON%" "%SCRIPT_DIR%video_downloader.py" --help
  exit /b 2
)

"%PYTHON%" "%SCRIPT_DIR%video_downloader.py" %*
exit /b %ERRORLEVEL%
