from __future__ import annotations

from pathlib import Path

from cp_mcp.compare import compare, offers_by_store, summarise_history, watch
from cp_mcp.parsers import parse_product_html

FIXTURES = Path(__file__).parent / "fixtures"


def _product():
    return parse_product_html(
        (FIXTURES / "product_macbook_air_m4.html").read_text(),
        product_id=59619,
        slug="notebook-apple-macbook-air-2025-apple-m4-memoria-16gb-ssd-512gb-136",
        url="/notebook-apple-macbook-air-2025-apple-m4-memoria-16gb-ssd-512gb-136_59619/",
    )


def test_offers_by_store_dedupes_and_sorts() -> None:
    p = _product()
    rows = offers_by_store(p.offers)
    assert rows
    # Each store appears once
    names = [r.store_name for r in rows]
    assert len(names) == len(set(names))
    # Sorted ascending by USD
    prices = [r.price_usd for r in rows]
    assert prices == sorted(prices)
    # Cheapest row has zero delta
    assert rows[0].delta_vs_cheapest_usd == 0.0
    # Subsequent rows have non-negative deltas
    assert all(r.delta_vs_cheapest_usd >= 0 for r in rows)


def test_offers_by_store_counts_skus() -> None:
    p = _product()
    rows = offers_by_store(p.offers)
    # Shopping China shows up multiple times in the fixture; their count must be > 1.
    sc = next((r for r in rows if r.store_name == "Shopping China"), None)
    assert sc is not None
    assert sc.skus_at_store >= 2


def test_history_summary() -> None:
    p = _product()
    h = summarise_history(p.price_history)
    assert h.months_of_data == len(p.price_history)
    assert h.min_usd is not None and h.max_usd is not None
    assert h.min_usd <= h.max_usd
    assert h.median_usd is not None
    assert h.latest_month == p.price_history[-1].month
    assert h.trend in {"rising", "falling", "flat"}


def test_compare_combines_offers_and_history() -> None:
    p = _product()
    c = compare(p)
    assert c.product_id == 59619
    assert c.cheapest_usd == c.by_store[0].price_usd
    assert c.cheapest_store == c.by_store[0].store_name
    assert c.history.months_of_data > 0


def test_watch_target_met() -> None:
    p = _product()
    cheapest = compare(p).cheapest_usd
    assert cheapest is not None
    w_under = watch(p, target_usd=cheapest + 50)
    assert w_under.target_met is True
    w_over = watch(p, target_usd=cheapest - 50)
    assert w_over.target_met is False
    assert w_over.delta_vs_target_usd is not None and w_over.delta_vs_target_usd > 0


def test_watch_band_position() -> None:
    p = _product()
    cheapest = compare(p).cheapest_usd
    assert cheapest is not None
    w = watch(p, target_usd=cheapest)
    assert w.history_min_usd is not None and w.history_max_usd is not None
    assert w.pct_of_band is not None and 0.0 <= w.pct_of_band <= 1.0
