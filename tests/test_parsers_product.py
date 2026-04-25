from __future__ import annotations

from pathlib import Path

from cp_mcp.models import Condition
from cp_mcp.parsers import parse_product_html

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text()


def _product():
    return parse_product_html(
        _load("product_macbook_air_m4.html"),
        product_id=59619,
        slug="notebook-apple-macbook-air-2025-apple-m4-memoria-16gb-ssd-512gb-136",
        url="/notebook-apple-macbook-air-2025-apple-m4-memoria-16gb-ssd-512gb-136_59619/",
    )


def test_product_basic_fields() -> None:
    p = _product()
    assert p.product_id == 59619
    assert p.brand == "Apple"
    assert p.category == "Notebook"
    assert p.condition == Condition.NEW
    assert "MacBook Air" in p.title
    assert p.description and "MacBook" in p.description


def test_product_specs_parsed() -> None:
    p = _product()
    spec_map = {s.key: s.value for s in p.specifications}
    assert spec_map["Marca"] == "Apple"
    assert spec_map["Memória RAM"] == "16GB"
    assert spec_map["Armazenamento"] == "SSD 512GB"


def test_product_price_history() -> None:
    p = _product()
    assert len(p.price_history) >= 12
    assert all(point.price_usd > 0 for point in p.price_history)
    # First and last entries match what's hard-coded in the fixture.
    assert p.price_history[0].month == "04/2025"
    assert p.price_history[0].price_usd == 1270.0


def test_product_offers_parsed() -> None:
    p = _product()
    assert len(p.offers) >= 25
    # Sorted by price for predictable assertions
    by_price = sorted(p.offers, key=lambda o: o.price_usd)
    cheapest = by_price[0]
    assert cheapest.price_usd > 0
    assert cheapest.price_brl is not None and cheapest.price_brl > 0
    assert cheapest.store_name
    # Some offers should expose WhatsApp + an outbound store URL.
    assert any(o.whatsapp_phone for o in p.offers)
    assert any(o.store_url and o.store_url.startswith("http") for o in p.offers)
