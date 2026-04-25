from __future__ import annotations

from cp_mcp.basket import (
    BasketItemInput,
    solve_pareto,
    solve_within_stores,
)
from cp_mcp.models import Offer


def _offer(store: str, price: float, oid: int = 0) -> Offer:
    return Offer(
        offer_id=oid,
        store_name=store,
        price_usd=price,
        price_brl=price * 5.0,
        store_url=None,
        whatsapp_phone=None,
        whatsapp_url=None,
    )


def _item(label: str, pid: int, *price_pairs: tuple[str, float]) -> BasketItemInput:
    offers = [_offer(s, p, oid=hash((label, s)) & 0xFFFF) for s, p in price_pairs]
    return BasketItemInput(
        label=label,
        product_id=pid,
        slug=f"slug-{pid}",
        title=label,
        qty=1,
        offers=offers,
    )


def test_pareto_prefers_single_store_when_close() -> None:
    # A and B both carry both items; C is cheaper on item-2 only by $1.
    items = [
        _item("Phone", 1, ("A", 800), ("B", 810)),
        _item("Laptop", 2, ("A", 1000), ("B", 990), ("C", 989)),
    ]
    result = solve_pareto(items)
    # Frontier sorted by stores_used ascending
    by_k = {opt.stores_used: opt for opt in result.frontier}
    # k=1: must visit one store with both — A or B. Best is A (800+1000=1800) vs B (810+990=1800) tie.
    assert 1 in by_k
    assert by_k[1].total_usd == 1800.0
    # k=2: best is item1@A (800) + item2@C (989) = 1789
    assert 2 in by_k
    assert by_k[2].total_usd == 1789.0
    # Pareto savings reported
    assert by_k[2].extra_savings_vs_prev_step_usd == 11.0
    assert by_k[2].delta_vs_min_total_usd == 0.0
    # Single-store baseline reported
    assert result.single_store_total_usd == 1800.0


def test_pareto_blocked_store_excluded() -> None:
    items = [
        _item("Phone", 1, ("A", 800), ("B", 810)),
        _item("Laptop", 2, ("A", 1000), ("B", 990), ("C", 989)),
    ]
    result = solve_pareto(items, blocked_stores=["C"])
    # Without C, the 2-store optimum collapses to A+B = 800+990 = 1790
    assert result.min_total_usd == 1790.0


def test_pareto_preferred_store_breaks_ties() -> None:
    # Items where A and B tie exactly. Prefer B should route there.
    items = [
        _item("Phone", 1, ("A", 800), ("B", 800)),
        _item("Laptop", 2, ("A", 1000), ("B", 1000)),
    ]
    result = solve_pareto(items, preferred_stores=["B"])
    chosen = result.frontier[0].solution
    # All visits should be at B
    assert {v.store_name for v in chosen.visits} == {"B"}


def test_pareto_max_stores_soft_cap_with_note() -> None:
    # 3 items, cheapest spread across 3 different stores; cap at 1.
    items = [
        _item("A", 1, ("X", 100), ("Y", 200), ("Z", 300)),
        _item("B", 2, ("X", 200), ("Y", 100), ("Z", 300)),
        _item("C", 3, ("X", 300), ("Y", 200), ("Z", 100)),
    ]
    # Unconstrained: 100+100+100 = 300 across 3 stores
    # 1-store best: X total 600 (100+200+300), Y total 500, Z total 700.
    result = solve_pareto(items, max_stores=1)
    assert len(result.frontier) == 1
    assert result.frontier[0].stores_used == 1
    assert result.frontier[0].total_usd == 500.0  # all at Y
    assert result.note is not None
    assert "savings on the table" in result.note.lower()


def test_within_stores_reports_missing() -> None:
    items = [
        _item("A", 1, ("X", 100), ("Y", 200)),
        _item("B", 2, ("Z", 50)),  # only at Z
    ]
    sol = solve_within_stores(items, allowed_stores=["X", "Y"])
    assert not sol.feasible
    assert "B" in sol.missing_items
    # A still gets assigned; total reflects A only
    assert sol.total_usd == 100.0


def test_within_stores_uses_preferred_on_tie() -> None:
    items = [_item("A", 1, ("X", 100), ("Y", 100))]
    sol = solve_within_stores(items, allowed_stores=["X", "Y"], preferred_stores=["Y"])
    assert sol.visits[0].store_name == "Y"


def test_pareto_consolidates_on_tie() -> None:
    # Item-2 ties between A and B. Item-1 only at A. Tie-break should keep item-2 at A
    # to avoid an extra store visit.
    items = [
        _item("Phone", 1, ("A", 800)),
        _item("Laptop", 2, ("A", 1000), ("B", 1000)),
    ]
    result = solve_pareto(items)
    # The k=1 solution must use only A
    by_k = {opt.stores_used: opt for opt in result.frontier}
    assert 1 in by_k
    assert {v.store_name for v in by_k[1].solution.visits} == {"A"}
    assert by_k[1].total_usd == 1800.0
