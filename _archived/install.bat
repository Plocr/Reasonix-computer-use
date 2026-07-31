@echo off
setlocal

if exist "%~dp0runtime\python.exe" (
    echo Self-contained runtime found. No pip installation is required.
    goto register
)

REM Optional dependency helper. Reasonix does not execute plugin install scripts.
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python 3.10+ is required and must be available on PATH.
    exit /b 1
)

echo Installing Python dependencies for Reasonix Computer Use...
python -m pip install -e "%~dp0"
if errorlevel 1 exit /b 1

:register
echo.
echo Runtime is ready. Register the plugin with one of:
echo   reasonix plugin install "%~dp0" --replace --yes
echo   Reasonix Desktop ^> Settings ^> Plugins ^> Local directory
echo.
echo Validate after installation:
echo   reasonix plugin doctor computer-use
exit /b 0
