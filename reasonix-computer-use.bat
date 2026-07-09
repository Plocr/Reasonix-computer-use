@echo off
REM Wrapper to launch the Reasonix Computer Use MCP server.
REM Reasonix plugin loader resolves this batch file relative to the plugin root,
REM and Python is then resolved from the system PATH.

python -m reasonix_computer_use %*
