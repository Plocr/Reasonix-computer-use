@echo off
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8:backslashreplace"
if exist "%~dp0..\runtime\python.exe" (
    "%~dp0..\runtime\python.exe" -m reasonix_computer_use.session_start
) else (
    python -m reasonix_computer_use.session_start
)
exit /b %errorlevel%
