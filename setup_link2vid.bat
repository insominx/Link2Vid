@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%"

echo [Link2Vid] Setup starting...
set "PYTHON=python"
if exist "%SCRIPT_DIR%.venv\Scripts\python.exe" (
  set "PYTHON=%SCRIPT_DIR%.venv\Scripts\python.exe"
) else (
  where python >nul 2>&1
  if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.8+ and add it to PATH.
    popd
    exit /b 1
  )
  echo [INFO] No .venv detected. Using system Python.
  echo [INFO] To create one: python -m venv .venv
)

echo [Link2Vid] Installing Python dependencies (upgrade to latest)...
%PYTHON% -m pip install -U -r requirements.txt
if errorlevel 1 (
  echo [ERROR] Failed to install Python dependencies.
  popd
  exit /b 1
)

echo [Link2Vid] Checking optional dependencies...
where ffmpeg >nul 2>&1
if errorlevel 1 (
  echo [WARN] ffmpeg not found. Some formats may fail or be video-only.
) else (
  for /f "delims=" %%V in ('ffmpeg -version 2^>^&1 ^| findstr /i "version"') do (
    echo [OK] ffmpeg: %%V
    goto :ffmpeg_done
  )
)
:ffmpeg_done

set "RUNTIME_FOUND="
for %%R in (deno node bun) do (
  where %%R >nul 2>&1
  if not errorlevel 1 (
    echo [OK] JS runtime detected: %%R
    set "RUNTIME_FOUND=1"
  )
)
if not defined RUNTIME_FOUND (
  echo [WARN] No JS runtime found. Install deno/node/bun for YouTube support.
)

echo [Link2Vid] Setup complete.
popd
pause
