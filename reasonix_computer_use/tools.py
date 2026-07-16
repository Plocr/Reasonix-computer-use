"""Register only the four Reasonix-facing domain tools."""

import importlib

# Import legacy modules as internal capabilities, then remove their public
# registrations. domain_tools is imported last and owns the public API.
from . import keyboard, mouse, screenshot, text_vision, ui_tree, utils, windows  # noqa: F401
from .mcp_server import TOOLS

TOOLS.clear()

from . import domain_tools  # noqa: E402,F401

# A diagnostic module may import domain_tools before the MCP registry bootstrap.
# Reload only in that import-order case so clearing legacy tools cannot leave an
# empty public registry.
if not {"computer_app", "computer_state", "computer_action", "computer_system"}.issubset(TOOLS):
    importlib.reload(domain_tools)

__all__ = ["domain_tools"]
