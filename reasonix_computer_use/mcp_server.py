"""MCP stdio server for Reasonix computer use plugin."""

import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from .trace import finish_trace, record_event, start_trace

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
    watched = paths or [Path(__file__), Path(__file__).with_name("runtime.py"),
                        Path(__file__).with_name("domain_tools.py")]
    result = []
    for path in watched:
        try:
            stat = path.stat()
            result.append((str(path), stat.st_mtime_ns, stat.st_size))
        except OSError:
            result.append((str(path), 0, 0))
    return tuple(result)


_STARTUP_SOURCE_SIGNATURE = _source_signature()


def register_tool(name: str, description: str, schema: dict[str, Any]):
    """Decorator to register a tool."""
    def decorator(func):
        TOOLS[name] = {
            "name": name,
            "description": description,
            "inputSchema": schema,
            "handler": func,
        }
        return func
    return decorator


async def handle_initialize(request_id: Any) -> dict[str, Any]:
    """Handle initialize request."""
    return create_response(request_id, {
        "protocolVersion": "2024-11-05",
        "capabilities": {
            "tools": {},
        },
        "serverInfo": {
            "name": "reasonix-computer-use",
            "version": __version__,
        },
    })


async def handle_tools_list(request_id: Any) -> dict[str, Any]:
    """Handle tools/list request."""
    tools_list = [
        {
            "name": t["name"],
            "description": t["description"],
            "inputSchema": t["inputSchema"],
        }
        for t in TOOLS.values()
    ]
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
        started = time.perf_counter()
        result = await tool["handler"](arguments)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        try:
            parsed_result = json.loads(result)
            from .runtime import REGISTRY
            context = None
            window_id = str(arguments.get("window_id") or parsed_result.get("window", {}).get("id") or "")
            if window_id:
                try:
                    context = REGISTRY.get(window_id)
                except KeyError:
                    context = None
            if context is not None and not context.trace_id:
                context.trace_id = start_trace("computer-task", metadata={
                    "tool": tool_name, "operation": arguments.get("operation", "")})
            if context is not None and context.trace_id:
                event = {"computer_app": "window_revision", "computer_state": "perception",
                         "computer_action": "action", "computer_system": "environment"}.get(tool_name, "verification")
                trace_data: dict[str, Any] = {
                    "window_id": context.window_id,
                    "revision": parsed_result.get("revision") or parsed_result.get("window", {}).get("revision", ""),
                    "status": parsed_result.get("status"),
                    "elapsed_ms": elapsed_ms,
                }
                if tool_name == "computer_state":
                    trace_data.update({"source": parsed_result.get("source"),
                                       "blocked": bool(parsed_result.get("blocked")),
                                       "progress": bool(parsed_result.get("progress")),
                                       "element_count": len(parsed_result.get("elements", []))})
                elif tool_name == "computer_action":
                    trace_data.update({"actions": arguments.get("actions", []),
                                       "verification": parsed_result.get("verification", {}),
                                       "blocked": bool(parsed_result.get("blocked"))})
                else:
                    trace_data["operation"] = arguments.get("operation", "")
                record_event(context.trace_id, event, trace_data)
                if tool_name == "computer_app" and arguments.get("operation") == "close":
                    finish_trace(context.trace_id, "completed" if parsed_result.get("status") == "ok" else "failed")
        except (json.JSONDecodeError, OSError, ValueError, TypeError):
            pass
        content = [{"type": "text", "text": result}]
        if tool_name == "computer_state":
            try:
                parsed = json.loads(result)
                image_path = (parsed.get("path") or parsed.get("image_path")) if parsed.get("status") == "ok" else None
                if image_path and os.path.isfile(image_path):
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


def _import_tools():
    """Import all tool modules to trigger registration."""
    from reasonix_computer_use import tools  # noqa: F401
    from reasonix_computer_use.system_index import start_background_enrichment, start_change_watcher
    from reasonix_computer_use.text_vision import prewarm_ocr
    start_background_enrichment()
    start_change_watcher()
    prewarm_ocr()


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
