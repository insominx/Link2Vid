@echo off
setlocal
set "SCRIPT_DIR=%~dp0\.."
cd /d "%SCRIPT_DIR%"

if not exist "release\Doggybag\Doggybag.exe" (
  echo [ERROR] release\Doggybag\Doggybag.exe not found. Run build_windows.bat first.
  exit /b 1
)

release\Doggybag\Doggybag.exe --smoke
exit /b %ERRORLEVEL%
