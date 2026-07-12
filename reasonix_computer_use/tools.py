"""Register only the four Reasonix-facing domain tools."""

# Import legacy modules as internal capabilities, then remove their public
# registrations. domain_tools is imported last and owns the public API.
from . import keyboard, mouse, screenshot, text_vision, ui_tree, utils, windows  # noqa: F401
from .mcp_server import TOOLS

TOOLS.clear()

from . import domain_tools  # noqa: E402,F401

__all__ = ["domain_tools"]
