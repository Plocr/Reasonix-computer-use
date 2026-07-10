@echo off
REM Reasonix Computer Use — Pre-action safety hook (Windows batch)
REM Reads REASONIX_TOOL_NAME and REASONIX_TOOL_ARGS environment variables

set "TOOL_NAME=%REASONIX_TOOL_NAME%"
set "TOOL_ARGS=%REASONIX_TOOL_ARGS%"

REM Always allow these read-only tools immediately
if /i "%TOOL_NAME%"=="computer_screenshot" exit /b 0
if /i "%TOOL_NAME%"=="computer_window_list" exit /b 0
if /i "%TOOL_NAME%"=="computer_ui_tree" exit /b 0
if /i "%TOOL_NAME%"=="computer_find_element" exit /b 0
if /i "%TOOL_NAME%"=="computer_app_list" exit /b 0

REM Block destructive operations (case-insensitive search in arguments)
echo %TOOL_ARGS%| findstr /i /r "rm.*-rf format delete shutdown reboot sudo" >nul 2>&1
if not errorlevel 1 (
    echo [BLOCKED] Dangerous operation detected: %TOOL_ARGS% >&2
    exit /b 1
)

exit /b 0
