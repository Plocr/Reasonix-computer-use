"""Integration tests for Reasonix Computer Use plugin.

These tests verify tool registration, argument parsing, and cross-platform
compatibility without requiring a real GUI environment (so they can run in CI).

For actual GUI tests (screenshot, mouse, keyboard, UI tree), see README.md
for manual testing instructions.
"""

import asyncio
import json
import os
import sys
import pytest

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_tools_module_loads():
    """Test that importing tools package registers all tools."""
    from reasonix_computer_use import tools  # noqa: F401

    from reasonix_computer_use.mcp_server import TOOLS

    expected_tools = [
        "computer_screenshot",
        "computer_window_list",
        "computer_window_activate",
        "computer_mouse_move",
        "computer_mouse_click",
        "computer_mouse_scroll",
        "computer_keyboard_press",
        "computer_keyboard_type",
        "computer_ui_tree",
        "computer_find_element",
        "computer_app_list",
        "computer_app_launch",
    ]

    for tool_name in expected_tools:
        assert tool_name in TOOLS, f"Tool not registered: {tool_name}"


def test_tool_schemas_valid_json():
    """Test that all tool schemas are valid JSON."""
    from reasonix_computer_use.mcp_server import TOOLS

    for tool_name, tool in TOOLS.items():
        schema = tool["inputSchema"]
        if isinstance(schema, str):
            parsed = json.loads(schema)
        else:
            parsed = schema

        assert isinstance(parsed, dict), f"{tool_name}: schema should be object"
        assert "type" in parsed, f"{tool_name}: schema missing 'type'"
        assert "properties" in parsed, f"{tool_name}: schema missing 'properties'"


def test_tool_descriptions_present():
    """Test that all tools have descriptions."""
    from reasonix_computer_use.mcp_server import TOOLS

    for tool_name, tool in TOOLS.items():
        assert tool.get("description"), f"{tool_name}: missing description"
        assert len(tool["description"]) > 20, f"{tool_name}: description too short"


def test_tools_have_handlers():
    """Test that all tools have async handlers."""
    from reasonix_computer_use.mcp_server import TOOLS

    for tool_name, tool in TOOLS.items():
        handler = tool["handler"]
        assert callable(handler), f"{tool_name}: handler not callable"
        assert asyncio.iscoroutinefunction(handler), \
            f"{tool_name}: handler should be async"


@pytest.mark.asyncio
async def test_mcp_server_handle_initialize():
    """Test MCP initialize request handling."""
    from reasonix_computer_use.mcp_server import handle_initialize

    result = await handle_initialize(request_id=1)

    assert result["jsonrpc"] == "2.0"
    assert result["id"] == 1
    assert "result" in result
    assert result["result"]["protocolVersion"] == "2024-11-05"
    assert result["result"]["serverInfo"]["name"] == "reasonix-computer-use"
    assert "tools" in result["result"]["capabilities"]


@pytest.mark.asyncio
async def test_mcp_server_handle_tools_list():
    """Test MCP tools/list request handling."""
    # Import tools to trigger registration of all tools
    from reasonix_computer_use import tools  # noqa: F401

    from reasonix_computer_use.mcp_server import handle_tools_list

    result = await handle_tools_list(request_id=2)

    assert result["jsonrpc"] == "2.0"
    assert result["id"] == 2
    assert "tools" in result["result"]
    assert isinstance(result["result"]["tools"], list)
    assert len(result["result"]["tools"]) > 0

    tool = result["result"]["tools"][0]
    assert "name" in tool
    assert "description" in tool
    assert "inputSchema" in tool


@pytest.mark.asyncio
async def test_mcp_server_handle_tools_call_unknown():
    """Test MCP tools/call with unknown tool returns error."""
    from reasonix_computer_use.mcp_server import handle_tools_call

    result = await handle_tools_call(
        request_id=3,
        params={"name": "nonexistent_tool", "arguments": {}}
    )

    assert result["jsonrpc"] == "2.0"
    assert result["id"] == 3
    assert "error" in result
    assert result["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_mcp_server_handle_tools_call_screenshot():
    """Test MCP tools/call with computer_screenshot."""
    # Import tools to trigger registration
    from reasonix_computer_use import tools  # noqa: F401

    from reasonix_computer_use.mcp_server import handle_tools_call

    result = await handle_tools_call(
        request_id=4,
        params={"name": "computer_screenshot", "arguments": {"mode": "full"}}
    )

    assert result["jsonrpc"] == "2.0"
    assert result["id"] == 4
    assert "result" in result or "error" in result


def test_parse_result_function():
    """Test parse_result helper returns JSON strings."""
    from reasonix_computer_use.utils import parse_result

    result = parse_result({"status": "ok"})
    assert json.loads(result) == {"status": "ok"}

    result = parse_result("already string")
    assert result == "already string"

    result = parse_result([1, 2, 3])
    assert json.loads(result) == [1, 2, 3]


def test_register_tool_decorator():
    """Test that register_tool decorator registers tools correctly."""
    from reasonix_computer_use.mcp_server import TOOLS, register_tool

    @register_tool(
        name="test_tool_xyz_unique",
        description="A test tool",
        schema={"type": "object", "properties": {}}
    )
    async def test_handler(args):
        return "test result"

    assert "test_tool_xyz_unique" in TOOLS
    assert TOOLS["test_tool_xyz_unique"]["name"] == "test_tool_xyz_unique"
    assert TOOLS["test_tool_xyz_unique"]["description"] == "A test tool"
    assert TOOLS["test_tool_xyz_unique"]["handler"] is test_handler


@pytest.mark.asyncio
async def test_app_list_returns_valid_json():
    """Test computer_app_list returns valid JSON."""
    from reasonix_computer_use.app_discover import computer_app_list

    result = await computer_app_list({"refresh": True})

    parsed = json.loads(result)
    assert "status" in parsed or "error" in parsed


@pytest.mark.asyncio
async def test_app_list_search():
    """Test computer_app_list search functionality."""
    from reasonix_computer_use.app_discover import computer_app_list

    # First scan and cache
    await computer_app_list({"refresh": True})

    # Then search
    result = await computer_app_list({"search": "Microsoft"})
    parsed = json.loads(result)
    assert "status" in parsed
    assert "matches" in parsed


def test_cache_read_write():
    """Test _write_cache and _read_cache functions."""
    from reasonix_computer_use.app_discover import _write_cache, _read_cache, _get_cache_path
    import tempfile
    import os

    # Set temp memory dir
    original_dir = os.environ.get("REASONIX_MEMORY_DIR")
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["REASONIX_MEMORY_DIR"] = tmpdir

        try:
            # Write cache
            test_content = "Test application list content"
            cache_path = _write_cache(test_content)
            assert os.path.exists(cache_path)

            # Read cache
            read_content = _read_cache()
            assert read_content is not None
            assert test_content in read_content
        finally:
            if original_dir is not None:
                os.environ["REASONIX_MEMORY_DIR"] = original_dir
            elif "REASONIX_MEMORY_DIR" in os.environ:
                del os.environ["REASONIX_MEMORY_DIR"]


@pytest.mark.asyncio
async def test_window_list_returns_valid_json():
    """Test computer_window_list returns valid JSON."""
    from reasonix_computer_use.screenshot import computer_window_list

    result = await computer_window_list({"visible_only": True})

    parsed = json.loads(result)
    assert "status" in parsed or "error" in parsed


def test_find_window_by_title_returns_valid():
    """Test _find_window_by_title returns valid result."""
    from reasonix_computer_use.screenshot import _find_window_by_title

    result = _find_window_by_title("definitely not a real window title 12345")
    assert result is None or isinstance(result, int)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
