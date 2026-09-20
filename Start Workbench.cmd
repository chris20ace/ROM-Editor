@echo off
setlocal
cd /d "%~dp0"
set "WORKBENCH_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%WORKBENCH_PYTHON%" goto run
where python.exe >nul 2>nul
if not errorlevel 1 (
    set "WORKBENCH_PYTHON=python.exe"
    goto run
)
echo Python 3.10 or newer is needed. Install Python or update WORKBENCH_PYTHON in this file.
pause
exit /b 1
:run
"%WORKBENCH_PYTHON%" "%~dp0launch.py"
if errorlevel 1 pause
