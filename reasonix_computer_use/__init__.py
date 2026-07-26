"""Reasonix Computer Use — cross-platform desktop automation plugin.

Architecture:
  protocol/   — Normalized coordinate protocol (CLAUDE_1024, GEMINI_1000, PIXEL, ELEMENT_REF)
  platform/   — OS abstraction layer (Windows/macOS/Linux PlatformProvider)
  perception/ — Precision-first, vision-fallback observation pipeline
  services/   — System profiling, hooks, tracing
  tools/      — MCP tool implementations (screen_interactor, computer_system, web_navigator)
"""

__version__ = "0.8.0-beta.3"
