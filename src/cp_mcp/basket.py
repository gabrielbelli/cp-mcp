"""Multi-store basket optimisation.

Given a list of items, each with a list of (store, price) offers, compute the
Pareto frontier of (store_count, total_price) — one solution per store-count from 1
up to "as many as needed". Each frontier point describes which stores to visit
and what to buy at each.

Algorithm
---------
We enumerate k-subsets of "useful" stores (stores carrying at least one item) and,
for each subset, assign each item to the cheapest in-subset offer. Tractable for
realistic inputs (≤ ~6 items, ≤ ~50 useful stores). For larger inputs, switch
this to an ILP via `pulp`; the public API stays the same.

Tie-breaks
----------
- preferred_stores: small price discount in scoring, never zeroes out a real saving
- "stay where you already are": when an item ties between in-subset stores, prefer
  the one with the most items already assigned in this candidate subset
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Iterable, Optional

from pydantic import BaseModel, ConfigDict

from .models import Offer, StoreAddress

# A small epsilon-style discount per match — ranks tied subsets,
# never overrides real price differences.
_PREFERRED_DISCOUNT_USD = 0.005


@dataclass
class _StoreOffer:
    """Internal: one item × one store row."""

    item_idx: int
    store: str
    price_usd: float
    price_brl: Optional[float]
    offer_id: Optional[int]
    store_url: Optional[str]
    whatsapp_url: Optional[str]
    whatsapp_phone: Optional[str]


# ---- Public models --------------------------------------------------------


class BasketItemInput(BaseModel):
    """One item, with its already-fetched per-store offers (cheapest per store)."""

    model_config = ConfigDict(extra="ignore")

    label: str
    product_id: int
    slug: str
    title: str
    qty: int = 1
    image_url: Optional[str] = None
    product_url: Optional[str] = None
    offers: list[Offer]


class AlternativeOffer(BaseModel):
    """One alternative store carrying the same item, for context on the buyer list."""

    model_config = ConfigDict(extra="ignore")

    store_name: str
    price_usd: float
    price_brl: Optional[float] = None
    delta_usd: float = 0.0  # vs. the chosen offer's price


class AssignedItem(BaseModel):
    label: str
    product_id: int
    title: str
    qty: int
    price_usd: float
    price_brl: Optional[float] = None
    offer_id: Optional[int] = None
    image_url: Optional[str] = None
    product_url: Optional[str] = None
    alternatives: list[AlternativeOffer] = []


class StoreVisit(BaseModel):
    store_name: str
    items: list[AssignedItem]
    subtotal_usd: float
    subtotal_brl: Optional[float] = None
    whatsapp_url: Optional[str] = None
    whatsapp_phone: Optional[str] = None
    store_url: Optional[str] = None
    addresses: list[StoreAddress] = []


class BasketSolution(BaseModel):
    """One concrete plan: which stores to visit and what to buy at each."""

    model_config = ConfigDict(extra="ignore")

    stores_used: int
    total_usd: float
    total_brl: Optional[float] = None
    visits: list[StoreVisit]
    feasible: bool = True
    missing_items: list[str] = []


class ParetoOption(BaseModel):
    """One row of the (stores_used, total_usd) frontier."""

    model_config = ConfigDict(extra="ignore")

    stores_used: int
    total_usd: float
    delta_vs_min_total_usd: float = 0.0
    extra_savings_vs_prev_step_usd: Optional[float] = None
    solution: BasketSolution


class BasketResult(BaseModel):
    """optimise_basket output."""

    model_config = ConfigDict(extra="ignore")

    n_items: int
    useful_stores: int
    min_total_usd: Optional[float] = None
    min_total_stores_used: Optional[int] = None
    single_store_total_usd: Optional[float] = None
    single_store_name: Optional[str] = None
    frontier: list[ParetoOption]
    note: Optional[str] = None


# ---- Core solver ----------------------------------------------------------


def _per_store_cheapest(offers: list[Offer]) -> dict[str, Offer]:
    """For one item, pick the cheapest offer at each store."""

    out: dict[str, Offer] = {}
    for o in offers:
        existing = out.get(o.store_name)
        if existing is None or o.price_usd < existing.price_usd:
            out[o.store_name] = o
    return out


def _alternatives_for(
    item: "BasketItemInput", *, chosen_store: str, chosen_price: float, limit: int = 5
) -> list[AlternativeOffer]:
    """Return up to `limit` other stores carrying this item, sorted by price ascending."""

    per_store = _per_store_cheapest(item.offers)
    out: list[AlternativeOffer] = []
    for store, offer in per_store.items():
        if store == chosen_store:
            continue
        out.append(
            AlternativeOffer(
                store_name=store,
                price_usd=offer.price_usd,
                price_brl=offer.price_brl,
                delta_usd=round(offer.price_usd - chosen_price, 2),
            )
        )
    out.sort(key=lambda a: a.price_usd)
    return out[:limit]


def _build_matrix(
    items: list[BasketItemInput],
    blocked: set[str],
    eligible: Optional[set[str]] = None,
) -> tuple[list[dict[str, Offer]], list[str]]:
    """Return per-item {store -> cheapest_offer} and the union store list.

    `eligible`, when provided, restricts the matrix to those stores per item;
    items with NO offer at any eligible store fall back to all their stores
    (otherwise the solve becomes infeasible). Alternatives are always derived
    from each item's full offer list, independent of this restriction.
    """

    matrix: list[dict[str, Offer]] = []
    union: set[str] = set()
    for item in items:
        per = _per_store_cheapest(item.offers)
        for store in list(per):
            if store in blocked:
                del per[store]
        if eligible is not None:
            restricted = {s: o for s, o in per.items() if s in eligible}
            if restricted:  # only restrict when the item still has at least one option
                per = restricted
        matrix.append(per)
        union.update(per.keys())
    return matrix, sorted(union)


def _evaluate_subset(
    items: list[BasketItemInput],
    matrix: list[dict[str, Offer]],
    subset: tuple[str, ...],
    preferred: set[str],
) -> Optional[BasketSolution]:
    """Assign each item to the cheapest in-subset offer; return None if any item is missing."""

    used_counts: dict[str, int] = {s: 0 for s in subset}
    visits_by_store: dict[str, list[AssignedItem]] = {s: [] for s in subset}
    visits_meta: dict[str, Offer] = {}
    total = 0.0

    for idx, item in enumerate(items):
        candidates: list[tuple[float, str, Offer]] = []
        for store in subset:
            offer = matrix[idx].get(store)
            if offer is None:
                continue
            score = offer.price_usd
            if store in preferred:
                score -= _PREFERRED_DISCOUNT_USD
            candidates.append((score, store, offer))
        if not candidates:
            return None
        # Tie-break: preferred discount → already-visited count → store name
        candidates.sort(
            key=lambda c: (c[0], -used_counts[c[1]], c[1])
        )
        _score, store, offer = candidates[0]
        used_counts[store] += 1
        item_total = offer.price_usd * item.qty
        total += item_total
        alts = _alternatives_for(item, chosen_store=store, chosen_price=offer.price_usd, limit=5)
        visits_by_store[store].append(
            AssignedItem(
                label=item.label,
                product_id=item.product_id,
                title=item.title,
                qty=item.qty,
                price_usd=offer.price_usd,
                price_brl=offer.price_brl,
                offer_id=offer.offer_id,
                image_url=item.image_url,
                product_url=item.product_url,
                alternatives=alts,
            )
        )
        visits_meta[store] = offer  # any offer from that store is fine for contact info

    visits: list[StoreVisit] = []
    for store, assigned in visits_by_store.items():
        if not assigned:
            continue
        meta = visits_meta[store]
        subtotal = sum(a.price_usd * a.qty for a in assigned)
        brl_parts = [a.price_brl * a.qty for a in assigned if a.price_brl is not None]
        subtotal_brl = round(sum(brl_parts), 2) if len(brl_parts) == len(assigned) else None
        visits.append(
            StoreVisit(
                store_name=store,
                items=assigned,
                subtotal_usd=round(subtotal, 2),
                subtotal_brl=subtotal_brl,
                whatsapp_url=meta.whatsapp_url,
                whatsapp_phone=meta.whatsapp_phone,
                store_url=meta.store_url,
            )
        )
    visits.sort(key=lambda v: -v.subtotal_usd)

    brl_subtotals = [v.subtotal_brl for v in visits]
    total_brl = round(sum(brl_subtotals), 2) if all(b is not None for b in brl_subtotals) else None

    return BasketSolution(
        stores_used=len(visits),
        total_usd=round(total, 2),
        total_brl=total_brl,
        visits=visits,
        feasible=True,
    )


def _best_at_k(
    items: list[BasketItemInput],
    matrix: list[dict[str, Offer]],
    union: list[str],
    k: int,
    preferred: set[str],
) -> Optional[BasketSolution]:
    """Best feasible solution using at most k distinct stores."""

    best: Optional[BasketSolution] = None
    best_pref_count = -1
    # Pre-filter the union to stores that actually carry at least one item we still need.
    candidates = [
        s for s in union if any(matrix[i].get(s) is not None for i in range(len(items)))
    ]
    # Trivial bound: if k >= len(items), at most len(items) stores are useful
    eff_k = min(k, len(candidates), len(items))
    if eff_k < 1:
        return None
    for subset in combinations(candidates, eff_k):
        sol = _evaluate_subset(items, matrix, subset, preferred)
        if sol is None:
            continue
        pref_count = sum(1 for v in sol.visits if v.store_name in preferred)
        if (
            best is None
            or sol.total_usd < best.total_usd - 1e-6
            or (
                abs(sol.total_usd - best.total_usd) < 1e-6
                and pref_count > best_pref_count
            )
        ):
            best = sol
            best_pref_count = pref_count
    return best


def solve_pareto(
    items: list[BasketItemInput],
    *,
    max_stores: Optional[int] = None,
    blocked_stores: Iterable[str] = (),
    preferred_stores: Iterable[str] = (),
    eligible_stores: Optional[Iterable[str]] = None,
) -> BasketResult:
    """Compute the Pareto frontier of (stores_used, total_usd).

    Returns one solution per store-count from 1..K, where K = min(len(items),
    distinct stores in the unconstrained optimum) — beyond that, adding stores
    can never improve total. If `max_stores` is set as a soft cap, we still
    return the frontier up to that cap; if no feasible solution exists at the
    cap, we surface the smallest feasible store-count solution and flag it.
    """

    blocked = {s for s in blocked_stores}
    preferred = {s for s in preferred_stores}
    eligible = {s for s in eligible_stores} if eligible_stores is not None else None
    matrix, union = _build_matrix(items, blocked, eligible)

    if not items or not union:
        return BasketResult(
            n_items=len(items),
            useful_stores=len(union),
            frontier=[],
            note="No items or no offers available after filtering.",
        )

    # The unconstrained optimum: each item at its cheapest available store.
    item_min_assignment: dict[int, str] = {}
    item_min_offer: dict[int, Offer] = {}
    items_with_no_offer: list[str] = []
    for i, per in enumerate(matrix):
        if not per:
            items_with_no_offer.append(items[i].label)
            continue
        store, offer = min(per.items(), key=lambda kv: kv[1].price_usd)
        item_min_assignment[i] = store
        item_min_offer[i] = offer

    if items_with_no_offer:
        return BasketResult(
            n_items=len(items),
            useful_stores=len(union),
            frontier=[],
            note=f"No offers found (after blocklist) for: {', '.join(items_with_no_offer)}",
        )

    min_total = sum(o.price_usd * items[i].qty for i, o in item_min_offer.items())
    min_total_stores = len(set(item_min_assignment.values()))

    # Single-store baseline: best store that covers all items
    single_store_best: Optional[tuple[str, float]] = None
    for store in union:
        if all(store in matrix[i] for i in range(len(items))):
            total = sum(matrix[i][store].price_usd * items[i].qty for i in range(len(items)))
            if single_store_best is None or total < single_store_best[1]:
                single_store_best = (store, total)

    cap = max_stores if max_stores is not None else min_total_stores
    cap = max(1, cap)

    frontier: list[ParetoOption] = []
    prev_total: Optional[float] = None
    for k in range(1, cap + 1):
        sol = _best_at_k(items, matrix, union, k, preferred)
        if sol is None:
            continue
        delta = round(sol.total_usd - min_total, 2)
        savings = round(prev_total - sol.total_usd, 2) if prev_total is not None else None
        frontier.append(
            ParetoOption(
                stores_used=sol.stores_used,
                total_usd=sol.total_usd,
                delta_vs_min_total_usd=delta,
                extra_savings_vs_prev_step_usd=savings,
                solution=sol,
            )
        )
        prev_total = sol.total_usd
        if abs(delta) < 0.005:  # we've reached the unconstrained optimum
            break

    note: Optional[str] = None
    if max_stores is not None and frontier:
        max_used = max(opt.stores_used for opt in frontier)
        if max_used < min_total_stores and frontier[-1].delta_vs_min_total_usd > 0.005:
            note = (
                f"Soft cap of {max_stores} stores left US$ {frontier[-1].delta_vs_min_total_usd:.2f} "
                f"of savings on the table — the unconstrained optimum needs {min_total_stores} stores."
            )

    return BasketResult(
        n_items=len(items),
        useful_stores=len(union),
        min_total_usd=round(min_total, 2),
        min_total_stores_used=min_total_stores,
        single_store_total_usd=round(single_store_best[1], 2) if single_store_best else None,
        single_store_name=single_store_best[0] if single_store_best else None,
        frontier=frontier,
        note=note,
    )


def solve_within_stores(
    items: list[BasketItemInput],
    allowed_stores: Iterable[str],
    *,
    blocked_stores: Iterable[str] = (),
    preferred_stores: Iterable[str] = (),
    eligible_stores: Optional[Iterable[str]] = None,
) -> BasketSolution:
    """Find the cheapest assignment using ONLY the user-supplied store list.

    Items that are unavailable at every allowed store are reported in
    `missing_items` rather than dropping the whole solution.
    """

    allowed = {s for s in allowed_stores} - {s for s in blocked_stores}
    eligible = {s for s in eligible_stores} if eligible_stores is not None else None
    matrix, _ = _build_matrix(
        items, blocked={s for s in blocked_stores}, eligible=eligible
    )
    preferred = {s for s in preferred_stores}

    visits_by_store: dict[str, list[AssignedItem]] = {s: [] for s in allowed}
    visits_meta: dict[str, Offer] = {}
    used_counts: dict[str, int] = {s: 0 for s in allowed}
    total = 0.0
    missing: list[str] = []

    for idx, item in enumerate(items):
        candidates: list[tuple[float, str, Offer]] = []
        for store in allowed:
            offer = matrix[idx].get(store)
            if offer is None:
                continue
            score = offer.price_usd
            if store in preferred:
                score -= _PREFERRED_DISCOUNT_USD
            candidates.append((score, store, offer))
        if not candidates:
            missing.append(item.label)
            continue
        candidates.sort(key=lambda c: (c[0], -used_counts[c[1]], c[1]))
        _, store, offer = candidates[0]
        used_counts[store] += 1
        total += offer.price_usd * item.qty
        alts = _alternatives_for(item, chosen_store=store, chosen_price=offer.price_usd, limit=5)
        visits_by_store[store].append(
            AssignedItem(
                label=item.label,
                product_id=item.product_id,
                title=item.title,
                qty=item.qty,
                price_usd=offer.price_usd,
                price_brl=offer.price_brl,
                offer_id=offer.offer_id,
                image_url=item.image_url,
                product_url=item.product_url,
                alternatives=alts,
            )
        )
        visits_meta[store] = offer

    visits: list[StoreVisit] = []
    for store, assigned in visits_by_store.items():
        if not assigned:
            continue
        meta = visits_meta[store]
        subtotal = sum(a.price_usd * a.qty for a in assigned)
        brl_parts = [a.price_brl * a.qty for a in assigned if a.price_brl is not None]
        subtotal_brl = round(sum(brl_parts), 2) if len(brl_parts) == len(assigned) else None
        visits.append(
            StoreVisit(
                store_name=store,
                items=assigned,
                subtotal_usd=round(subtotal, 2),
                subtotal_brl=subtotal_brl,
                whatsapp_url=meta.whatsapp_url,
                whatsapp_phone=meta.whatsapp_phone,
                store_url=meta.store_url,
            )
        )
    visits.sort(key=lambda v: -v.subtotal_usd)

    brl_subtotals = [v.subtotal_brl for v in visits]
    total_brl = round(sum(brl_subtotals), 2) if all(b is not None for b in brl_subtotals) else None

    return BasketSolution(
        stores_used=len(visits),
        total_usd=round(total, 2),
        total_brl=total_brl,
        visits=visits,
        feasible=not missing,
        missing_items=missing,
    )
