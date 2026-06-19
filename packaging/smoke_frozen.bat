@echo off
setlocal
set "SCRIPT_DIR=%~dp0\.."
cd /d "%SCRIPT_DIR%"

if not exist "release\Link2Vid\Link2Vid.exe" (
  echo [ERROR] release\Link2Vid\Link2Vid.exe not found. Run build_windows.bat first.
  exit /b 1
)

release\Link2Vid\Link2Vid.exe --smoke
exit /b %ERRORLEVEL%
