"""
Platform-agnostic helpers shared by OS backends.

Timing policy (human-like click rhythm), drag interpolation, key-name
normalization and the FFmpeg recorder lifecycle are identical on every
platform.  Keeping them here gives Windows and Linux one source of truth —
the same behaviour, the same constants, the same failure semantics.

All coordinates in this module are PHYSICAL screen pixels.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple


# ── Human-like input timing (single source of truth) ─────────────────────
# Self-drawn UIs (QQ Music, CEF apps, ...) misread machine-speed double
# clicks (20ms hold / 50ms gap) as two single clicks.  Human double-click:
# ~60-100ms press hold, ~150-300ms gap, well inside the OS double-click
# threshold (GetDoubleClickTime default 500ms on Windows).

PRESS_HOLD = 0.06     # down -> up hold time
CLICK_GAP = 0.20      # interval between clicks of a multi-click
MOVE_SETTLE = 0.03    # pause after moving the cursor before the action
SCROLL_SETTLE = 0.05  # pause after a scroll event
MAX_CLICK_COUNT = 10  # upper bound for a multi-click count
MAX_DURATION = 5.0    # upper bound for any explicit duration (drag/click hold)


def clamp_count(count: int) -> int:
    """Clamp a click count into [1, MAX_CLICK_COUNT]."""
    return max(1, min(int(count), MAX_CLICK_COUNT))


def clamp_duration(duration: float) -> float:
    """Clamp a duration into [0.05, MAX_DURATION]."""
    return max(0.05, min(float(duration), MAX_DURATION))


def drag_plan(from_xy: Tuple[int, int], to_xy: Tuple[int, int],
              duration: float) -> Tuple[List[Tuple[int, int]], float]:
    """Linear interpolation plan for a mouse drag.

    Returns (points, delay): the cursor positions to visit (excluding the
    start point) and the sleep between consecutive steps.  ~40 steps/second
    capped at 60 steps, matching the human-speed drag rhythm.
    """
    from_x, from_y = from_xy
    to_x, to_y = to_xy
    duration = clamp_duration(duration)
    steps = max(2, min(int(duration * 40), 60))
    delay = duration / steps
    points: List[Tuple[int, int]] = []
    for step in range(1, steps + 1):
        cx = from_x + (to_x - from_x) * step // steps
        cy = from_y + (to_y - from_y) * step // steps
        points.append((cx, cy))
    return points, delay


# ── Key-name normalization ────────────────────────────────────────────────
# Modifier aliases are shared; each backend maps the normalized name to its
# own scancode table (VK codes on Windows, keysyms on Linux/X11).

MODIFIER_ALIASES = {
    "control": "ctrl",
    "cmd": "super", "win": "super", "meta": "super", "command": "super",
    "opt": "alt", "altgr": "alt",
}


def normalize_key_name(name: str) -> str:
    """Normalize a modifier alias to its canonical form (case-insensitive)."""
    lowered = name.lower()
    return MODIFIER_ALIASES.get(lowered, lowered)


# ── FFmpeg recorder lifecycle ─────────────────────────────────────────────
# Only the input flags differ per platform (gdigrab on Windows, x11grab on
# Linux); the Popen management, startup probe and teardown are identical.

class FfmpegRecorder:
    """Manage an FFmpeg screen-recording subprocess.

    ``args_builder(rect)`` returns the platform-specific ffmpeg argument
    list (including output path).  ``start`` probes for immediate exit
    (~0.6s grace) so instant failures are reported instead of silently
    pretending to record; ``stop`` terminates with a 5s grace before kill.
    """

    def __init__(self) -> None:
        self._process: Optional[subprocess.Popen] = None
        self._output: Optional[Path] = None

    def start(self, output_path: Path, args_builder: Callable,
              rect: Optional[Tuple[int, int, int, int]] = None) -> bool:
        """Start recording.  Returns True if recording actually started."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self._output = output_path
        args = args_builder(rect)
        try:
            self._process = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # Probe for immediate exit: capture can fail right away (remote
            # session, driver issues) while the process still reports success
            # to us.  A ~0.6s grace period catches most instant failures.
            try:
                time.sleep(0.6)
                if self._process.poll() is not None:
                    self._process = None
                    return False
            except OSError:
                pass
            return True
        except FileNotFoundError:
            # FFmpeg not available — can't record
            return False

    def stop(self) -> Optional[Path]:
        """Stop recording and return the saved file path (or None)."""
        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None
            return self._output
        return None
