"""Comparison helpers — pure functions over parsed Product / Offer / PricePoint.

These are the analytic core for Phase 3 tools (compare_prices, find_best_deal,
watch_price). Kept HTTP-free so they're cheap to unit-test against fixtures.
"""

from __future__ import annotations

from typing import Iterable, Optional

from pydantic import BaseModel, ConfigDict

from .models import Offer, PricePoint, Product


class StoreOffer(BaseModel):
    """One row per store: their cheapest offer for the product."""

    model_config = ConfigDict(extra="ignore")

    store_name: str
    price_usd: float
    price_brl: Optional[float] = None
    delta_vs_cheapest_usd: float = 0.0
    delta_vs_cheapest_pct: float = 0.0
    skus_at_store: int = 1
    offer_id: Optional[int] = None
    store_url: Optional[str] = None
    whatsapp_url: Optional[str] = None
    whatsapp_phone: Optional[str] = None


class HistorySummary(BaseModel):
    months_of_data: int = 0
    min_usd: Optional[float] = None
    min_month: Optional[str] = None
    max_usd: Optional[float] = None
    max_month: Optional[str] = None
    median_usd: Optional[float] = None
    latest_usd: Optional[float] = None
    latest_month: Optional[str] = None
    pct_above_min: Optional[float] = None  # current vs all-time min
    trend: Optional[str] = None  # "rising" | "falling" | "flat"


class Comparison(BaseModel):
    """compare_prices output."""

    model_config = ConfigDict(extra="ignore")

    product_id: int
    title: str
    url: str
    cheapest_usd: Optional[float] = None
    cheapest_store: Optional[str] = None
    by_store: list[StoreOffer]
    history: HistorySummary


class WatchResult(BaseModel):
    """watch_price output."""

    model_config = ConfigDict(extra="ignore")

    target_usd: float
    current_usd: Optional[float]
    target_met: bool
    delta_vs_target_usd: Optional[float] = None
    history_min_usd: Optional[float] = None
    history_max_usd: Optional[float] = None
    pct_of_band: Optional[float] = None  # 0.0 = at min, 1.0 = at max
    note: Optional[str] = None


def offers_by_store(offers: Iterable[Offer]) -> list[StoreOffer]:
    """Return one row per store (cheapest offer per store), sorted by price ascending."""

    by_store: dict[str, tuple[Offer, int]] = {}
    for o in offers:
        existing = by_store.get(o.store_name)
        if existing is None or o.price_usd < existing[0].price_usd:
            by_store[o.store_name] = (o, (existing[1] + 1) if existing else 1)
        else:
            by_store[o.store_name] = (existing[0], existing[1] + 1)

    rows: list[StoreOffer] = []
    for store, (offer, count) in by_store.items():
        rows.append(
            StoreOffer(
                store_name=store,
                price_usd=offer.price_usd,
                price_brl=offer.price_brl,
                skus_at_store=count,
                offer_id=offer.offer_id,
                store_url=offer.store_url,
                whatsapp_url=offer.whatsapp_url,
                whatsapp_phone=offer.whatsapp_phone,
            )
        )

    rows.sort(key=lambda r: r.price_usd)
    if rows:
        cheapest = rows[0].price_usd
        for r in rows:
            r.delta_vs_cheapest_usd = round(r.price_usd - cheapest, 2)
            r.delta_vs_cheapest_pct = round(
                ((r.price_usd - cheapest) / cheapest * 100) if cheapest else 0.0, 2
            )
    return rows


def summarise_history(history: list[PricePoint]) -> HistorySummary:
    """Compute basic stats over the price-history list (oldest-first)."""

    if not history:
        return HistorySummary()
    prices = [p.price_usd for p in history]
    sorted_prices = sorted(prices)
    n = len(prices)
    median = (
        sorted_prices[n // 2]
        if n % 2 == 1
        else (sorted_prices[n // 2 - 1] + sorted_prices[n // 2]) / 2
    )
    min_idx = prices.index(min(prices))
    max_idx = prices.index(max(prices))
    latest = history[-1]

    # Trend: compare last 3 months' average vs prior 3 months'
    trend: Optional[str] = None
    if n >= 6:
        recent = sum(prices[-3:]) / 3
        prior = sum(prices[-6:-3]) / 3
        if recent > prior * 1.02:
            trend = "rising"
        elif recent < prior * 0.98:
            trend = "falling"
        else:
            trend = "flat"

    pct_above_min = (
        round((latest.price_usd - min(prices)) / min(prices) * 100, 2)
        if min(prices) > 0
        else None
    )

    return HistorySummary(
        months_of_data=n,
        min_usd=min(prices),
        min_month=history[min_idx].month,
        max_usd=max(prices),
        max_month=history[max_idx].month,
        median_usd=round(median, 2),
        latest_usd=latest.price_usd,
        latest_month=latest.month,
        pct_above_min=pct_above_min,
        trend=trend,
    )


def compare(product: Product) -> Comparison:
    rows = offers_by_store(product.offers)
    cheapest = rows[0] if rows else None
    return Comparison(
        product_id=product.product_id,
        title=product.title,
        url=product.url,
        cheapest_usd=cheapest.price_usd if cheapest else None,
        cheapest_store=cheapest.store_name if cheapest else None,
        by_store=rows,
        history=summarise_history(product.price_history),
    )


def watch(product: Product, target_usd: float) -> WatchResult:
    rows = offers_by_store(product.offers)
    current = rows[0].price_usd if rows else None
    history = summarise_history(product.price_history)

    target_met = current is not None and current <= target_usd
    delta = round(current - target_usd, 2) if current is not None else None

    pct_of_band: Optional[float] = None
    note: Optional[str] = None
    if (
        history.min_usd is not None
        and history.max_usd is not None
        and history.max_usd > history.min_usd
        and current is not None
    ):
        pct_of_band = round(
            (current - history.min_usd) / (history.max_usd - history.min_usd), 4
        )
        if pct_of_band <= 0.05:
            note = "at or near the 12-month low"
        elif pct_of_band <= 0.25:
            note = "near the bottom of the 12-month band"
        elif pct_of_band >= 0.75:
            note = "near the top of the 12-month band"

    return WatchResult(
        target_usd=target_usd,
        current_usd=current,
        target_met=target_met,
        delta_vs_target_usd=delta,
        history_min_usd=history.min_usd,
        history_max_usd=history.max_usd,
        pct_of_band=pct_of_band,
        note=note,
    )
