"""
computer_system — system profiling, diagnostics, dependency setup.

Operations:
  profile       — Build system.md + system-index.json + apps/*.json
  refresh       — Rebuild the system index
  diagnose      — Run diagnostics on the plugin environment
  setup         — Install runtime dependencies
  setup_status  — Poll dependency installation progress
  command       — Run a single read-only diagnostic command (restricted)
  trace         — Manage task traces
  file          — Search Known Folders (Desktop, Documents, Downloads)
  window        — Window management (list, activate)

This is a read-mostly tool; destructive operations are gated.
"""

from __future__ import annotations

import json
import os
import platform as _platform
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from ..services import SystemProfiler, get_profiler, memory_dir, index_path, profile_path


class ComputerSystem:
    """System environment tool exposed as MCP tool `computer_system`."""

    def __init__(self):
        self._profiler = get_profiler()

    # ── Profile ────────────────────────────────────────────────────────────

    async def profile(self, reason: str = "manual refresh") -> dict:
        """Build or refresh the system profile."""
        try:
            self._profiler.profile(reason)
            return {
                "status": "ok",
                "system_md": str(profile_path()),
                "system_index": str(index_path()),
                "scale_factor": self._profiler.get_scale_factor(),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def refresh(self) -> dict:
        """Alias for profile()."""
        return await self.profile("refresh")

    # ── Diagnose ───────────────────────────────────────────────────────────

    async def diagnose(self) -> dict:
        """Run environment diagnostics."""
        result: dict = {
            "status": "ok",
            "platform": _platform.platform(),
            "python": sys.version,
            "dependencies": {},
        }

        # Check key dependencies
        deps = {
            "Pillow": "Pillow",
            "comtypes": "comtypes",
            "easyocr": "easyocr",
            "numpy": "numpy",
        }
        for name, pkg in deps.items():
            try:
                mod = __import__(pkg)
                version = getattr(mod, "__version__", "installed")
                result["dependencies"][name] = version
            except ImportError:
                result["dependencies"][name] = "missing"

        # Check system index
        idx = index_path()
        result["system_index"] = {
            "exists": idx.exists(),
            "path": str(idx),
        }

        # Check profile
        prof = profile_path()
        result["system_profile"] = {
            "exists": prof.exists(),
            "path": str(prof),
        }

        # Perception availability
        try:
            from ..perception import PerceptionRouter
            router = PerceptionRouter()
            result["perception"] = {
                "precision_available": router.precision_available,
                "vision_available": router.vision_available,
            }
        except Exception:
            result["perception"] = {"error": "could not initialize"}

        return result

    # ── Setup ──────────────────────────────────────────────────────────────

    async def setup(self, confirmed: bool = False) -> dict:
        """Install runtime dependencies.

        Requires explicit confirmation from the user.
        """
        if not confirmed:
            return {
                "status": "blocked",
                "code": "setup_required",
                "message": "Dependency installation requires confirmation. "
                           "Set confirmed=true to proceed.",
            }
        try:
            from ..environment_setup import install_dependencies
            result = install_dependencies()
            # install_dependencies returns a status dict; propagate failures
            # instead of blindly reporting success.
            if isinstance(result, dict) and result.get("status") in ("failed", "error"):
                return {"status": "error", "code": "setup_failed",
                        "message": "Dependency installation failed",
                        "detail": str(result.get("error", result))}
            return {"status": "ok", "message": "Dependency installation started"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def setup_status(self) -> dict:
        """Poll dependency installation progress."""
        try:
            from ..environment_setup import get_setup_status
            status = get_setup_status()
            return {"status": "ok", "setup_status": status}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    # ── Command ────────────────────────────────────────────────────────────

    async def command(self, cmd: str) -> dict:
        """Run a single read-only diagnostic command.

        Restricted to safe commands only. No shell pipelines, redirects,
        or metacharacters that could chain arbitrary commands.
        """
        import re as _re
        import shlex

        # Whitelist of safe read-only commands.
        # Only real executables: cmd.exe built-ins (ver/set/echo/date/time/
        # dir/tree) have no .exe and fail with FileNotFoundError under
        # shell=False — listing them would only produce confusing errors.
        safe_commands = {
            "whoami", "hostname",
            "where", "systeminfo", "tasklist",
        }

        raw = cmd.strip()
        if not raw:
            return {"status": "error", "error": "empty command"}

        # Reject shell metacharacters that enable command chaining/injection
        if _re.search(r'[|&;><`\$\(\)!{}]', raw):
            return {
                "status": "blocked",
                "code": "unsafe_command",
                "message": "Command contains shell metacharacters; "
                           "pipelines, redirects, and chaining are not allowed",
            }

        # Parse into tokens — each token must be a simple word
        try:
            tokens = shlex.split(raw, posix=False)
        except ValueError:
            return {"status": "error", "error": "command could not be parsed safely"}

        if not tokens:
            return {"status": "error", "error": "empty command"}

        base = tokens[0].lower()
        if base.endswith(".exe"):
            base = base[:-4]
        if base not in safe_commands:
            return {
                "status": "blocked",
                "code": "unsafe_command",
                "message": f"Command '{base}' is not in the allowed read-only set",
            }

        try:
            result = subprocess.run(
                tokens, shell=False,
                capture_output=True, text=True,
                timeout=30,
            )
            return {
                "status": "ok",
                "stdout": result.stdout[:4096],
                "stderr": result.stderr[:1024],
                "exit_code": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"status": "error", "error": "command timed out"}
        except FileNotFoundError:
            return {"status": "error", "error": f"command not found: {tokens[0]}"}

    # ── File ───────────────────────────────────────────────────────────────

    async def file_search(
        self,
        query: str,
        folder: str = "desktop",
    ) -> dict:
        """Search for files in Known Folders only.

        Args:
            query: Filename or pattern to search for.
            folder: One of 'desktop', 'documents', 'downloads', 'pictures'.
        """
        import glob as _glob

        index = self._profiler.load_index()
        folders = index.get("known_folders", {})

        # Map folder names
        folder_map = {
            "desktop": "桌面",
            "documents": "文档",
            "downloads": "下载",
            "pictures": "图片",
        }
        key = folder_map.get(folder.casefold(), folder)

        folder_info = folders.get(key, {})
        folder_path = folder_info.get("path", "") if isinstance(folder_info, dict) else str(folder_info)

        if not folder_path or not os.path.isdir(folder_path):
            return {
                "status": "error",
                "code": "folder_not_found",
                "message": f"Known Folder '{folder}' not found or inaccessible",
            }

        import re as _re
        # Sanitize query: escape glob metacharacters to prevent injection
        safe_query = _re.sub(r'[\[\]?{}*\\]', '', query).strip()
        if not safe_query:
            return {"status": "error", "code": "empty_query",
                    "message": "Query is empty after sanitization"}

        pattern = os.path.join(folder_path, f"*{safe_query}*")
        matches = _glob.glob(pattern, recursive=False)[:20]

        return {
            "status": "ok",
            "folder": folder_path,
            "query": query,
            "matches": matches,
            "count": len(matches),
        }

    # ── Window ─────────────────────────────────────────────────────────────

    async def window_list(self) -> dict:
        """List visible top-level windows."""
        from ..platform import get_platform
        plat = get_platform()
        windows = plat.list_windows()
        return {
            "status": "ok",
            "windows": [
                {
                    "id": w.id,
                    "title": w.title,
                    "process_id": w.process_id,
                    "rect": list(w.rect),
                    "dpi": w.dpi,
                    "scale_factor": w.scale_factor,
                }
                for w in windows[:50]
            ],
        }
