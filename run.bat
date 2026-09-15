@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
  where py >nul 2>nul
  if errorlevel 1 (
    echo [DeepSonder-PySide6] Python was not found. Install Python 3.12.
    exit /b 1
  )
  echo [DeepSonder-PySide6] Creating the Python environment...
  py -3.12 -m venv .venv
  if errorlevel 1 exit /b 1
)

"%PYTHON_EXE%" -c "import PySide6" >nul 2>nul
if errorlevel 1 (
  echo [DeepSonder-PySide6] Installing runtime dependencies...
  "%PYTHON_EXE%" -m pip install -r requirements.txt
  if errorlevel 1 exit /b 1
)

if /i "%DEEPSONDER_CHECK_ONLY%"=="1" (
  echo [DeepSonder-PySide6] Launcher prerequisites passed.
  exit /b 0
)

echo [DeepSonder-PySide6] Starting...
"%PYTHON_EXE%" main.py
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" echo [DeepSonder-PySide6] Startup failed with exit code %EXIT_CODE%.
exit /b %EXIT_CODE%
