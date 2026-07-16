"""Launch interactive targets through a Windows service outside the MCP Job."""

from __future__ import annotations

import base64
import os
import subprocess
from pathlib import Path


_WMI_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = $env:REASONIX_BROKER_COMMAND
    CurrentDirectory = $env:REASONIX_BROKER_CWD
}
if ($result.ReturnValue -ne 0 -or -not $result.ProcessId) {
    throw "Win32_Process.Create failed: $($result.ReturnValue)"
}
[Console]::Out.Write($result.ProcessId)
""".strip()
_WMI_SCRIPT_ENCODED = base64.b64encode(_WMI_SCRIPT.encode("utf-16le")).decode("ascii")


class LaunchBrokerError(RuntimeError):
    pass


def _wmi_create_raw(command_line: str, cwd: str = "") -> int:
    environment = os.environ.copy()
    environment["REASONIX_BROKER_COMMAND"] = command_line
    environment["REASONIX_BROKER_CWD"] = cwd or os.getcwd()
    powershell = str(Path(os.environ.get("WINDIR", r"C:\Windows")) /
                     "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
    completed = subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", _WMI_SCRIPT_ENCODED],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=environment, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    output = completed.stdout.strip().lstrip("\ufeff")
    if completed.returncode != 0 or not output.isdigit():
        message = (completed.stderr or completed.stdout or "WMI launch failed").strip()[:500]
        raise LaunchBrokerError(message)
    return int(output)


def _wmi_create(command: list[str], cwd: str = "") -> int:
    return _wmi_create_raw(subprocess.list2cmdline(command), cwd)


def launch_via_system_broker(target: str, arguments: str = "", cwd: str = "") -> tuple[int, str]:
    command = [target]
    if arguments:
        # Shortcut arguments are already a Windows command-line fragment.
        command_line = subprocess.list2cmdline([target]) + " " + arguments
        return _wmi_create_raw(command_line, cwd or str(Path(target).parent)), "wmi"
    return _wmi_create(command, cwd or str(Path(target).parent)), "wmi"


def shell_execute(target: str) -> int:
    explorer = str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "explorer.exe")
    return _wmi_create([explorer, target], str(Path(explorer).parent))
