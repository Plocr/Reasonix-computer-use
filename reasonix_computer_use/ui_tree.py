"""
Backward-compatibility shim for ui_tree → perception.precision.windows_uia.

This module exists so legacy code (domain_tools.py, screenshot.py,
capability_runner.py) that imports from reasonix_computer_use.ui_tree
continues to work during the transition to the new perception architecture.
"""

from __future__ import annotations

from .perception.precision.windows_uia import (
    WindowsUIAPrecision,
    _UIA_AVAILABLE as UIA_AVAILABLE,
)

_compat_instance = None


def _get_instance():
    global _compat_instance
    if _compat_instance is None:
        _compat_instance = WindowsUIAPrecision()
    return _compat_instance


def observe(window_id=None, scope="interactive", max_elements=80):
    """Legacy compatibility wrapper."""
    ui = _get_instance()
    snapshot = ui.observe(window_id=window_id, max_elements=max_elements)
    elements = []
    for el in snapshot.elements:
        elements.append({
            "ref": el.id,
            "role": el.role,
            "name": el.text,
            "rect": list(el.bbox),
            "actions": ["click"],
        })
    return {
        "status": "ok",
        "revision": f"w{int(window_id or 0):x}-r{snapshot.revision}",
        "window": {"hwnd": hex(int(window_id or 0)), "title": ""},
        "elements": elements,
    }


async def computer_act(args: dict) -> str:
    """Legacy compatibility wrapper."""
    return '{"status":"ok","note":"legacy shim — use screen_interactor instead"}'


_visual_tokens: dict = {}


def consume_visual_fallback(token, window_id):
    """Legacy compatibility wrapper."""
    if token and token in _visual_tokens:
        return True, ""
    return False, "token not found"
