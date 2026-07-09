"""
Reasonix Computer Use Plugin - Windows GUI automation tools.

This package implements MCP tools for browser-outside GUI operations.
All tools auto-register via decorators in mcp_server.py when this package is imported.
"""

# Import utils first (no registration, just utilities)
from . import utils  # noqa: F401

# Import all tool modules to trigger registration decorators
from . import mouse
from . import keyboard
from . import screenshot
from . import ui_tree
from . import app_discover

__all__ = ["utils", "mouse", "keyboard", "screenshot", "ui_tree", "app_discover"]
