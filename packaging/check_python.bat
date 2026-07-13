@echo off
setlocal

set "PYTHON_CANDIDATE=%~1"
if not defined PYTHON_CANDIDATE (
  echo [ERROR] Python interpreter not specified. Install Python 3.10+ and try again.
  exit /b 1
)

rem Paths are checked directly; only bare command names are searched on PATH.
set "PATH_SHAPED="
if not "%PYTHON_CANDIDATE:\=%"=="%PYTHON_CANDIDATE%" set "PATH_SHAPED=1"
if not "%PYTHON_CANDIDATE:/=%"=="%PYTHON_CANDIDATE%" set "PATH_SHAPED=1"

if defined PATH_SHAPED (
  if not exist "%PYTHON_CANDIDATE%" (
    echo [ERROR] Python interpreter not found: %PYTHON_CANDIDATE%
    echo [ERROR] Install Python 3.10+ or select an existing interpreter.
    exit /b 1
  )
) else (
  where "%PYTHON_CANDIDATE%" >nul 2>&1
  if errorlevel 1 (
    echo [ERROR] Python command not found on PATH: %PYTHON_CANDIDATE%
    echo [ERROR] Install Python 3.10+ and add it to PATH.
    exit /b 1
  )
)

call "%PYTHON_CANDIDATE%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 39)"
set "PYTHON_CHECK_EXIT=%ERRORLEVEL%"
if "%PYTHON_CHECK_EXIT%"=="0" exit /b 0
if "%PYTHON_CHECK_EXIT%"=="39" (
  echo [ERROR] Python 3.10+ is required. Selected interpreter is older than 3.10: %PYTHON_CANDIDATE%
  exit /b 2
)

echo [ERROR] Could not validate Python interpreter: %PYTHON_CANDIDATE%
echo [ERROR] The interpreter exited with code %PYTHON_CHECK_EXIT% while checking Python 3.10+.
exit /b 3
