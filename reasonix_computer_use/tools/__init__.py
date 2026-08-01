"""Register the Reasonix Computer Use MCP tools (v0.9.0-preview).

Public tools (exposed to the host Agent):
  - screen_interactor  — Core tool: observe screens + execute actions
  - computer_system    — System profiling, diagnostics, setup
  - web_navigator      — Web automation routing via Playwright/chrome MCP
  - computer_app       — Backward-compatible alias (launch/focus/close apps)

Hidden tools (internal, not in tools/list):
  - mouse_action, keyboard_action, screenshot, screen_recorder

Legacy aliases (backward compatible):
  - computer_state → screen_interactor (observe mode)
  - computer_action → screen_interactor (execute mode)
"""

from ..mcp_server import TOOLS, register_tool

# Clear legacy registrations (module body runs once per process)
TOOLS.clear()

# Singleton ScreenInteractor instance (shared across observe/execute calls)
_SI_INSTANCE = None

def _get_si():
    global _SI_INSTANCE
    if _SI_INSTANCE is None:
        from .screen_interactor import ScreenInteractor
        _SI_INSTANCE = ScreenInteractor()
    return _SI_INSTANCE


# ── Public tools ────────────────────────────────────────────────────────────

@register_tool(
    name="screen_interactor",
    description="""Core Computer Use execution tool. Two modes:

**observe** — Return a structured screen snapshot with element IDs, bounding boxes,
text, and a11y metadata. Uses precision layer (UIA on Windows) with vision fallback
(EasyOCR). The host Agent uses ELEMENT_REFs from the snapshot to issue actions.

**execute** — Run up to 5 actions against the latest snapshot. Each action carries
an ELEMENT_REF (preferred) with a normalized-coordinate fallback (CLAUDE_1024,
GEMINI_1000, or PIXEL). Conversion to physical pixels is handled internally.

Coordinate protocol: CLAUDE_1024 (0–1023→1024×768), GEMINI_1000 (0–999→1000×1000),
PIXEL (raw, needs resolution context), ELEMENT_REF (element ID, recommended).""",
    schema={
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["observe", "execute"],
                "description": "observe = capture screen state; execute = run actions."
            },
            "window_id": {
                "type": "string",
                "description": "Window to observe (hwnd, title, or omit for foreground)."
            },
            "max_elements": {
                "type": "integer", "default": 80, "minimum": 1, "maximum": 200,
                "description": "Max elements to return (observe mode)."
            },
            "force_vision": {
                "type": "boolean", "default": False,
                "description": "Skip precision layer, go directly to OCR (observe mode)."
            },
            "actions": {
                "type": "array",
                "items": {"type": "object"},
                "maxItems": 5,
                "description": "Action commands to execute (execute mode). Each has type, element_ref, fallback, etc."
            },
            "revision": {
                "type": "integer",
                "description": "Expected snapshot revision for staleness detection (execute mode)."
            },
            "expect": {
                "type": "object",
                "description": "Post-execution verification (text_present, etc.)."
            },
        },
        "required": ["mode"],
    },
)
async def screen_interactor(args: dict) -> str:
    import json
    from .screen_interactor import ScreenInteractor

    si = _get_si()
    mode = args.get("mode", "observe")

    if mode == "observe":
        result = await si.observe(
            window_id=args.get("window_id"),
            max_elements=args.get("max_elements", 80),
            force_vision=args.get("force_vision", False),
        )
    elif mode == "execute":
        result = await si.execute(
            actions=args.get("actions", []),
            revision=args.get("revision"),
            expect=args.get("expect"),
        )
    else:
        result = {"status": "error", "code": "invalid_mode", "message": f"Unknown mode: {mode}"}

    return json.dumps(result, ensure_ascii=False)


@register_tool(
    name="computer_system",
    description="""System profiling, diagnostics, dependency setup, and window management.

Operations:
  profile       — Build system.md + system-index.json + apps/*.json
  refresh       — Rebuild the system index
  diagnose      — Run diagnostics on plugin environment and dependencies
  setup         — Install runtime dependencies (requires confirmed=true)
  setup_status  — Poll dependency installation progress
  command       — Run a single read-only diagnostic command (whitelist restricted)
  file          — Search files in Known Folders (Desktop, Documents, Downloads, Pictures)
  window        — List/activate visible windows""",
    schema={
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["profile", "refresh", "diagnose", "setup", "setup_status",
                         "trace", "file", "window", "command"],
                "description": "Which operation to perform."
            },
            "confirmed": {"type": "boolean", "default": False},
            "query": {"type": "string", "description": "File search query."},
            "folder": {"type": "string", "default": "desktop"},
            "command": {"type": "string", "description": "Read-only diagnostic command."},
        },
        "required": ["operation"],
    },
)
async def computer_system(args: dict) -> str:
    import json
    from .computer_system import ComputerSystem

    cs = ComputerSystem()
    op = args.get("operation", "")

    ops = {
        "profile": lambda: cs.profile(args.get("reason", "mcp call")),
        "refresh": cs.refresh,
        "diagnose": cs.diagnose,
        "setup": lambda: cs.setup(args.get("confirmed", False)),
        "setup_status": cs.setup_status,
        "command": lambda: cs.command(args.get("command", "")),
        "file": lambda: cs.file_search(args.get("query", ""), args.get("folder", "desktop")),
        "window": cs.window_list,
    }

    handler = ops.get(op)
    if handler is None:
        result = {"status": "error", "code": "unknown_operation", "message": f"Unknown operation: {op}"}
    else:
        result = await handler()

    return json.dumps(result, ensure_ascii=False)


@register_tool(
    name="web_navigator",
    description="""[BROWSER ONLY] Web automation routing. Does NOT control the desktop.
Routes browser scenarios to Playwright MCP (isolated) or mcp-chrome (login reuse).

Operations:
  navigate     — Route to URL via Playwright MCP or mcp-chrome
  snapshot     — Get page a11y tree (browser page only)
  action       — Route a browser action (click, fill, type, press_key)""",
    schema={
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["navigate", "snapshot", "action"],
                "description": "Which web operation to route."
            },
            "url": {"type": "string", "description": "Target URL (navigate mode)."},
            "use_chrome": {"type": "boolean", "default": False},
            "server": {"type": "string", "default": "playwright"},
            "action_type": {"type": "string"},
            "uid": {"type": "string"},
            "value": {"type": "string"},
            "key": {"type": "string"},
        },
        "required": ["operation"],
    },
    read_only=True,
)
async def web_navigator(args: dict) -> str:
    import json
    from .web_navigator import WebNavigator

    wn = WebNavigator()
    op = args.get("operation", "")

    if op == "navigate":
        result = await wn.navigate(
            url=args.get("url", ""),
            use_chrome=args.get("use_chrome", False),
        )
    elif op == "snapshot":
        result = await wn.get_snapshot(server=args.get("server", "playwright"))
    elif op == "action":
        result = await wn.execute_action(
            action_type=args.get("action_type", "click"),
            server=args.get("server", "playwright"),
            uid=args.get("uid"),
            value=args.get("value"),
            key=args.get("key"),
        )
    else:
        result = {"status": "error", "code": "unknown_operation"}

    return json.dumps(result, ensure_ascii=False)


# ── Backward-compatible aliases ─────────────────────────────────────────────

@register_tool(
    name="computer_app",
    description="""Backward-compatible alias. Launch, focus, list, or close Windows applications.
Prefer screen_interactor for observation and action execution.""",
    schema={
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["search", "launch", "open_file", "focus", "list_running", "close"]},
            "query": {"type": "string"},
            "path": {"type": "string", "description": "File path for open_file."},
            "app_id": {"type": "string"},
            "window_id": {"type": "string"},
            "close_mode": {"type": "string", "enum": ["window", "process"]},
            "confirmed": {"type": "boolean", "default": False},
            "limit": {"type": "integer", "default": 10},
        },
        "required": ["operation"],
    },
)
async def computer_app(args: dict) -> str:
    import json
    from ..platform import get_platform
    plat = get_platform()
    op = args.get("operation", "")

    if op == "list_running":
        windows = plat.list_windows()
        return json.dumps({
            "status": "ok", "windows": [
                {"id": w.id, "title": w.title, "process_id": w.process_id}
                for w in windows[:args.get("limit", 10)]
            ]
        }, ensure_ascii=False)

    if op == "search":
        # Live re-scan and update system image
        import json as _j
        try:
            from ..services import get_profiler
            profiler = get_profiler()
            profiler.profile("search rescan")
            index = profiler.load_index()
            apps = index.get("applications", [])
            query = args.get("query", "").lower()
            matches = [a for a in apps if query in a.get("name","").lower()][:20]
            return _j.dumps({
                "status": "ok", "total_apps": len(apps),
                "matches": matches, "count": len(matches)
            }, ensure_ascii=False)
        except Exception as e:
            return _j.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)

    if op == "focus":
        window_id = args.get("window_id")
        if not window_id:
            return json.dumps({"status": "error", "code": "missing_window_id",
                               "error": "focus requires window_id"}, ensure_ascii=False)
        try:
            activated = plat.activate_window(window_id)
        except Exception as exc:
            return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)
        if not activated:
            return json.dumps({"status": "error", "code": "activation_denied",
                               "error": f"窗口激活失败（可能被系统前台锁定或窗口无效）: {window_id}",
                               "hint": "确认目标窗口存在且未最小化；Windows 前台锁定限制下无法强制激活"},
                              ensure_ascii=False)
        return json.dumps({"status": "ok", "window_id": window_id}, ensure_ascii=False)

    if op == "close":
        # Window close via Alt+F4.  NEVER fall through if activation fails:
        # Alt+F4 would go to whatever window IS in the foreground, which may
        # close the wrong (user's active) window while still reporting ok.
        window_id = args.get("window_id")
        if not window_id:
            return json.dumps({"status": "error", "code": "missing_window_id",
                               "error": "close requires window_id"}, ensure_ascii=False)
        try:
            activated = plat.activate_window(window_id)
        except Exception as exc:
            return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)
        if not activated:
            return json.dumps({"status": "error", "code": "activation_denied",
                               "error": f"窗口激活失败，已取消关闭以避免误关其他窗口: {window_id}"},
                              ensure_ascii=False)
        # Double-check the target is actually the foreground window before
        # sending Alt+F4.
        try:
            fg = plat.get_foreground_window()
        except Exception:
            fg = None
        if fg is None or fg.id != window_id:
            return json.dumps({"status": "error", "code": "foreground_mismatch",
                               "error": f"前台窗口({fg.id if fg else 'none'})不是目标({window_id})，已取消关闭"},
                              ensure_ascii=False)
        plat.keyboard_press(["alt", "f4"])
        return json.dumps({"status": "ok", "action": "close"}, ensure_ascii=False)

    if op == "launch":
        import os
        target = args.get("query", "")
        if not target:
            return json.dumps({"status": "error", "error": "no query"}, ensure_ascii=False)

        # 1. Resolve via system image first
        resolved = target
        from_index = False
        try:
            from ..services import get_profiler
            profiler = get_profiler()
            index = profiler.load_index()
            apps = index.get("applications", [])
            target_lower = target.lower()
            # First try exact name match
            for app in apps:
                if app.get("name", "").lower() == target_lower:
                    resolved = app.get("path", resolved)
                    from_index = True
                    break
            # Then try fuzzy match (name contains target)
            if not from_index:
                for app in apps:
                    if target_lower in app.get("name", "").lower():
                        resolved = app.get("path", resolved)
                        from_index = True
                        break
        except Exception:
            pass

        # shell: pseudo-paths (UWP/Store apps) are only trusted when they came
        # from the app index.  A model-supplied query must never smuggle an
        # arbitrary shell: target (e.g. shell:AppsFolder\\<AppID>) past the
        # existence check.
        if resolved.startswith("shell:") and not from_index:
            return json.dumps({"status": "blocked", "code": "shell_path_rejected",
                               "error": "shell: targets are only allowed from the app index; "
                                        "use computer_app(operation='search', query=...) first"},
                              ensure_ascii=False)

        # Validate: resolved path must exist on disk.
        # UWP/Store apps use "shell:AppsFolder\\<AppID>" pseudo-paths that
        # os.path.exists cannot verify — allow them through to os.startfile.
        if not resolved.startswith("shell:"):
            if not os.path.exists(resolved):
                return json.dumps({"status": "error", "error": f"path not found: {resolved}",
                                   "hint": "Use computer_app(operation='search', query=...) to find apps"},
                                  ensure_ascii=False)

        try:
            os.startfile(resolved)
            return json.dumps({"status": "ok", "launched": resolved}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)

    if op == "open_file":
        import os, re
        path = args.get("path", args.get("query", ""))
        if not path:
            return json.dumps({"status": "error", "error": "no path"}, ensure_ascii=False)

        # Resolve to absolute path and validate existence
        resolved = os.path.abspath(path)
        if not os.path.exists(resolved):
            return json.dumps({"status": "error", "error": f"file not found: {resolved}"},
                              ensure_ascii=False)

        # Block direct execution of PE files via open_file — use launch instead
        dangerous_exts = {'.exe', '.bat', '.cmd', '.ps1', '.vbs', '.js', '.msi',
                          '.com', '.scr', '.lnk', '.hta', '.url', '.pif', '.wsh', '.wsf',
                          '.cpl', '.reg', '.inf'}
        _, ext = os.path.splitext(resolved)
        if ext.lower() in dangerous_exts:
            return json.dumps({"status": "blocked", "code": "executable_blocked",
                               "message": f"Cannot open '{ext}' files via open_file; "
                                          "use computer_app(operation='launch') for executables"},
                              ensure_ascii=False)

        try:
            os.startfile(resolved)
            return json.dumps({"status": "ok", "file": resolved}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)

    return json.dumps({"status": "error", "code": "unknown_operation"}, ensure_ascii=False)


@register_tool(
    name="computer_state",
    description="""Legacy alias for screen_interactor(observe). Returns structured screen snapshot.""",
    schema={
        "type": "object",
        "properties": {
            "window_id": {"type": "string"},
            "max_elements": {"type": "integer", "default": 80},
        },
    },
    read_only=True,
)
async def computer_state(args: dict) -> str:
    import json
    si = _get_si()
    result = await si.observe(window_id=args.get("window_id"), max_elements=args.get("max_elements", 80))
    return json.dumps(result, ensure_ascii=False)


@register_tool(
    name="computer_action",
    description="""Legacy alias for screen_interactor(execute). Runs action batch.""",
    schema={
        "type": "object",
        "properties": {
            "actions": {"type": "array", "items": {"type": "object"}, "maxItems": 5},
            "revision": {"type": "integer"},
            "expect": {"type": "object"},
        },
        "required": ["actions"],
    },
)
async def computer_action(args: dict) -> str:
    import json
    si = _get_si()
    result = await si.execute(actions=args.get("actions", []), revision=args.get("revision"), expect=args.get("expect"))
    return json.dumps(result, ensure_ascii=False)


# ── Hidden tools (internal use only) ────────────────────────────────────────

@register_tool(
    name="mouse_action",
    description="[HIDDEN] Raw mouse operations with normalized coordinates. Internal use only.",
    schema={"type": "object", "properties": {}},
)
async def mouse_action(args: dict) -> str:
    return '{"status":"ok","note":"hidden tool — use screen_interactor instead"}'

@register_tool(
    name="keyboard_action",
    description="[HIDDEN] Raw keyboard operations. Internal use only.",
    schema={"type": "object", "properties": {}},
)
async def keyboard_action(args: dict) -> str:
    return '{"status":"ok","note":"hidden tool — use screen_interactor instead"}'

@register_tool(
    name="screenshot",
    description="[HIDDEN] Screen capture to Downloads. Internal use only.",
    schema={"type": "object", "properties": {}},
)
async def screenshot(args: dict) -> str:
    return '{"status":"ok","note":"hidden tool — use screen_interactor instead"}'

@register_tool(
    name="screen_recorder",
    description="[HIDDEN] Screen recording (system-native). Internal use only.",
    schema={"type": "object", "properties": {}},
)
async def screen_recorder(args: dict) -> str:
    return '{"status":"ok","note":"hidden tool — use screen_interactor instead"}'
