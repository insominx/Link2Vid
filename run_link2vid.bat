@echo off
set "SCRIPT_DIR=%~dp0"
powershell -NoProfile -WindowStyle Hidden -Command "Start-Process python -WorkingDirectory '%SCRIPT_DIR%' -ArgumentList '\"%SCRIPT_DIR%video_downloader.py\"' -WindowStyle Hidden"
