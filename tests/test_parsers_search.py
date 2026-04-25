from __future__ import annotations

from pathlib import Path

from cp_mcp.models import SortOrder
from cp_mcp.parsers import parse_search_html

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_search_macbook_top_level_fields() -> None:
    result = parse_search_html(_load("search_macbook.html"), query="macbook", page=1)
    assert result.query == "macbook"
    assert result.page == 1
    assert result.total_results == 363
    assert result.total_pages == 19
    assert result.sort == SortOrder.RELEVANCE


def test_search_macbook_returns_20_unique_products() -> None:
    result = parse_search_html(_load("search_macbook.html"), query="macbook", page=1)
    assert len(result.products) == 20
    ids = [p.product_id for p in result.products]
    assert len(set(ids)) == len(ids), "product IDs must be unique on the page"


def test_search_macbook_first_card_shape() -> None:
    result = parse_search_html(_load("search_macbook.html"), query="macbook", page=1)
    first = result.products[0]
    assert first.product_id == 67714
    assert first.slug.startswith("notebook-apple-macbook-neo-2026")
    assert first.url.startswith("https://www.comprasparaguai.com.br/")
    assert first.price_usd_from == 665.0
    assert first.price_brl_from == 3404.8
    assert first.offer_count == 56
    assert first.image_url and "linodeobjects.com" in first.image_url


def test_search_macbook_suggestions() -> None:
    result = parse_search_html(_load("search_macbook.html"), query="macbook", page=1)
    assert any("Notebook" == s.label and s.count == 257 for s in result.suggestions)
    assert all(s.url.startswith("https://www.comprasparaguai.com.br/") for s in result.suggestions)
