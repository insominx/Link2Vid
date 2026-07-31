@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "RELEASE_DIR=release\Link2Vid"
set "RELEASE_EXE=%RELEASE_DIR%\Link2Vid.exe"
pushd "%SCRIPT_DIR%"

set "PYTHON=python"
if exist "%SCRIPT_DIR%.venv\Scripts\python.exe" (
  set "PYTHON=%SCRIPT_DIR%.venv\Scripts\python.exe"
)

call "%SCRIPT_DIR%packaging\check_python.bat" "%PYTHON%"
set "PYTHON_CHECK_EXIT=%ERRORLEVEL%"
if not "%PYTHON_CHECK_EXIT%"=="0" (
  echo [ERROR] Build stopped before dependency installation and PyInstaller.
  popd
  exit /b %PYTHON_CHECK_EXIT%
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
set "FFMPEG_SOURCE="
set "FFPROBE_SOURCE="
if defined ChocolateyInstall if exist "%ChocolateyInstall%\lib\ffmpeg\tools\ffmpeg\bin\ffmpeg.exe" (
  set "FFMPEG_SOURCE=%ChocolateyInstall%\lib\ffmpeg\tools\ffmpeg\bin\ffmpeg.exe"
  set "FFPROBE_SOURCE=%ChocolateyInstall%\lib\ffmpeg\tools\ffmpeg\bin\ffprobe.exe"
)
if not defined FFMPEG_SOURCE (
  for /f "delims=" %%F in ('where ffmpeg 2^>nul') do if not defined FFMPEG_SOURCE set "FFMPEG_SOURCE=%%F"
  for /f "delims=" %%F in ('where ffprobe 2^>nul') do if not defined FFPROBE_SOURCE set "FFPROBE_SOURCE=%%F"
)
if not defined FFMPEG_SOURCE (
  echo [WARN] ffmpeg not found on builder PATH; bin\ffmpeg.exe not staged.
  goto :sidecars_done
)

copy /Y "%FFMPEG_SOURCE%" "%RELEASE_DIR%\bin\ffmpeg.exe" >nul
if defined FFPROBE_SOURCE copy /Y "%FFPROBE_SOURCE%" "%RELEASE_DIR%\bin\ffprobe.exe" >nul
"%RELEASE_DIR%\bin\ffmpeg.exe" -version >nul 2>&1
if errorlevel 1 (
  echo [WARN] Staged ffmpeg is not runnable; removing invalid sidecars.
  del /f /q "%RELEASE_DIR%\bin\ffmpeg.exe" "%RELEASE_DIR%\bin\ffprobe.exe" 2>nul
  goto :sidecars_done
)
"%RELEASE_DIR%\bin\ffprobe.exe" -version >nul 2>&1
if errorlevel 1 (
  echo [WARN] Staged ffprobe is not runnable; removing invalid sidecars.
  del /f /q "%RELEASE_DIR%\bin\ffmpeg.exe" "%RELEASE_DIR%\bin\ffprobe.exe" 2>nul
  goto :sidecars_done
)
echo [OK] Runnable ffmpeg/ffprobe staged to %RELEASE_DIR%\bin\
:sidecars_done

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
