@echo off
setlocal
rem Launch the packaged Link2Vid build from release\Link2Vid.
set "SCRIPT_DIR=%~dp0"
set "APP_DIR=%SCRIPT_DIR%release\Link2Vid"
set "LAUNCHER=%APP_DIR%\Link2Vid.bat"

if not exist "%LAUNCHER%" (
  echo [ERROR] Packaged app not found: %LAUNCHER%
  echo Run build_windows.bat first.
  pause
  exit /b 1
)

start "" /D "%APP_DIR%" "%LAUNCHER%"
endlocal
