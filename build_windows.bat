@echo off
setlocal EnableDelayedExpansion
set "SCRIPT_DIR=%~dp0"
set "RELEASE_DIR=release\Link2Vid"
set "RELEASE_EXE=%RELEASE_DIR%\Link2Vid.exe"
pushd "%SCRIPT_DIR%"

set "PYTHON=python"
if exist "%SCRIPT_DIR%.venv\Scripts\python.exe" (
  set "PYTHON=%SCRIPT_DIR%.venv\Scripts\python.exe"
)

echo [Link2Vid] Installing build dependencies...
"%PYTHON%" -m pip install -r packaging\requirements-build.txt
if errorlevel 1 (
  echo [ERROR] Failed to install build dependencies.
  popd
  exit /b 1
)

echo [Link2Vid] Building portable app into %RELEASE_DIR%...
"%PYTHON%" -m PyInstaller packaging\link2vid.spec --noconfirm --clean --workpath build\pyinstaller-work --distpath release
if errorlevel 1 (
  echo [ERROR] PyInstaller build failed.
  popd
  exit /b 1
)

if not exist "%RELEASE_EXE%" (
  echo [ERROR] Expected packaged exe missing: %RELEASE_EXE%
  popd
  exit /b 1
)

echo [Link2Vid] Removing non-runnable PyInstaller stubs from build\...
for /f "delims=" %%F in ('dir /s /b build\Link2Vid.exe 2^>nul') do del /f /q "%%F" 2>nul

copy /Y packaging\link2vid_launch.bat "%RELEASE_DIR%\Link2Vid.bat" >nul
copy /Y packaging\README.release.txt "%RELEASE_DIR%\README.txt" >nul

if exist "developer.json" (
  copy /Y developer.json "%RELEASE_DIR%\developer.json" >nul
  echo [OK] Copied developer.json into %RELEASE_DIR%
)

echo [Link2Vid] Staging sidecar binaries...
if not exist "%RELEASE_DIR%\bin" mkdir "%RELEASE_DIR%\bin"
where ffmpeg >nul 2>&1
if errorlevel 1 (
  echo [WARN] ffmpeg not found on builder PATH; bin\ffmpeg.exe not staged.
) else (
  for /f "delims=" %%F in ('where ffmpeg') do (
    copy /Y "%%F" "%RELEASE_DIR%\bin\ffmpeg.exe" >nul
    goto :ffmpeg_copied
  )
  :ffmpeg_copied
  where ffprobe >nul 2>&1
  if not errorlevel 1 (
    for /f "delims=" %%F in ('where ffprobe') do (
      copy /Y "%%F" "%RELEASE_DIR%\bin\ffprobe.exe" >nul
      goto :ffprobe_copied
    )
  )
  :ffprobe_copied
  echo [OK] ffmpeg/ffprobe staged to %RELEASE_DIR%\bin\
)

echo [Link2Vid] Running import smoke test...
"%RELEASE_EXE%" --smoke
if errorlevel 1 (
  echo [ERROR] Smoke test failed for %RELEASE_EXE%
  popd
  exit /b 1
)

echo.
echo [Link2Vid] Build complete.
echo   Start the app: %RELEASE_DIR%\Link2Vid.bat
echo   Or from repo root: run_link2vid_packaged.bat
echo.
start "" explorer "%SCRIPT_DIR%%RELEASE_DIR%"
popd
endlocal
