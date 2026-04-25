"""Smoke test: server module imports and the ping tool is registered."""

from __future__ import annotations


def test_server_imports() -> None:
    from cp_mcp import server

    assert server.mcp is not None


def test_ping_returns_version() -> None:
    from cp_mcp import __version__
    from cp_mcp.server import ping

    result = ping()
    assert result["server"] == "cp-mcp"
    assert result["version"] == __version__
