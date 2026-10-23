@echo off
setlocal
rem Run setup_link2vid.bat once before first launch.
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

set "PYTHON=python"
set "PYTHONW="
if exist "%SCRIPT_DIR%.venv\Scripts\python.exe" (
  set "PYTHON=%SCRIPT_DIR%.venv\Scripts\python.exe"
)

call "%SCRIPT_DIR%packaging\check_python.bat" "%PYTHON%"
set "PYTHON_CHECK_EXIT=%ERRORLEVEL%"
if not "%PYTHON_CHECK_EXIT%"=="0" (
  echo [ERROR] Link2Vid was not launched. Run setup_link2vid.bat after installing Python 3.10+.
  pause
  exit /b %PYTHON_CHECK_EXIT%
)

rem Use pythonw only when it belongs to the interpreter validated above.
for /f "usebackq delims=" %%F in (`"%PYTHON%" -c "import pathlib, sys; print(pathlib.Path(sys.executable).with_name('pythonw.exe'))"`) do set "PYTHONW=%%F"
if defined PYTHONW if not exist "%PYTHONW%" set "PYTHONW="

echo [Link2Vid] Checking application...
"%PYTHON%" -c "from link2vid.ui.webview_app import run" 2>nul
if errorlevel 1 (
  echo [ERROR] Link2Vid failed to load. Details:
  "%PYTHON%" -c "from link2vid.ui.webview_app import run"
  echo.
  echo Try running setup_link2vid.bat, then launch again.
  pause
  exit /b 1
)

if defined PYTHONW (
  start "" /D "%SCRIPT_DIR%" "%PYTHONW%" "%SCRIPT_DIR%video_downloader.py"
) else (
  start "" /D "%SCRIPT_DIR%" "%PYTHON%" "%SCRIPT_DIR%video_downloader.py"
)

endlocal
