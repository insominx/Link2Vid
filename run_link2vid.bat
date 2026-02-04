@echo off
rem Run setup_link2vid.bat once before first launch.
set "SCRIPT_DIR=%~dp0"
set "PYTHON=python"
if exist "%SCRIPT_DIR%.venv\Scripts\python.exe" (
  set "PYTHON=%SCRIPT_DIR%.venv\Scripts\python.exe"
)
powershell -NoProfile -WindowStyle Hidden -Command "Start-Process -FilePath '%PYTHON%' -WorkingDirectory '%SCRIPT_DIR%' -ArgumentList '\"%SCRIPT_DIR%video_downloader.py\"' -WindowStyle Hidden"
