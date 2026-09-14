@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

set "APP_PYTHON=%CD%\.venv\Scripts\python.exe"
set "ELECTRON_DIR=%CD%\electron"
set "NOVALIST_ELECTRON_EXE=%ELECTRON_DIR%\node_modules\electron\dist\electron.exe"

if exist "%APP_PYTHON%" goto :python_ready

where python >nul 2>&1
if errorlevel 1 (
  echo [Novalist] Python was not found. Install Python 3.10 or newer.
  goto :error
)

echo [Novalist] Creating the Python Sidecar environment...
python -m venv .venv
if errorlevel 1 goto :error

:python_ready
"%APP_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
  echo [Novalist] The Python Sidecar requires Python 3.10 or newer.
  goto :error
)

where node >nul 2>&1
if errorlevel 1 (
  echo [Novalist] Node.js was not found. Install Node.js 24 or newer.
  goto :error
)
node -e "process.exit(Number(process.versions.node.split('.')[0]) >= 24 ? 0 : 1)"
if errorlevel 1 (
  echo [Novalist] The Electron workspace requires Node.js 24 or newer.
  goto :error
)

where npm >nul 2>&1
if errorlevel 1 (
  echo [Novalist] npm was not found. Reinstall Node.js with npm.
  goto :error
)

set "ELECTRON_READY=1"
if not exist "%ELECTRON_DIR%\node_modules\.bin\tsc.cmd" set "ELECTRON_READY=0"
if not exist "%ELECTRON_DIR%\node_modules\.bin\vite.cmd" set "ELECTRON_READY=0"
if not exist "%ELECTRON_DIR%\node_modules\.bin\electron.cmd" set "ELECTRON_READY=0"

if /I "%NOVALIST_CHECK_ONLY%"=="1" (
  "%APP_PYTHON%" --version
  node --version
  npm --version
  if "%ELECTRON_READY%"=="0" echo [Novalist] Electron dependencies are incomplete; a normal launch will run npm ci.
  echo [Novalist] Electron launcher prerequisites passed.
  exit /b 0
)

if "%ELECTRON_READY%"=="1" goto :start_electron

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$expected = $env:NOVALIST_ELECTRON_EXE; $running = @(Get-Process electron -ErrorAction SilentlyContinue); $locked = @($running.Where({$_.Path -eq $expected})); if ($locked.Count) { Write-Host ('[Novalist] Project Electron processes still lock dependencies. Close PIDs: ' + ($locked.Id -join ', ')); exit 2 }"
if errorlevel 1 (
  echo [Novalist] Cannot repair Electron dependencies while project processes are running.
  goto :error
)

echo [Novalist] Restoring Electron dependencies from the lock file...
pushd "%ELECTRON_DIR%"
call npm ci
set "INSTALL_EXIT=%ERRORLEVEL%"
popd
if not "%INSTALL_EXIT%"=="0" goto :error

:start_electron
set "NOVALIST_PYTHON=%APP_PYTHON%"
echo [Novalist] Starting the Electron workspace...
pushd "%ELECTRON_DIR%"
if /I "%NOVALIST_SELF_TEST%"=="1" (
  call npm run self-test
) else (
  call npm start
)
set "APP_EXIT=%ERRORLEVEL%"
popd
if not "%APP_EXIT%"=="0" goto :error
exit /b 0

:error
echo.
echo [Novalist] Electron setup or startup failed. Review the message above.
pause
exit /b 1
