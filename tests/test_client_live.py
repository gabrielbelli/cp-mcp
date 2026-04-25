"""Opt-in live smoke test. Hits comprasparaguai.com.br over the network.

Skipped by default. Run with: CP_LIVE=1 ./venv/bin/pytest tests/test_client_live.py -q
"""

from __future__ import annotations

import os

import pytest

from cp_mcp.client import CPClient
from cp_mcp.models import SortOrder
from cp_mcp.parsers import parse_search_html

pytestmark = pytest.mark.skipif(
    os.getenv("CP_LIVE") != "1",
    reason="set CP_LIVE=1 to run live network tests",
)


@pytest.mark.asyncio
async def test_live_search_macbook() -> None:
    async with CPClient(cache=None) as client:
        url, body = await client.fetch_search("macbook", page=1, sort=SortOrder.RELEVANCE)
    result = parse_search_html(body, query="macbook", page=1)
    assert result.products, "live search should return at least one product"
    assert result.total_results and result.total_results > 0
