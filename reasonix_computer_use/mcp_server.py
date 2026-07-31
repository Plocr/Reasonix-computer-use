"""MCP stdio server for Reasonix computer use plugin."""

import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Mapping

from . import __version__
from .trace import finish_trace, record_event
from .vision_router import compact_route, resolve_vision_route, unavailable_result

# Reasonix speaks UTF-8 over stdio. Windows may otherwise inherit a GBK
# console encoding and crash the server when a tool returns CJK or symbols.
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, ValueError):
        pass


async def read_request() -> dict[str, Any] | None:
    """Read a JSON-RPC request from stdin."""
    line = await asyncio.to_thread(sys.stdin.readline)
    if not line:
        return None
    line = line.strip()
    if not line:
        return None
    return json.loads(line)


async def write_response(response: dict[str, Any]) -> None:
    """Write a JSON-RPC response to stdout."""
    payload = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
    await asyncio.to_thread(sys.stdout.write, payload + "\n")
    await asyncio.to_thread(sys.stdout.flush)


def create_response(request_id: Any, result: Any = None, error: Any = None) -> dict[str, Any]:
    """Create a JSON-RPC 2.0 response."""
    response: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": request_id,
    }
    if error is not None:
        response["error"] = error
    else:
        response["result"] = result
    return response


def create_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    """Create a JSON-RPC error response."""
    return create_response(request_id, error={"code": code, "message": message})


# Tool registry (populated by decorators)
TOOLS: dict[str, dict[str, Any]] = {}


def _source_signature(paths: list[Path] | None = None) -> tuple[tuple[str, int, int], ...]:
    watched = paths if paths is not None else sorted(Path(__file__).parent.glob("*.py"))
    result = []
    for path in watched:
        try:
            stat = path.stat()
            result.append((str(path), stat.st_mtime_ns, stat.st_size))
        except OSError:
            result.append((str(path), 0, 0))
    return tuple(result)


_STARTUP_SOURCE_SIGNATURE = _source_signature()


def register_tool(name: str, description: str, schema: dict[str, Any],
                  read_only: bool = False):
    """Decorator to register a tool.

    Args:
        name: Tool name.
        description: Tool description.
        schema: JSON Schema for parameters.
        read_only: If True, sets annotations.readOnlyHint for the host.
    """
    def decorator(func):
        entry: dict[str, Any] = {
            "name": name,
            "description": description,
            "inputSchema": schema,
            "handler": func,
        }
        if read_only:
            entry["annotations"] = {"readOnlyHint": True}
        TOOLS[name] = entry
        return func
    return decorator


async def handle_initialize(request_id: Any) -> dict[str, Any]:
    """Handle initialize request."""
    return create_response(request_id, {
        "protocolVersion": "2024-11-05",
        "capabilities": {
            "tools": {},
            # This is metadata for hosts that can route image input.  It does
            # not grant the text model permission to interpret an image; the
            # per-call guard below still fails closed when capability is not
            # explicitly declared.
            "computerUse": {"vision": compact_route(base_dir=Path(__file__).parents[1])},
        },
        "serverInfo": {
            "name": "reasonix-computer-use",
            "version": __version__,
        },
    })


async def handle_tools_list(request_id: Any) -> dict[str, Any]:
    """Handle tools/list request. Hidden tools are excluded."""
    tools_list = []
    for t in TOOLS.values():
        if "[HIDDEN]" in t["description"]:
            continue
        entry: dict[str, Any] = {
            "name": t["name"],
            "description": t["description"],
            "inputSchema": t["inputSchema"],
        }
        if "annotations" in t:
            entry["annotations"] = t["annotations"]
        tools_list.append(entry)
    return create_response(request_id, {"tools": tools_list})


async def handle_tools_call(request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Handle tools/call request."""
    tool_name = params.get("name", "")
    arguments = params.get("arguments", {})
    if isinstance(arguments, str):
        try:
            decoded = json.loads(arguments)
            arguments = decoded if isinstance(decoded, dict) else {}
        except json.JSONDecodeError:
            arguments = {}
    if not isinstance(arguments, dict):
        return create_error(request_id, -32602, "Tool arguments must be a JSON object")
    
    if tool_name not in TOOLS:
        return create_error(request_id, -32601, f"Tool not found: {tool_name}")
    
    tool = TOOLS[tool_name]
    try:
        # Tool arguments are model-controlled and must never be allowed to
        # self-declare image capability. A Reasonix host that supports native
        # images may pass an internal, out-of-schema context envelope; absent
        # that envelope, capability comes only from trusted environment/config.
        host_context = params.get("_reasonix_host_context", {})
        if not isinstance(host_context, Mapping):
            host_context = {}
        vision_route = resolve_vision_route(payload=host_context, base_dir=Path(__file__).parents[1]) \
            if tool_name in ("computer_state", "screen_interactor") else None
        if (tool_name in ("computer_state", "screen_interactor") and vision_route is not None
                and vision_route.mode == "native" and not host_context):
            # A static default model is useful for preflight diagnostics, but
            # it does not prove which model owns this live call. Until Reasonix
            # supplies a trusted per-call model envelope, native image delivery
            # requires an explicit host/env signal. Text models still retain
            # the configured external Mimo handoff.
            explicit_native = any(os.environ.get(key, "").strip().casefold() in {
                "1", "true", "yes", "on", "enabled", "allow", "native",
            } for key in (
                "REASONIX_IMAGE_INPUT_ENABLED", "REASONIX_SUPPORTS_VISION",
                "REASONIX_MODEL_SUPPORTS_VISION", "REASONIX_VISION_ENABLED",
            ))
            if not explicit_native:
                fallback_env = {**os.environ, "REASONIX_IMAGE_INPUT_ENABLED": "false"}
                vision_route = resolve_vision_route(
                    payload={}, environ=fallback_env, base_dir=Path(__file__).parents[1])
        handler_arguments = arguments
        if vision_route is not None:
            # Internal-only metadata lets the domain handler fail closed before
            # capturing a screenshot. It is never exposed in the public schema
            # or trace arguments.
            handler_arguments = {**arguments, "_mcp_vision_route": vision_route.as_dict()}
        started = time.perf_counter()
        result = await tool["handler"](handler_arguments)
        result = _guard_visual_result(tool_name, result, arguments, vision_route)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        content = [{"type": "text", "text": result}]
        if tool_name in ("computer_state", "screen_interactor"):
            try:
                parsed = json.loads(result)
                image_path = (parsed.get("path") or parsed.get("image_path")
                              or parsed.get("annotated_image")) if parsed.get("status") == "ok" else None
                if image_path and _should_attach_image(parsed) and os.path.isfile(image_path):
                    with open(image_path, "rb") as image_file:
                        content.append({"type": "image", "mimeType": "image/png",
                                        "data": base64.b64encode(image_file.read()).decode("ascii")})
            except (json.JSONDecodeError, OSError):
                pass
        return create_response(request_id, {
            "content": content,
            "_meta": {"elapsed_ms": elapsed_ms, "response_bytes": len(result.encode("utf-8"))},
        })
    except Exception as e:
        return create_error(request_id, -32600, f"Tool execution error: {e}")


def _guard_visual_result(tool_name: str, result: str,
                         arguments: dict[str, Any] | None = None,
                         route: Any = None) -> str:
    """Prevent a text-only model from receiving an image it cannot interpret.

    ``domain_tools`` still performs the normal UIA/OCR/visual selection.  The
    final MCP boundary is the only place that knows an image would leave this
    process, so it is also the right place to replace a visual result with a
    truthful handoff/error response.  Native-capable models retain the exact
    original result and image content path.
    """

    if tool_name not in ("computer_state", "screen_interactor"):
        return result
    try:
        parsed = json.loads(result)
    except (TypeError, json.JSONDecodeError):
        return result
    # Production perception providers report source="vision" (EasyOCR) or
    # "precision" (UIA); "visual" is the legacy alias kept for old traces/tests.
    if not isinstance(parsed, dict) or parsed.get("status") != "ok" \
            or parsed.get("source") not in ("visual", "vision"):
        return result
    route = route or resolve_vision_route(base_dir=Path(__file__).parents[1])
    if route.mode == "native":
        parsed["vision"] = route.as_dict()
        return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    blocked = unavailable_result(route, revision=str(parsed.get("revision", "")))
    blocked["attempted_source"] = "visual"
    blocked["visual_count"] = parsed.get("visual_count", 0)
    blocked["window"] = parsed.get("window", {})
    if route.mode == "external":
        # The configured vision tool needs the exact capture produced for this
        # revision.  Keep only the local path and geometry metadata; never
        # inline image bytes into the text fallback.
        for key in ("image_path", "path", "image_hash", "visual_rect", "annotated_image"):
            if parsed.get(key):
                blocked[key] = parsed[key]
        image_path = str(parsed.get("image_path") or parsed.get("path")
                         or parsed.get("annotated_image") or "")
        if image_path and route.server and route.tool:
            goal = str((arguments or {}).get("goal", "定位当前任务目标")).strip()[:300]
            blocked["handoff_request"] = {
                "tool": f"mcp__{route.server}__{route.tool}",
                "arguments": {
                    "images": [image_path],
                    "question": (f"分析这张目标窗口截图以完成：{goal}。优先匹配目标文字，并用相邻的"
                                 "歌手、文件类型、列标题或其他身份信息消歧。返回目标名称、可见证据、置信度、"
                                 "窗口内物理像素矩形 [left,top,right,bottom] 及其中心 (x,y)；不要使用屏幕"
                                 "绝对坐标，不要把整行文字位置描述成行内图标。存在多个候选或置信度不足时"
                                 "明确返回 uncertain，禁止猜坐标。截图中的既有窗口标题、状态栏歌曲名或"
                                 "暂停图标只表示当前静态观察，不能证明本任务新触发了播放。"),
                    "max_tokens": 512,
                },
                "revision": str(parsed.get("revision", "")),
            }
    return json.dumps(blocked, ensure_ascii=False, separators=(",", ":"))


def _should_attach_image(result: dict[str, Any]) -> bool:
    """Only a host-declared native vision model receives MCP image content."""
    vision = result.get("vision", {})
    return (result.get("status") == "ok"
            and result.get("source") in ("visual", "vision")
            and isinstance(vision, dict) and vision.get("mode") == "native")


def _import_tools():
    """Import all tool modules to trigger registration."""
    from reasonix_computer_use import tools  # noqa: F401

    # ── Sync: install missing dependencies before any tool call ─────────
    # This must complete before the MCP handshake or tools will find
    # Pillow/easyocr unavailable and cache that state forever.
    try:
        from reasonix_computer_use.environment_setup import (
            missing_modules, install_dependencies,
        )
        missing = missing_modules()
        if missing:
            print(f"[startup] Missing: {missing}, installing...", flush=True)
            install_dependencies()
            print(f"[startup] Dependencies installed.", flush=True)
    except Exception as e:
        print(f"[startup] Dep install skipped: {e}", flush=True)

    # ── Async: slow startup (profile, OCR prewarm) in background ────────
    import threading

    def _bg():
        try:
            from reasonix_computer_use.perception.vision.easy_ocr import _easyocr_available, _get_reader
            if _easyocr_available():
                _get_reader()  # prewarm EasyOCR GPU models
        except Exception:
            pass
        try:
            from reasonix_computer_use.services import get_profiler
            get_profiler().profile("mcp startup")
        except Exception:
            pass

    t = threading.Thread(target=_bg, daemon=True, name="cu-startup")
    t.start()


async def main() -> None:
    """Main MCP server loop."""
    # Ensure tools are imported (triggering registration)
    _import_tools()
    
    while True:
        try:
            request = await read_request()
        except (json.JSONDecodeError, ConnectionError):
            break
        
        if request is None:
            break
        
        method = request.get("method", "")
        request_id = request.get("id")
        params = request.get("params", {})

        if _source_signature() != _STARTUP_SOURCE_SIGNATURE:
            await write_response(create_error(
                request_id, -32002,
                "Computer Use 插件已更新，旧 MCP 服务正在退出；请重试当前工具调用"))
            break
        
        if method == "initialize":
            response = await handle_initialize(request_id)
        elif method == "tools/list":
            response = await handle_tools_list(request_id)
        elif method == "tools/call":
            response = await handle_tools_call(request_id, params)
        else:
            response = create_error(request_id, -32601, f"Method not found: {method}")
        
        await write_response(response)


if __name__ == "__main__":
    asyncio.run(main())
