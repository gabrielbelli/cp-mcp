from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from . import __version__
from .tools import register_basket_tools, register_scraping_tools

mcp = FastMCP("cp-mcp")

register_scraping_tools(mcp)
register_basket_tools(mcp)


@mcp.tool()
def ping() -> dict:
    """Liveness check. Returns server name, version, and runtime config."""
    return {
        "server": "cp-mcp",
        "version": __version__,
        "user_agent": os.getenv("CP_USER_AGENT", "cp-mcp/0.1"),
        "rate_limit_rps": float(os.getenv("CP_RATE_LIMIT_RPS", "1.0")),
        "cache_path": os.getenv("CP_CACHE_PATH", "data/cache.sqlite"),
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
