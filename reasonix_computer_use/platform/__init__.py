# Reasonix Computer Use — Platform abstraction layer

from .base import PlatformProvider, WindowInfo

def get_platform() -> PlatformProvider:
    """Return the correct PlatformProvider for the current OS."""
    import sys
    if sys.platform == "win32":
        from .windows import WindowsPlatformProvider
        return WindowsPlatformProvider()
    elif sys.platform == "darwin":
        # macOS stub — falls back to WindowsPlatformProvider for dev
        from .windows import WindowsPlatformProvider
        return WindowsPlatformProvider()
    else:
        # Linux stub — falls back to WindowsPlatformProvider for dev
        from .windows import WindowsPlatformProvider
        return WindowsPlatformProvider()

# Re-export for backward compatibility
from .windows import WindowsPlatformProvider

__all__ = [
    "PlatformProvider",
    "WindowInfo",
    "get_platform",
    "WindowsPlatformProvider",
]
