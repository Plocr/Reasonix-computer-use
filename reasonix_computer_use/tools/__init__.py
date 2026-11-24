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
            "observe_mode": {
                "type": "string",
                "enum": ["auto", "ocr", "precision"],
                "description": ("observe mode layer selection. 'ocr' forces a screenshot+OCR pass "
                                "(never satisfied by an earlier precision result); 'precision' "
                                "forces UIA/AXAPI/AT-SPI2 even for a temporarily blacklisted window; "
                                "'auto' keeps the precision-first strategy.")
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
            mode=args.get("observe_mode"),
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
            "trace_id": {"type": "string", "description": "Trace ID to read (operation=trace)."},
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
        "trace": lambda: cs.trace(args.get("trace_id", "")),
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
            "rescan": {"type": "boolean", "default": False, "description": "Force a full app re-scan (search operation)."},
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
        # Search uses the existing system index. A full re-scan (registry +
        # shortcuts + drives) is only performed when the index is missing or
        # explicitly requested via rescan=true — never on every search.
        import json as _j
        try:
            from ..services import get_profiler
            profiler = get_profiler()
            index = profiler.load_index()
            apps = index.get("applications", [])
            if (not index or not index.get("system") or not apps) or args.get("rescan"):
                profiler.profile("search rescan")
                index = profiler.load_index()
                apps = index.get("applications", [])
            query = args.get("query", "").lower()
            matches = [a for a in apps if query in a.get("name","").lower()][:20]
            return _j.dumps({
                "status": "ok", "total_apps": len(apps),
                "matches": matches, "count": len(matches),
                "rescan": bool(args.get("rescan")),
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
    description="""Legacy alias for screen_interactor(observe). Returns structured screen snapshot.

`mode` selects the perception layer and is honoured (it used to be silently
dropped, which made icon-only WebView2 controls unreachable):
  auto      — precision-first, vision fallback (default)
  ocr       — force screenshot + OCR; never satisfied by a cached UIA result
  precision — force UIA/AXAPI/AT-SPI2, ignoring the temporary blacklist""",
    schema={
        "type": "object",
        "properties": {
            "window_id": {"type": "string"},
            "max_elements": {"type": "integer", "default": 80},
            "operation": {"type": "string", "description": "Ignored (legacy hosts send 'state')."},
            "mode": {
                "type": "string", "default": "auto",
                "enum": ["auto", "ocr", "vision", "precision", "uia"],
                "description": "Perception layer: auto | ocr | precision.",
            },
            "force_vision": {"type": "boolean", "default": False},
        },
    },
    read_only=True,
)
async def computer_state(args: dict) -> str:
    import json
    si = _get_si()
    result = await si.observe(
        window_id=args.get("window_id"),
        max_elements=args.get("max_elements", 80),
        force_vision=bool(args.get("force_vision", False)),
        mode=args.get("mode"),
    )
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
    schema={
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["click", "double_click", "right_click", "drag", "scroll"]},
            "x": {"type": "integer", "description": "Normalized x coordinate."},
            "y": {"type": "integer", "description": "Normalized y coordinate."},
            "space": {"type": "string", "enum": ["CLAUDE_1024", "GEMINI_1000", "PIXEL"], "default": "CLAUDE_1024"},
            "to_x": {"type": "integer", "description": "Drag destination x (PIXEL)."},
            "to_y": {"type": "integer", "description": "Drag destination y (PIXEL)."},
            "amount": {"type": "integer", "default": 3, "description": "Scroll amount (positive = down/right)."},
            "direction": {"type": "string", "enum": ["vertical", "up", "down", "left", "right"], "default": "vertical"},
            "duration": {"type": "number", "default": 0.5, "description": "Drag duration in seconds."},
        },
        "required": ["operation"],
    },
)
async def mouse_action(args: dict) -> str:
    import json
    from .hidden.actions import HiddenMouse
    from ..protocol import NormalizedCoord, CoordinateSpace

    mouse = HiddenMouse()
    op = args.get("operation", "click")
    coord = NormalizedCoord(
        x=int(args.get("x", 0)),
        y=int(args.get("y", 0)),
        space=CoordinateSpace(args.get("space", "CLAUDE_1024")),
    )
    if op == "click":
        result = mouse.click(coord, button="left", count=1)
    elif op == "double_click":
        result = mouse.double_click(coord)
    elif op == "right_click":
        result = mouse.right_click(coord)
    elif op == "drag":
        to_x = int(args.get("to_x", args.get("x", 0)))
        to_y = int(args.get("to_y", args.get("y", 0)))
        to_coord = NormalizedCoord(x=to_x, y=to_y, space=CoordinateSpace.PIXEL)
        result = mouse.drag(coord, to_coord, duration=float(args.get("duration", 0.5)))
    elif op == "scroll":
        result = mouse.scroll(coord, amount=int(args.get("amount", 3)),
                              direction=str(args.get("direction", "vertical")))
    else:
        result = {"status": "error", "code": "unknown_operation", "message": f"Unknown operation: {op}"}
    return json.dumps(result, ensure_ascii=False)


@register_tool(
    name="keyboard_action",
    description="[HIDDEN] Raw keyboard operations. Internal use only.",
    schema={
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["type", "press", "key_down", "key_up"]},
            "text": {"type": "string", "description": "Text to type (operation=type)."},
            "keys": {"type": "array", "items": {"type": "string"}, "description": "Key combination (operation=press)."},
            "key": {"type": "string", "description": "Single key (operation=key_down/key_up)."},
        },
        "required": ["operation"],
    },
)
async def keyboard_action(args: dict) -> str:
    import json
    from .hidden.actions import HiddenKeyboard

    keyboard = HiddenKeyboard()
    op = args.get("operation", "type")
    if op == "type":
        result = keyboard.type(str(args.get("text", "")))
    elif op == "press":
        keys = args.get("keys") or ([args.get("key")] if args.get("key") else [])
        result = keyboard.press([str(k) for k in keys]) if keys else \
            {"status": "error", "code": "missing_keys", "message": "press requires keys"}
    elif op == "key_down":
        result = keyboard.key_down(str(args.get("key", "")))
    elif op == "key_up":
        result = keyboard.key_up(str(args.get("key", "")))
    else:
        result = {"status": "error", "code": "unknown_operation", "message": f"Unknown operation: {op}"}
    return json.dumps(result, ensure_ascii=False)


@register_tool(
    name="screenshot",
    description="[HIDDEN] Screen capture to Downloads. Internal use only.",
    schema={
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "Optional output filename (default: screenshot_<timestamp>.png)."},
        },
    },
)
async def screenshot(args: dict) -> str:
    import json
    from .hidden.actions import HiddenScreenshot

    capture = HiddenScreenshot()
    result = capture.capture(filename=args.get("filename"))
    return json.dumps(result, ensure_ascii=False)


@register_tool(
    name="screen_recorder",
    description="[HIDDEN] Screen recording (system-native). Internal use only.",
    schema={
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["start", "stop"]},
            "filename": {"type": "string", "description": "Optional output filename (operation=start)."},
        },
        "required": ["operation"],
    },
)
async def screen_recorder(args: dict) -> str:
    import json
    from .hidden.actions import HiddenScreenRecorder

    recorder = HiddenScreenRecorder()
    op = args.get("operation", "start")
    if op == "start":
        result = recorder.start(filename=args.get("filename"))
    elif op == "stop":
        result = recorder.stop()
    else:
        result = {"status": "error", "code": "unknown_operation", "message": f"Unknown operation: {op}"}
    return json.dumps(result, ensure_ascii=False)
