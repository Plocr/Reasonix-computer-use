"""Utility functions for Reasonix Computer Use plugin."""

import json
from typing import Any


def parse_result(result: Any) -> str:
    """Parse tool execution result to JSON string for MCP response.
    
    Args:
        result: Any result value (dict, list, str, etc.)
        
    Returns:
        JSON string representation of the result.
        If result is already a string, returns it as-is.
    """
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False, default=str, separators=(",", ":"))


def tool_error(code: str, message: str, *, retryable: bool = False,
               fallback: str | None = None) -> str:
    result = {"status": "error", "code": code, "message": message,
              "retryable": retryable}
    if fallback:
        result["fallback"] = fallback
    return parse_result(result)


def safe_get(dictionary: dict, key: str, default: Any = None) -> Any:
    """Safely get value from dictionary with type checking."""
    if not isinstance(dictionary, dict):
        return default
    return dictionary.get(key, default)


def truncate_string(s: str, max_length: int = 500, suffix: str = "...") -> str:
    """Truncate string to max_length with suffix indicator."""
    if not isinstance(s, str):
        return str(s)
    if len(s) <= max_length:
        return s
    return s[:max_length - len(suffix)] + suffix
