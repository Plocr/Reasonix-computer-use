"""
macOS AXAPI precision provider — observes the Accessibility tree via
PyObjC (ApplicationServices) and emits the same structured ScreenSnapshot
contract as the Windows UIA provider: stable element ids, UIA-style roles,
physical-pixel bounding boxes.

Requires the Accessibility permission (AXIsProcessTrusted); when it is
missing, observe() raises a clear OSError and the perception router falls
back to the vision layer.
"""

from __future__ import annotations

import itertools
import sys
from typing import Dict, List, Optional

from ..base import PerceptionProvider
from ...protocol import ElementRef, ScreenSnapshot


def _pyobjc_available() -> bool:
    try:
        import ApplicationServices  # noqa: F401
        return True
    except ImportError:
        return False


# AX role → UIA-style role (kept aligned with windows_uia's
# INTERACTIVE_ROLES so all backends emit the same vocabulary).
AX_ROLE_MAP: Dict[str, str] = {
    "AXButton": "Button",
    "AXCheckBox": "CheckBox",
    "AXComboBox": "ComboBox",
    "AXPopUpButton": "ComboBox",
    "AXTextField": "Edit",
    "AXTextArea": "Edit",
    "AXSearchField": "Edit",
    "AXLink": "Hyperlink",
    "AXMenuItem": "MenuItem",
    "AXRadioButton": "RadioButton",
    "AXSlider": "Slider",
    "AXScrollBar": "ScrollBar",
    "AXIncrementor": "Spinner",
    "AXRow": "ListItem",
    "AXCell": "DataItem",
    "AXWebArea": "Document",
}

_MAX_WALK_DEPTH = 30

# AXError codes
_KAX_ERROR_APIDISABLED = -25211


class MacOSAXAPIPrecision(PerceptionProvider):
    """macOS precision layer via the Accessibility API (AXUIElement)."""

    def __init__(self):
        self._ref_counter = itertools.count(1)
        self._revision_counter: Dict[str, int] = {}

    @property
    def source(self) -> str:
        return "precision"

    @property
    def available(self) -> bool:
        """Available on macOS with PyObjC installed (permission checked at
        observe time so the router can fall back to vision)."""
        if sys.platform != "darwin":
            return False
        return _pyobjc_available()

    # ── AX helpers (every read is exception-guarded) ─────────────────────

    def _ax(self):
        import ApplicationServices
        return ApplicationServices

    def _copy(self, element, attribute: str):
        """Copy an AX attribute; returns the value or None."""
        try:
            err, value = self._ax().AXUIElementCopyAttributeValue(
                element, attribute, None)
            if err != 0:
                if err == _KAX_ERROR_APIDISABLED:
                    raise OSError(
                        "Accessibility permission missing. Grant it in "
                        "System Settings > Privacy & Security > Accessibility "
                        "for the Python process running Reasonix.")
                return None
            return value
        except OSError:
            raise
        except Exception:
            return None

    def _point(self, ax_value) -> Optional[tuple[float, float]]:
        """Extract (x, y) from an AXValue CGPoint."""
        try:
            ok, point = self._ax().AXValueGetValue(
                ax_value, self._ax().kAXValueCGPointType, None)
            if ok:
                return (float(point.x), float(point.y))
        except Exception:
            pass
        return None

    def _size(self, ax_value) -> Optional[tuple[float, float]]:
        """Extract (width, height) from an AXValue CGSize."""
        try:
            ok, size = self._ax().AXValueGetValue(
                ax_value, self._ax().kAXValueCGSizeType, None)
            if ok:
                return (float(size.width), float(size.height))
        except Exception:
            pass
        return None

    def _scale_factor(self) -> float:
        try:
            from AppKit import NSScreen
            return float(NSScreen.mainScreen().backingScaleFactor())
        except Exception:
            return 1.0

    def _children(self, element) -> list:
        value = self._copy(element, self._ax().kAXChildrenAttribute)
        return list(value) if value else []

    def _role(self, element) -> str:
        value = self._copy(element, self._ax().kAXRoleAttribute)
        return value if isinstance(value, str) else ""

    def _title(self, element) -> str:
        value = self._copy(element, self._ax().kAXTitleAttribute)
        if isinstance(value, str):
            return value
        value = self._copy(element, self._ax().kAXDescriptionAttribute)
        return value if isinstance(value, str) else ""

    def _enabled(self, element) -> bool:
        value = self._copy(element, self._ax().kAXEnabledAttribute)
        return bool(value) if value is not None else True

    def _rect(self, element):
        """Physical-pixel rect (left, top, right, bottom) or None."""
        pos = self._point(self._copy(element, self._ax().kAXPositionAttribute))
        size = self._size(self._copy(element, self._ax().kAXSizeAttribute))
        if pos is None or size is None:
            return None
        scale = self._scale_factor()
        left = int(pos[0] * scale)
        top = int(pos[1] * scale)
        width = int(size[0] * scale)
        height = int(size[1] * scale)
        if width <= 0 or height <= 0:
            return None
        return (left, top, left + width, top + height)

    # ── Window resolution ────────────────────────────────────────────────

    def _frontmost_pid(self) -> Optional[int]:
        try:
            from AppKit import NSWorkspace
            front = NSWorkspace.sharedWorkspace().frontmostApplication()
            return int(front.processIdentifier()) if front else None
        except Exception:
            return None

    def _windows_for_pid(self, pid: int) -> list:
        try:
            app = self._ax().AXUIElementCreateApplication(pid)
            value = self._copy(app, self._ax().kAXWindowsAttribute)
            return list(value) if value else []
        except Exception:
            return []

    def _focused_window(self, pid: int):
        try:
            app = self._ax().AXUIElementCreateApplication(pid)
            return self._copy(app, self._ax().kAXFocusedWindowAttribute)
        except Exception:
            return None

    def _resolve_root_window(self, window_id: Optional[str]):
        """Return (pid, window_element) to walk, or (None, None)."""
        if window_id is None:
            pid = self._frontmost_pid()
            if pid is None:
                return None, None
            window = self._focused_window(pid)
            if window is None:
                windows = self._windows_for_pid(pid)
                window = windows[0] if windows else None
            return pid, window
        parts = str(window_id).split(":")
        try:
            pid = int(parts[0])
        except ValueError:
            return None, None
        windows = self._windows_for_pid(pid)
        if not windows:
            return None, None
        if len(parts) > 1:
            try:
                return pid, windows[int(parts[1])]
            except (ValueError, IndexError):
                return None, None
        return pid, windows[0]

    # ── Observation ──────────────────────────────────────────────────────

    def observe(
        self,
        window_id: Optional[str] = None,
        max_elements: int = 80,
    ) -> ScreenSnapshot:
        if not self.available:
            raise RuntimeError(
                "AXAPI not available: need macOS + PyObjC "
                "(pip install -e '.[macos]')")
        pid, root = self._resolve_root_window(window_id)
        window_key = str(window_id) if window_id is not None else f"pid:{pid}"
        if root is None:
            return ScreenSnapshot(
                revision=0, window_id=window_key, source="precision",
                elements=[])

        elements: List[ElementRef] = []
        seen: set = set()
        limit = max(1, min(int(max_elements), 200))

        def walk(element, depth: int):
            if depth > _MAX_WALK_DEPTH or len(elements) >= limit:
                return
            role = self._role(element)
            mapped = AX_ROLE_MAP.get(role)
            if mapped is not None and self._enabled(element):
                rect = self._rect(element)
                if rect is not None:
                    title = self._title(element)
                    dedupe = (mapped, title, tuple(rect))
                    if dedupe not in seen:
                        seen.add(dedupe)
                        elements.append(ElementRef(
                            id=f"e{next(self._ref_counter)}",
                            text=title,
                            role=mapped,
                            bbox=rect,
                        ))
            for child in self._children(element):
                walk(child, depth + 1)

        walk(root, 0)

        self._revision_counter[window_key] = \
            self._revision_counter.get(window_key, 0) + 1
        rect = self._rect(root) or (0, 0, 0, 0)

        return ScreenSnapshot(
            revision=self._revision_counter[window_key],
            window_id=window_key,
            source="precision",
            elements=elements,
            width=rect[2] - rect[0],
            height=rect[3] - rect[1],
            scale_factor=1.0,  # enriched by the caller with system-index data
        )
