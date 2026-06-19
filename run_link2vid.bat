@echo off
setlocal
rem Run setup_link2vid.bat once before first launch.
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

set "PYTHON=python"
set "PYTHONW=pythonw"
if exist "%SCRIPT_DIR%.venv\Scripts\python.exe" (
  set "PYTHON=%SCRIPT_DIR%.venv\Scripts\python.exe"
  set "PYTHONW=%SCRIPT_DIR%.venv\Scripts\pythonw.exe"
)

where "%PYTHON%" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python not found. Install Python 3.8+ or run setup_link2vid.bat.
  pause
  exit /b 1
)

echo [Link2Vid] Checking application...
"%PYTHON%" -c "from link2vid.ui.main_window import VideoDownloaderApp" 2>nul
if errorlevel 1 (
  echo [ERROR] Link2Vid failed to load. Details:
  "%PYTHON%" -c "from link2vid.ui.main_window import VideoDownloaderApp"
  echo.
  echo Try running setup_link2vid.bat, then launch again.
  pause
  exit /b 1
)

if exist "%PYTHONW%" (
  start "" /D "%SCRIPT_DIR%" "%PYTHONW%" "%SCRIPT_DIR%video_downloader.py"
) else (
  start "" /D "%SCRIPT_DIR%" "%PYTHON%" "%SCRIPT_DIR%video_downloader.py"
)

endlocal
