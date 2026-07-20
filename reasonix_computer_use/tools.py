"""Register only the four Reasonix-facing domain tools."""

import importlib

# Core modules required for pure-visual operation.
from . import keyboard, mouse, screenshot, utils, windows  # noqa: F401
from . import platform_backend, window_backend  # noqa: F401

# Legacy modules (UIA/OCR) are optional. Import only if installed; their
# MCP tool registrations are intentionally not loaded in the vision-only build.
try:
    from . import ui_tree  # noqa: F401
except ImportError:
    pass
try:
    from . import text_vision  # noqa: F401
except ImportError:
    pass

from .mcp_server import TOOLS

TOOLS.clear()

from . import domain_tools  # noqa: E402,F401

# A diagnostic module may import domain_tools before the MCP registry bootstrap.
# Reload only in that import-order case so clearing legacy tools cannot leave an
# empty public registry.
if not {"computer_app", "computer_state", "computer_action", "computer_system"}.issubset(TOOLS):
    importlib.reload(domain_tools)

__all__ = ["domain_tools"]
