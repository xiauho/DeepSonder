@echo off
chcp 65001 >nul
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto :environment_ready

python --version >nul 2>&1
if errorlevel 1 (
  echo [Novalist] 未找到可用的 Python。请先安装 Python 3.10 或更高版本。
  pause
  exit /b 1
)

echo [Novalist] 正在创建项目环境...
python -m venv .venv
if errorlevel 1 goto :error

:environment_ready
set "APP_PYTHON=.venv\Scripts\python.exe"
if /I "%NOVALIST_CHECK_ONLY%"=="1" (
  "%APP_PYTHON%" --version
  if errorlevel 1 goto :error
  echo [Novalist] BAT 启动脚本和项目环境检查通过。
  exit /b 0
)

echo [Novalist] 正在检查运行依赖...
"%APP_PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 goto :error
echo [Novalist] 正在启动...
"%APP_PYTHON%" main.py
if errorlevel 1 goto :error
pause
exit /b 0

:error
echo.
echo [Novalist] 安装依赖或启动失败，请查看上方提示。
pause
exit /b 1
