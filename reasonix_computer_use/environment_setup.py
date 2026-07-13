"""First-run dependency bootstrap for lightweight Git installations."""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


DEPENDENCIES = (
    "Pillow>=10.0.0",
    "comtypes>=1.4.0",
    "rapidocr-onnxruntime>=1.4.4",
)
MODULES = ("PIL", "comtypes", "rapidocr_onnxruntime")
STALE_SETUP_SECONDS = 900
_PROCESS_STARTED_AT = time.time()


def setup_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Reasonix" / "computer-use"


def packages_dir() -> Path:
    return setup_root() / "site-packages"


def state_path() -> Path:
    return setup_root() / "setup-state.json"


def log_path() -> Path:
    return setup_root() / "setup.log"


def activate_packages() -> None:
    path = str(packages_dir())
    if path not in sys.path:
        sys.path.insert(0, path)
    importlib.invalidate_caches()


def missing_modules() -> list[str]:
    activate_packages()
    return [name for name in MODULES if importlib.util.find_spec(name) is None]


def _read_state() -> dict[str, Any]:
    try:
        value = json.loads(state_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _write_state(value: dict[str, Any]) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {**value, "updated_at": time.time()}
    handle, temporary = tempfile.mkstemp(prefix=".setup-state.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _log_tail(limit: int = 8) -> list[str]:
    try:
        lines = log_path().read_text(encoding="utf-8", errors="replace").splitlines()
        return [line[:240] for line in lines[-max(1, min(limit, 12)):]]
    except OSError:
        return []


def environment_status() -> dict[str, Any]:
    missing = missing_modules()
    state = _read_state()
    status = str(state.get("status", "setup_required" if missing else "ready"))
    updated = float(state.get("updated_at", 0) or 0)
    if status == "installing" and updated and time.time() - updated > STALE_SETUP_SECONDS:
        status = "failed"
        state = {**state, "status": status, "error": "安装进程超时或意外退出"}
        _write_state(state)
    if not missing:
        status = "ready"
    result: dict[str, Any] = {
        "status": status,
        "ready": not missing,
        "missing": missing,
        "packages_dir": str(packages_dir()),
    }
    if status == "ready":
        restart_required = bool(updated and updated > _PROCESS_STARTED_AT)
        result.update({"restart_required": restart_required,
                       "next_hint": ("新建 Reasonix 会话以加载 UIA/OCR 依赖"
                                     if restart_required else "环境已就绪，可以使用 Computer Use")})
    if status == "installing":
        result.update({"phase": state.get("phase", "pip_install"), "pid": state.get("pid"),
                       "poll_after_seconds": 3, "log_tail": _log_tail()})
    elif status == "failed":
        result.update({"error": str(state.get("error", "依赖安装失败"))[:300],
                       "log_tail": _log_tail()})
    return result


def _progress_marker() -> tuple[Any, ...]:
    state = _read_state()
    try:
        log = log_path().stat()
        log_marker = (log.st_mtime_ns, log.st_size)
    except OSError:
        log_marker = (0, 0)
    return (state.get("status"), state.get("updated_at"), *log_marker)


def wait_environment_status(wait_seconds: float = 0) -> dict[str, Any]:
    """Long-poll until setup progress changes, avoiding Agent-side shell sleeps."""
    initial = environment_status()
    if wait_seconds <= 0 or initial.get("status") != "installing":
        return initial
    marker = _progress_marker()
    deadline = time.monotonic() + min(wait_seconds, 30.0)
    while time.monotonic() < deadline:
        time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))
        current = environment_status()
        if current.get("status") != "installing" or _progress_marker() != marker:
            return current
    return environment_status()


def start_environment_setup(confirmed: bool = False) -> dict[str, Any]:
    status = environment_status()
    if status["ready"] or status["status"] == "installing":
        return status
    if not confirmed:
        return {
            **status,
            "status": "confirmation_required",
            "blocked": True,
            "message": "首次 Git 安装需要下载 Python 依赖到当前用户目录",
        }
    if importlib.util.find_spec("pip") is None:
        return {**status, "status": "failed", "error": "当前 Python 没有 pip，请重新安装 Python 3.10+"}
    root = setup_root()
    root.mkdir(parents=True, exist_ok=True)
    _write_state({"status": "installing", "phase": "starting"})
    flags = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
             | getattr(subprocess, "CREATE_NO_WINDOW", 0)
             | getattr(subprocess, "DETACHED_PROCESS", 0))
    process = subprocess.Popen(
        [sys.executable, "-m", "reasonix_computer_use.environment_setup", "--worker"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True, creationflags=flags,
    )
    _write_state({"status": "installing", "phase": "pip_install", "pid": process.pid})
    return environment_status()


def run_worker() -> int:
    root = setup_root()
    target = packages_dir()
    root.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True, exist_ok=True)
    _write_state({"status": "installing", "phase": "pip_install", "pid": os.getpid()})
    command = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
               "--no-input", "--upgrade", "--target", str(target), *DEPENDENCIES]
    try:
        with log_path().open("w", encoding="utf-8", errors="replace") as log:
            completed = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=log,
                                       stderr=subprocess.STDOUT, timeout=900, check=False)
        activate_packages()
        missing = missing_modules()
        if completed.returncode == 0 and not missing:
            _write_state({"status": "ready", "phase": "complete", "returncode": 0})
            return 0
        error = f"pip 返回 {completed.returncode}" if completed.returncode else f"仍缺少模块：{', '.join(missing)}"
        _write_state({"status": "failed", "phase": "complete", "returncode": completed.returncode,
                      "error": error})
        return 1
    except Exception as exc:
        _write_state({"status": "failed", "phase": "exception", "error": str(exc)[:300]})
        return 1


if __name__ == "__main__":
    raise SystemExit(run_worker() if "--worker" in sys.argv else 2)
