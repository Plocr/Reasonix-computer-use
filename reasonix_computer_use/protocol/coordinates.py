"""
Normalized coordinate protocol for multi-model communication.

Coordinate spaces:
  - CLAUDE_1024:  0–1023 mapped to 1024×768 viewport
  - GEMINI_1000:  0–999  mapped to 1000×1000 viewport
  - PIXEL:        raw physical pixels (requires resolution context)
  - ELEMENT_REF:  element ID reference (most stable; preferred for text-only LLMs)

Mapping semantics (important):
  - When a foreground window is available, CLAUDE_1024 / GEMINI_1000 map
    to the WINDOW INTERIOR — (0,0) is the window's top-left corner and
    (1023,1023) its bottom-right.  This keeps normalized coordinates
    stable regardless of window position/size.
  - When no window is targeted, they map to the full display.
  - PIXEL coordinates are used verbatim as physical screen pixels.

All conversion uses the physical display dimensions from system-index.json;
scale_factor is carried for host Agent reference only.
"""

from __future__ import annotations

import enum
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple


# ── Coordinate space enumeration ────────────────────────────────────────────

class CoordinateSpace(str, enum.Enum):
    """Normalized coordinate spaces for cross-model communication."""
    CLAUDE_1024 = "CLAUDE_1024"   # 0–1023 → 1024×768
    GEMINI_1000 = "GEMINI_1000"   # 0–999  → 1000×1000
    PIXEL       = "PIXEL"         # raw physical pixels
    ELEMENT_REF = "ELEMENT_REF"   # element ID (no coordinate needed)


# ── Normalized coordinate ───────────────────────────────────────────────────

@dataclass(frozen=True)
class NormalizedCoord:
    """A coordinate in one of the normalized spaces.

    When space is ELEMENT_REF, x and y are ignored and ref MUST be set.
    """
    x: int
    y: int
    space: CoordinateSpace = CoordinateSpace.CLAUDE_1024
    ref: Optional[str] = None  # element ID, required for ELEMENT_REF

    def __post_init__(self):
        if self.space == CoordinateSpace.ELEMENT_REF and not self.ref:
            raise ValueError("ELEMENT_REF space requires a ref (element ID)")
        if self.space == CoordinateSpace.CLAUDE_1024:
            if not (0 <= self.x <= 1023 and 0 <= self.y <= 1023):
                raise ValueError(
                    f"CLAUDE_1024 coords must be 0–1023, got ({self.x}, {self.y}). "
                    "If you meant physical screen pixels, set fallback.space="
                    "\"PIXEL\" (or use element_ref from an observe snapshot).")
        elif self.space == CoordinateSpace.GEMINI_1000:
            if not (0 <= self.x <= 999 and 0 <= self.y <= 999):
                raise ValueError(
                    f"GEMINI_1000 coords must be 0–999, got ({self.x}, {self.y}). "
                    "If you meant physical screen pixels, set fallback.space="
                    "\"PIXEL\" (or use element_ref from an observe snapshot).")

    def to_dict(self) -> dict:
        d = {"x": self.x, "y": self.y, "space": self.space.value}
        if self.ref:
            d["ref"] = self.ref
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "NormalizedCoord":
        space = CoordinateSpace(d.get("space", "CLAUDE_1024"))
        return cls(
            x=d.get("x", 0),
            y=d.get("y", 0),
            space=space,
            ref=d.get("ref"),
        )


# ── Element reference ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class ElementRef:
    """A stable reference to a UI element discovered during perception."""
    id: str                    # unique element ID within a ScreenSnapshot
    text: str = ""             # visible text / name
    role: str = ""             # a11y role (button, textbox, etc.)
    bbox: Tuple[int, int, int, int] = (0, 0, 0, 0)  # physical pixel bbox

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "text": self.text,
            "role": self.role,
            "bbox": list(self.bbox),
        }


# ── Conversion engine ───────────────────────────────────────────────────────

@dataclass
class CoordinateConverter:
    """Converts normalized coordinates to physical pixels.

    Reads scale_factor from system-index.json.  The viewport dimensions
    for each normalized space are used together with the physical display
    resolution to compute the target pixel.

    Usage:
        converter = CoordinateConverter.from_system_index()
        px, py = converter.to_physical(coord, window_rect)
    """

    scale_factor: float = 1.0
    display_width: int = 1920
    display_height: int = 1080

    # Canonical resolutions for each normalized space
    _CANONICAL: dict = field(default_factory=lambda: {
        CoordinateSpace.CLAUDE_1024: (1024, 768),
        CoordinateSpace.GEMINI_1000: (1000, 1000),
    }, init=False, repr=False)

    @classmethod
    def from_system_index(cls, index_path: Optional[Path] = None) -> "CoordinateConverter":
        """Create a converter from the system-index.json file.

        Falls back to 1920×1080 @ 1.0 scale if the file is missing or invalid.
        """
        if index_path is None:
            # Use absolute path via services layer (not CWD-relative)
            try:
                from ...services import index_path as _svc_index
                index_path = _svc_index()
            except ImportError:
                index_path = Path("memory/system-index.json")

        scale = 1.0
        dw, dh = 1920, 1080

        try:
            if index_path.exists():
                data = json.loads(index_path.read_text(encoding="utf-8"))
                displays = data.get("displays", [])
                if displays:
                    primary = displays[0]
                    # Look for the primary display, or use the first one
                    for d in displays:
                        if d.get("primary"):
                            primary = d
                            break
                    dw = primary.get("width", 1920)
                    dh = primary.get("height", 1080)
                    scale = primary.get("scale_factor", 1.0)
        except (json.JSONDecodeError, OSError, KeyError):
            pass

        return cls(scale_factor=scale, display_width=dw, display_height=dh)

    def to_physical(
        self,
        coord: NormalizedCoord,
        window_rect: Optional[Tuple[int, int, int, int]] = None,
    ) -> Tuple[int, int]:
        """Convert a normalized coordinate to physical screen pixels.

        Args:
            coord: The normalized coordinate to convert.
            window_rect: Optional (left, top, right, bottom) in physical pixels.
                         When provided, CLAUDE_1024 and GEMINI_1000 are mapped
                         to the window interior. When omitted, the full display
                         is used.

        Returns:
            (x, y) in physical screen pixels.
        """
        if coord.space == CoordinateSpace.ELEMENT_REF:
            raise ValueError(
                "ELEMENT_REF has no pixel coordinate; resolve the element first"
            )

        if coord.space == CoordinateSpace.PIXEL:
            # PIXEL coordinates are already physical — no scaling needed
            return (int(coord.x), int(coord.y))

        # Determine the target viewport in physical pixels
        if window_rect is not None:
            left, top, right, bottom = window_rect
            # window_rect from DPI-aware APIs is already physical pixels
            vw = right - left
            vh = bottom - top
            if vw <= 0 or vh <= 0:
                # Defensive: an implausible window rect would map every
                # normalized coordinate to a garbage pixel; fall back to
                # the full display instead of producing off-screen clicks.
                vw = self.display_width
                vh = self.display_height
                origin_x = 0
                origin_y = 0
            else:
                origin_x = left
                origin_y = top
        else:
            # display_width/height from system-index.json are already physical
            vw = self.display_width
            vh = self.display_height
            origin_x = 0
            origin_y = 0

        # Map normalized → physical
        canon_w, canon_h = self._CANONICAL[coord.space]
        px = int(origin_x + round(coord.x * vw / canon_w))
        py = int(origin_y + round(coord.y * vh / canon_h))

        return (px, py)

    def from_physical(
        self,
        px: int,
        py: int,
        space: CoordinateSpace,
        window_rect: Optional[Tuple[int, int, int, int]] = None,
    ) -> NormalizedCoord:
        """Convert physical screen pixels to a normalized coordinate.

        This is the inverse of to_physical, used when saving known-good
        coordinates for cross-resolution replay.
        """
        if space == CoordinateSpace.ELEMENT_REF:
            raise ValueError("ELEMENT_REF is not a coordinate space")

        if space == CoordinateSpace.PIXEL:
            # PIXEL is physical — no conversion needed
            return NormalizedCoord(
                x=int(px),
                y=int(py),
                space=CoordinateSpace.PIXEL,
            )

        # Determine viewport (physical pixels — no scale_factor needed)
        if window_rect is not None:
            left, top, right, bottom = window_rect
            vw = right - left
            vh = bottom - top
            if vw <= 0 or vh <= 0:
                vw = self.display_width
                vh = self.display_height
                origin_x = 0
                origin_y = 0
            else:
                origin_x = left
                origin_y = top
        else:
            vw = self.display_width
            vh = self.display_height
            origin_x = 0
            origin_y = 0

        canon_w, canon_h = self._CANONICAL[space]
        nx = max(0, min(canon_w - 1, round((px - origin_x) * canon_w / vw)))
        ny = max(0, min(canon_h - 1, round((py - origin_y) * canon_h / vh)))

        return NormalizedCoord(x=nx, y=ny, space=space)
