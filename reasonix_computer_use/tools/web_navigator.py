"""
web_navigator — unified web automation channel.

Routes web scenarios through MCP-connected browser automation tools:
  - Default: Playwright MCP (isolated instance, based on Accessibility Tree)
  - Optional: mcp-chrome (reuses user browser login state for intranet)

This tool is a thin proxy — it detects web navigation intents and advises
the host Agent to route through the appropriate MCP server.  The actual
browser interaction is performed by the connected MCP tools.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class WebNavigator:
    """Web automation proxy.

    This tool does NOT directly control a browser. Instead, it:
      1. Validates that the target URL is well-formed.
      2. Advises the host which MCP server to use (playwright or chrome).
      3. Returns the suggested MCP tool call structure.

    The host Agent then calls the recommended MCP tools directly.
    """

    # Known MCP servers for web automation
    PLAYWRIGHT_SERVER = "playwright"
    CHROME_SERVER = "mcp-chrome"

    def __init__(self, prefer_server: str = "playwright"):
        self._prefer = prefer_server

    async def navigate(
        self,
        url: str,
        use_chrome: bool = False,
        isolated: bool = True,
    ) -> dict:
        """Advise navigation to a URL through browser MCP.

        Args:
            url: The target URL.
            use_chrome: Use mcp-chrome (reuse login state) instead of Playwright.
            isolated: For Playwright, use an isolated browser context.

        Returns:
            Suggested MCP tool call for the host Agent.
        """
        # Basic URL validation
        if not url.startswith(("http://", "https://")):
            return {
                "status": "error",
                "code": "invalid_url",
                "message": "URL must start with http:// or https://",
            }

        server = self.CHROME_SERVER if use_chrome else self.PLAYWRIGHT_SERVER
        tool = f"mcp__{server}__navigate_page" if server == "playwright" else f"mcp__{server}__navigate"

        return {
            "status": "ok",
            "action": "navigate",
            "url": url,
            "mcp_server": server,
            "mcp_tool": tool,
            "isolated_context": isolated and server == "playwright",
            "hint": (
                f"Use {tool} to navigate to {url}. "
                f"{'Isolated browser context will be used.' if isolated else ''}"
            ),
        }

    async def get_snapshot(
        self,
        server: str = "playwright",
        verbose: bool = False,
    ) -> dict:
        """Advise getting an accessibility tree snapshot of the current page.

        Args:
            server: Which MCP server to use ('playwright' or 'mcp-chrome').
            verbose: Include all a11y tree nodes.

        Returns:
            Suggested MCP tool call.
        """
        srv = server if server in (self.PLAYWRIGHT_SERVER, self.CHROME_SERVER) else self.PLAYWRIGHT_SERVER
        tool = f"mcp__{srv}__take_snapshot"

        return {
            "status": "ok",
            "action": "snapshot",
            "mcp_server": srv,
            "mcp_tool": tool,
            "verbose": verbose,
            "hint": f"Use {tool} with verbose={str(verbose).lower()} to capture the page structure.",
        }

    async def execute_action(
        self,
        action_type: str,
        server: str = "playwright",
        **kwargs,
    ) -> dict:
        """Advise executing a browser action.

        Args:
            action_type: One of 'click', 'fill', 'press_key', 'type', 'scroll'.
            server: MCP server to target.
            **kwargs: Action-specific parameters (uid, value, key, etc.)

        Returns:
            Suggested MCP tool call.
        """
        srv = server if server in (self.PLAYWRIGHT_SERVER, self.CHROME_SERVER) else self.PLAYWRIGHT_SERVER

        tool_map = {
            "click": f"mcp__{srv}__click",
            "fill": f"mcp__{srv}__fill",
            "type": f"mcp__{srv}__type_text",
            "press_key": f"mcp__{srv}__press_key",
            "hover": f"mcp__{srv}__hover",
            "scroll": "",  # Scroll is done via keyboard or wheel, varies by server
        }

        tool = tool_map.get(action_type, "")
        if not tool:
            return {
                "status": "error",
                "code": "unknown_action",
                "message": f"Unknown action type: {action_type}",
            }

        return {
            "status": "ok",
            "action": action_type,
            "mcp_server": srv,
            "mcp_tool": tool,
            "params": kwargs,
            "hint": f"Use {tool} with the provided parameters.",
        }
