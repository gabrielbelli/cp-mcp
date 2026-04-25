from __future__ import annotations

from pathlib import Path

from cp_mcp.intent import (
    Intent,
    extract_features,
    parse_intent,
    query_strategies,
    rank_candidates,
    score_card,
)
from cp_mcp.parsers import parse_search_html

FIXTURES = Path(__file__).parent / "fixtures"


def _macbook_cards():
    html = (FIXTURES / "search_macbook.html").read_text()
    return parse_search_html(html, query="macbook", page=1).products


def test_parse_intent_macbook_air_slash_form() -> None:
    i = parse_intent("MacBook Air M4 16/512")
    assert i.family == "macbook-air"
    assert i.chip == "m4"
    assert i.ram_gb == 16
    assert i.storage_gb == 512


def test_parse_intent_nvme_tb() -> None:
    i = parse_intent("Samsung 990 Pro 2TB NVMe")
    assert i.brand == "samsung"
    assert i.family == "990-pro"
    assert i.storage_gb == 2048


def test_parse_intent_rtx() -> None:
    i = parse_intent("RTX 4070 Super")
    assert i.family == "rtx"
    assert i.chip == "rtx-4070-super"


def test_extract_features_from_macbook_card() -> None:
    cards = _macbook_cards()
    target = next(
        c for c in cards if c.product_id == 59619
    )  # MacBook Air 2025 M4 16/512 13.6"
    f = extract_features(target)
    assert f.brand == "apple"
    assert f.family == "macbook-air"
    assert f.chip == "m4"
    assert f.ram_gb == 16
    assert f.storage_gb == 512
    assert f.year == 2025
    assert f.screen_inches is not None and abs(f.screen_inches - 13.6) < 0.01


def test_score_card_perfect_match() -> None:
    cards = _macbook_cards()
    target = next(c for c in cards if c.product_id == 59619)
    intent = Intent(
        family="macbook-air", chip="m4", ram_gb=16, storage_gb=512, screen_inches=13.6
    )
    s = score_card(target, intent)
    assert s.score >= 15
    assert "family" in s.matched and "chip" in s.matched
    assert not s.mismatched


def test_rank_candidates_picks_correct_top() -> None:
    cards = _macbook_cards()
    intent = Intent(
        family="macbook-air", chip="m4", ram_gb=16, storage_gb=512, screen_inches=13.6
    )
    ranked = rank_candidates(cards, intent, min_score=5)
    assert ranked, "should have at least one strong candidate"
    assert ranked[0].card.product_id == 59619


def test_query_strategies_orders_broad_to_narrow() -> None:
    intent = parse_intent("MacBook Air M4 16/512")
    qs = query_strategies(intent)
    assert qs[0] == "macbook air"
    # Each subsequent strategy is at least as long as the previous.
    for prev, nxt in zip(qs, qs[1:]):
        assert len(nxt) >= len(prev)
