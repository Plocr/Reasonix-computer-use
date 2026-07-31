"""
Screen snapshot — the structured JSON returned by the perception layer.

A ScreenSnapshot contains a list of elements discovered via the precision
layer (UIA / AXAPI / AT-SPI2) or the vision layer (OCR).  Each element has
a stable ID, bounding box, text, and a11y metadata.

The host Agent (VLM/LLM) uses this structured output to decide actions;
the visual layer does NOT make decisions — it only outputs coordinates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .coordinates import ElementRef


@dataclass
class ScreenSnapshot:
    """A point-in-time observation of a window or screen.

    Fields:
        revision:    Monotonic counter so the host can detect staleness.
        window_id:   Platform window handle or ID.
        source:      Which layer produced this snapshot ("precision" or "vision").
        elements:    Discovered UI elements with IDs, bboxes, text, and a11y roles.
        width:       Physical pixel width of the captured area.
        height:      Physical pixel height of the captured area.
        scale_factor: Display scale factor for coordinate conversion.
    """

    revision: int
    window_id: str
    source: str  # "precision" | "vision"
    elements: List[ElementRef] = field(default_factory=list)
    width: int = 0
    height: int = 0
    scale_factor: float = 1.0

    # Optional: when the precision layer detected an input field ready for text
    input_ready: bool = False
    input_typed: bool = False
    input_submitted: bool = False

    # Optional: signal that no further action is possible with current tools
    blocked: bool = False
    blocked_reason: str = ""

    # Optional: local path to a fresh screenshot of the observed area, so the
    # host can verify state that the a11y tree does not expose (e.g. a music
    # player's bottom bar, self-drawn widgets).
    screenshot_path: str = ""

    # Optional: human-readable hint about perception quality (e.g. "UIA
    # element sparse — verify visually via screenshot_path").
    quality_hint: str = ""

    def find_element(self, ref_id: str) -> Optional[ElementRef]:
        """Look up an element by its stable ID."""
        for el in self.elements:
            if el.id == ref_id:
                return el
        return None

    def to_dict(self) -> dict:
        return {
            "revision": self.revision,
            "window_id": self.window_id,
            "source": self.source,
            "elements": [e.to_dict() for e in self.elements],
            "width": self.width,
            "height": self.height,
            "scale_factor": self.scale_factor,
            "input_ready": self.input_ready,
            "input_typed": self.input_typed,
            "input_submitted": self.input_submitted,
            "blocked": self.blocked,
            "blocked_reason": self.blocked_reason,
            "screenshot_path": self.screenshot_path,
            "quality_hint": self.quality_hint,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ScreenSnapshot":
        return cls(
            revision=d.get("revision", 0),
            window_id=d.get("window_id", ""),
            source=d.get("source", "unknown"),
            elements=[ElementRef(**e) if isinstance(e, dict) else e
                       for e in d.get("elements", [])],
            width=d.get("width", 0),
            height=d.get("height", 0),
            scale_factor=d.get("scale_factor", 1.0),
            input_ready=d.get("input_ready", False),
            input_typed=d.get("input_typed", False),
            input_submitted=d.get("input_submitted", False),
            blocked=d.get("blocked", False),
            blocked_reason=d.get("blocked_reason", ""),
            screenshot_path=d.get("screenshot_path", ""),
            quality_hint=d.get("quality_hint", ""),
        )


@dataclass
class ActionCommand:
    """A command issued by the host Agent for execution.

    The host SHOULD provide element_ref when possible, with the
    normalized coordinate as a fallback.  The executor resolves
    ELEMENT_REF against the latest ScreenSnapshot first.
    """

    type: str  # "click_ref", "click_point", "type", "press", "scroll", etc.
    element_ref: Optional[str] = None       # preferred: stable element ID
    fallback: Optional["NormalizedCoord"] = None  # fallback normalized coordinate
    text: str = ""                          # for "type" / "press"
    keys: List[str] = field(default_factory=list)  # for "press" (key combos)
    amount: int = 0                         # for "scroll"
    duration: float = 0.0                   # for "drag" / "wait"
    to_x: Optional[int] = None              # for "drag": destination x (physical px)
    to_y: Optional[int] = None              # for "drag": destination y (physical px)

    def to_dict(self) -> dict:
        d: dict = {"type": self.type}
        if self.element_ref:
            d["element_ref"] = self.element_ref
        if self.fallback:
            d["fallback"] = self.fallback.to_dict()
        if self.text:
            d["text"] = self.text
        if self.keys:
            d["keys"] = self.keys
        if self.amount:
            d["amount"] = self.amount
        if self.duration:
            d["duration"] = self.duration
        if self.to_x is not None:
            d["to_x"] = self.to_x
        if self.to_y is not None:
            d["to_y"] = self.to_y
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ActionCommand":
        from .coordinates import NormalizedCoord

        if not isinstance(d, dict):
            raise ValueError(f"ActionCommand.from_dict expects a dict, got {type(d).__name__}")

        action_type = d.get("type", "")
        if not isinstance(action_type, str) or not action_type:
            raise ValueError("ActionCommand requires a non-empty string 'type' field")

        fallback = None
        if "fallback" in d:
            fallback = NormalizedCoord.from_dict(d["fallback"])

        # Support `key` (singular) as alias for `keys` — wrap string in list.
        # Also accept comma/plus-separated combos like "Control+a" or
        # "ctrl,shift,c" that host agents commonly emit.
        raw_keys = d.get("keys", d.get("key", []))
        if isinstance(raw_keys, str):
            raw_keys = [raw_keys]
        if isinstance(raw_keys, list):
            expanded: list[str] = []
            for item in raw_keys:
                if isinstance(item, str) and ("+" in item or "," in item):
                    for part in item.replace(",", "+").split("+"):
                        part = part.strip()
                        if part:
                            expanded.append(part)
                elif item:
                    expanded.append(str(item))
            raw_keys = expanded

        # Text aliases: `text` (canonical), `keyboard` and `value` are all
        # accepted so agents using fill-style payloads do not silently type
        # nothing (a silent no-op was the root cause of a "success" that
        # never injected anything).
        raw_text = d.get("text", d.get("keyboard", d.get("value", "")))

        return cls(
            type=action_type,
            element_ref=d.get("element_ref"),
            fallback=fallback,
            text=str(raw_text),
            keys=raw_keys if isinstance(raw_keys, list) else [],
            amount=int(d.get("amount", 0)),
            duration=float(d.get("duration", 0.0)),
            to_x=d.get("to_x"),
            to_y=d.get("to_y"),
        )
