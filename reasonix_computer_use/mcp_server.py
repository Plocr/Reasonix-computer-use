"""MCP stdio server for Reasonix computer use plugin."""

import asyncio
import json
import sys
from typing import Any


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
    payload = json.dumps(response, ensure_ascii=True)
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
            "version": "0.1.0",
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
    
    if tool_name not in TOOLS:
        return create_error(request_id, -32601, f"Tool not found: {tool_name}")
    
    tool = TOOLS[tool_name]
    try:
        result = await tool["handler"](arguments)
        return create_response(request_id, {
            "content": [
                {
                    "type": "text",
                    "text": result,
                }
            ],
        })
    except Exception as e:
        return create_error(request_id, -32600, f"Tool execution error: {e}")


def _import_tools():
    """Import all tool modules to trigger registration."""
    from reasonix_computer_use import tools  # noqa: F401


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
