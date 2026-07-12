@echo off
REM Reasonix Computer Use — Post-action logging hook (Windows batch)

set "TOOL_NAME=%REASONIX_TOOL_NAME%"
set "EXIT_CODE=%REASONIX_EXIT_CODE%"
set "MEMORY_DIR=%REASONIX_MEMORY_DIR%"
if not defined MEMORY_DIR set "MEMORY_DIR=%REASONIX_PLUGIN_ROOT%\memory"
if not defined MEMORY_DIR set "MEMORY_DIR=%CD%\memory"
set "LOG_FILE=%MEMORY_DIR%\operation-log.md"

if not exist "%MEMORY_DIR%" mkdir "%MEMORY_DIR%"

REM Log metadata only. Tool arguments and typed text are intentionally omitted.
echo [%date% %time%] Tool: %TOOL_NAME% Exit: %EXIT_CODE%>> "%LOG_FILE%"

exit /b 0
