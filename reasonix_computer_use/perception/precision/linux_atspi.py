"""
Linux AT-SPI2 precision provider — observes the accessibility tree over
D-Bus (pygobject / gi.repository.Atspi) and emits the same structured
ScreenSnapshot contract as the Windows UIA provider: stable element ids,
UIA-style roles, physical-pixel bounding boxes.

Wayland sessions are excluded (global coordinates are unreliable there);
the perception router falls back to the vision layer instead.

AT-SPI2 D-Bus is synchronous for property reads, so observe() does not
need an event loop; a failed property read on one node is skipped without
aborting the whole walk.
"""

from __future__ import annotations

import itertools
import os
import sys
from typing import Dict, List, Optional

from ..base import PerceptionProvider
from ...protocol import ElementRef, ScreenSnapshot


def _is_wayland() -> bool:
    """Detect if running under a Wayland session."""
    return os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"


def _atspi_available() -> bool:
    """True when PyGObject with the AT-SPI2 bindings is importable."""
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi  # noqa: F401
        return True
    except Exception:
        return False


# AT-SPI2 role name → UIA-style role (kept aligned with windows_uia's
# INTERACTIVE_ROLES so both backends emit the same vocabulary).
ATSPI_ROLE_MAP: Dict[str, str] = {
    "push button": "Button",
    "check box": "CheckBox",
    "combo box": "ComboBox",
    "text": "Edit",
    "entry": "Edit",
    "link": "Hyperlink",
    "list item": "ListItem",
    "menu item": "MenuItem",
    "radio button": "RadioButton",
    "scroll bar": "ScrollBar",
    "slider": "Slider",
    "spin button": "Spinner",
    "page tab": "TabItem",
    "tree item": "TreeItem",
    "table cell": "DataItem",
    "document": "Document",
}

# AT-SPI2 frame-like roles that represent a top-level window.
_WINDOW_ROLES = {"frame", "window", "dialog"}

_MAX_WALK_DEPTH = 30


class LinuxATSPI2Precision(PerceptionProvider):
    """Linux precision layer via AT-SPI2 (D-Bus accessibility)."""

    def __init__(self):
        self._ref_counter = itertools.count(1)
        self._revision_counter: Dict[str, int] = {}
        self._atspi_initialized = False

    @property
    def source(self) -> str:
        return "precision"

    @property
    def available(self) -> bool:
        """Available on Linux with X11 and PyGObject AT-SPI2 bindings.

        Wayland sessions are excluded because global coordinates are
        unreliable there; the router falls back to the vision layer.
        """
        if sys.platform != "linux":
            return False
        if _is_wayland():
            return False
        return _atspi_available()

    # ── AT-SPI bootstrap ─────────────────────────────────────────────────

    def _ensure_atspi(self):
        """Import and initialize the Atspi bindings once."""
        if not self._atspi_initialized:
            import gi
            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi
            Atspi.init()
            self._atspi_initialized = True
            self._atspi = Atspi
        return self._atspi

    # ── Window resolution ────────────────────────────────────────────────

    def _resolve_root_window(self, window_id: Optional[str]):
        """Return the AT-SPI node to walk, or None.

        window_id=None      → the currently ACTIVE frame
        window_id="app:<pid>" → first frame of the app with that PID
        window_id=<int>     → first frame of the desktop child at index
        """
        Atspi = self._ensure_atspi()
        try:
            desktop = Atspi.get_desktop(0)
        except Exception:
            return None
        if window_id is None:
            return self._active_window(desktop)
        if isinstance(window_id, str) and window_id.startswith("app:"):
            try:
                pid = int(window_id[4:])
            except ValueError:
                return None
            return self._window_for_pid(desktop, pid)
        try:
            idx = int(window_id)
            app = desktop.get_child_at_index(idx)
            return self._first_window(app)
        except Exception:
            return None

    def _active_window(self, desktop):
        for i in range(self._child_count(desktop)):
            app = self._child_at(desktop, i)
            if app is None:
                continue
            for j in range(self._child_count(app)):
                win = self._child_at(app, j)
                if win is None:
                    continue
                if self._role_name(win) in _WINDOW_ROLES:
                    try:
                        if win.get_state_set().contains(
                                self._ensure_atspi().StateType.ACTIVE):
                            return win
                    except Exception:
                        pass
        return None

    def _window_for_pid(self, desktop, pid: int):
        for i in range(self._child_count(desktop)):
            app = self._child_at(desktop, i)
            if app is None:
                continue
            try:
                if app.get_process_id() == pid:
                    return self._first_window(app)
            except Exception:
                continue
        return None

    def _first_window(self, app):
        for j in range(self._child_count(app)):
            win = self._child_at(app, j)
            if win is None:
                continue
            if self._role_name(win) in _WINDOW_ROLES:
                return win
        return None

    # ── Observation ──────────────────────────────────────────────────────

    def observe(
        self,
        window_id: Optional[str] = None,
        max_elements: int = 80,
    ) -> ScreenSnapshot:
        if not self.available:
            raise RuntimeError(
                "AT-SPI2 not available: need Linux + X11 + PyGObject "
                "(python3-gi, gir1.2-atspi-2.0)")
        root = self._resolve_root_window(window_id)
        window_key = str(window_id or "active")
        if root is None:
            return ScreenSnapshot(
                revision=0, window_id=window_key, source="precision",
                elements=[])

        elements: List[ElementRef] = []
        seen: set = set()
        limit = max(1, min(int(max_elements), 200))

        def walk(acc, depth: int):
            if depth > _MAX_WALK_DEPTH or len(elements) >= limit:
                return
            item = self._props(acc)
            if item is not None:
                dedupe = (item["role"], item["name"], tuple(item["rect"]))
                if dedupe not in seen:
                    seen.add(dedupe)
                    elements.append(ElementRef(
                        id=f"e{next(self._ref_counter)}",
                        text=item["name"],
                        role=item["role"],
                        bbox=(item["rect"][0], item["rect"][1],
                              item["rect"][2], item["rect"][3]),
                    ))
            for i in range(self._child_count(acc)):
                child = self._child_at(acc, i)
                if child is not None:
                    walk(child, depth + 1)

        walk(root, 0)

        self._revision_counter[window_key] = \
            self._revision_counter.get(window_key, 0) + 1
        width, height = self._window_size(root)

        return ScreenSnapshot(
            revision=self._revision_counter[window_key],
            window_id=window_key,
            source="precision",
            elements=elements,
            width=width,
            height=height,
            scale_factor=1.0,  # enriched by the caller with system-index data
        )

    # ── Node helpers (every read is exception-guarded) ───────────────────

    def _props(self, acc) -> Optional[dict]:
        """Extract {name, role, rect} for an interactive, visible node."""
        try:
            role = ATSPI_ROLE_MAP.get(self._role_name(acc))
            if role is None:
                return None
            states = acc.get_state_set()
            if not states.contains(self._ensure_atspi().StateType.ENABLED):
                return None
            if not states.contains(self._ensure_atspi().StateType.SHOWING):
                return None
            x, y, w, h = acc.get_extents(
                self._ensure_atspi().CoordType.SCREEN)
            if w <= 0 or h <= 0:
                return None
            return {
                "name": acc.get_name() or "",
                "role": role,
                "rect": [int(x), int(y), int(x) + int(w), int(y) + int(h)],
            }
        except Exception:
            return None

    def _role_name(self, acc) -> str:
        try:
            return acc.get_role_name() or ""
        except Exception:
            return ""

    def _child_count(self, acc) -> int:
        try:
            return acc.get_child_count() or 0
        except Exception:
            return 0

    def _child_at(self, acc, index: int):
        try:
            return acc.get_child_at_index(index)
        except Exception:
            return None

    def _window_size(self, root) -> tuple[int, int]:
        try:
            _, _, w, h = root.get_extents(
                self._ensure_atspi().CoordType.SCREEN)
            return int(w), int(h)
        except Exception:
            return 0, 0
