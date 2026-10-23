@echo off
setlocal
rem Launch the packaged Doggybag build from release\Doggybag.
set "SCRIPT_DIR=%~dp0"
set "APP_DIR=%SCRIPT_DIR%release\Doggybag"
set "LAUNCHER=%APP_DIR%\Doggybag.bat"

if not exist "%LAUNCHER%" (
  echo [ERROR] Packaged app not found: %LAUNCHER%
  echo Run build_windows.bat first.
  pause
  exit /b 1
)

start "" /D "%APP_DIR%" "%LAUNCHER%"
endlocal
