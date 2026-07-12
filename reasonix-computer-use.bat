@echo off
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8:backslashreplace"
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
REM Wrapper to launch the Reasonix Computer Use MCP server.
REM Reasonix plugin loader resolves this batch file relative to the plugin root,
REM Release archives include an embedded runtime; source checkouts fall back to PATH.

if exist "%~dp0runtime\python.exe" (
    "%~dp0runtime\python.exe" -m reasonix_computer_use %*
) else (
    python -m reasonix_computer_use %*
)
