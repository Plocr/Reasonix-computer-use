"""MCP stdio server entry point for Reasonix computer use plugin.

Run with: python -m reasonix_computer_use
"""

import asyncio
from reasonix_computer_use.mcp_server import main


if __name__ == "__main__":
    asyncio.run(main())
