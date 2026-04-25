"""Basket-optimisation MCP tools."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from pathlib import Path

from ..basket import (
    BasketItemInput,
    BasketResult,
    BasketSolution,
    solve_pareto,
    solve_within_stores,
)
from ..format import format_basket as _format_basket
from ..models import Offer, StoreAddress
from ..parsers import parse_store_html, slugify
from ..store_index import big_store_names, fetch_directory
from ..intent import Intent, parse_intent, query_strategies, rank_candidates
from ..models import Condition, ProductCard
from ..parsers import parse_product_html, parse_search_html
from .scraping import _get_client


# ---- Item resolution -------------------------------------------------------


async def _resolve_item(item: dict[str, Any]) -> BasketItemInput:
    """Resolve a flexible item spec into a BasketItemInput with offers populated.

    Accepted shapes (all optional unless noted):
        {"product_id": int, "slug": str, "label"?: str, "qty"?: int}
        {"intent_text": "macbook air m4 16/512", "label"?: str, "qty"?: int}
        {"brand": "...", "family": "...", "chip": "...", "ram_gb": int, ...}
    """

    label = item.get("label") or item.get("intent_text") or item.get("title")
    qty = int(item.get("qty") or 1)

    product_id: Optional[int] = item.get("product_id")
    slug: Optional[str] = item.get("slug")

    if product_id is None or not slug:
        # Need to resolve. Build an Intent.
        intent: Intent
        if item.get("intent_text"):
            intent = parse_intent(item["intent_text"])
        else:
            intent = Intent(raw="")
        for f in ("brand", "family", "chip", "ram_gb", "storage_gb", "year", "screen_inches"):
            if item.get(f) is not None:
                setattr(intent, f, item[f])
        if item.get("include_conditions"):
            intent.conditions = [Condition(c.lower()) for c in item["include_conditions"]]
        elif not intent.conditions:
            intent.conditions = [Condition.NEW]

        client = _get_client()
        cards: dict[int, ProductCard] = {}
        winner = None
        for q in query_strategies(intent):
            for pg in (1, 2, 3):
                _, body = await client.fetch_search(query=q, page=pg)
                page = parse_search_html(body, query=q, page=pg)
                for c in page.products:
                    cards.setdefault(c.product_id, c)
                if not page.products:
                    break
                ranked = rank_candidates(cards.values(), intent, min_score=5)
                if ranked:
                    winner = ranked[0]
                    break
            if winner:
                break
        if not winner:
            # Fallback: best near-miss (still requires positive score so we don't
            # accept unrelated products). Caller can override with product_id+slug.
            near = rank_candidates(cards.values(), intent, min_score=1)
            if near:
                winner = near[0]
        if not winner:
            raise ValueError(
                f"Could not resolve item to a product: {item!r}. "
                "Try a more specific query, or pass product_id+slug after using search_products/resolve_query."
            )
        product_id = winner.card.product_id
        slug = winner.card.slug
        label = label or winner.card.title

    # Fetch the product to get its offers.
    client = _get_client()
    url, body = await client.fetch_product(slug, product_id)
    product = parse_product_html(body, product_id=product_id, slug=slug, url=url)

    return BasketItemInput(
        label=label or product.title,
        product_id=product_id,
        slug=slug,
        title=product.title,
        qty=qty,
        image_url=product.image_url,
        product_url=product.url,
        offers=product.offers,
    )


async def _resolve_all(items: list[dict[str, Any]]) -> list[BasketItemInput]:
    return list(await asyncio.gather(*[_resolve_item(it) for it in items]))


async def _fetch_store_addresses(store_name: str) -> list[StoreAddress]:
    """Best-effort lookup of a store's branch addresses on /lojas/<slug>/.

    Returns [] if the slugified name isn't a real page (404). Tolerant by design —
    address enrichment is informational, not load-bearing for the basket.
    """

    slug = slugify(store_name)
    if not slug:
        return []
    client = _get_client()
    try:
        _, body = await client.fetch_store(slug)
    except Exception:
        return []
    try:
        store = parse_store_html(body, slug=slug)
    except Exception:
        return []
    return store.addresses


async def _enrich_addresses(solution: BasketSolution) -> None:
    """Mutate the solution: populate visit.addresses for each unique store, in parallel."""

    unique_stores: list[str] = []
    seen: set[str] = set()
    for visit in solution.visits:
        if visit.store_name not in seen:
            seen.add(visit.store_name)
            unique_stores.append(visit.store_name)
    if not unique_stores:
        return
    results = await asyncio.gather(
        *[_fetch_store_addresses(s) for s in unique_stores], return_exceptions=False
    )
    by_name = dict(zip(unique_stores, results))
    for visit in solution.visits:
        visit.addresses = by_name.get(visit.store_name, [])


# ---- Tools ----------------------------------------------------------------


def register_basket_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    async def optimise_basket(
        items: list[dict],
        max_stores: Optional[int] = None,
        blocked_stores: Optional[list[str]] = None,
        preferred_stores: Optional[list[str]] = None,
        prefer_big_stores: bool = True,
        big_store_min_reviews: int = 1000,
    ) -> dict:
        """Plan a multi-item shopping trip across stores, optimising for total US$.

        Returns the **Pareto frontier**: one row per store-count from 1 up to the
        number of stores in the unconstrained optimum. Each row carries the
        complete plan (which stores, what to buy where, contact info), so the user
        can pick the trade-off they want — a single store at a small premium, or
        multiple stores for the absolute lowest total.

        Args:
            items: list of item specs. Each item may be:
                {"product_id": <int>, "slug": "<str>", "label"?: "<str>", "qty"?: 1}
                {"intent_text": "<str>", "label"?: "<str>", "qty"?: 1}
                {"brand": "...", "family": "...", "chip": "...", "ram_gb": ..., ...}
                Refurbished/CPO products are excluded by default — pass
                "include_conditions": ["new","recondicionado"] per item to allow them.
            max_stores: SOFT cap on stores visited. If a hard cap would block items,
                we still return the best feasible solution and flag the gap.
            blocked_stores: stores never to use.
            preferred_stores: stores to favour on ties (small bias, never overrides
                a real saving).
            prefer_big_stores: when True (default), restrict the chosen offers to
                established stores (≥ big_store_min_reviews on Google). Smaller
                stores with cheaper prices are NOT picked but still surface in
                each item's `alternatives` list, so the user can override
                manually if the saving is worth it. Disable with False to
                optimise purely on price.
            big_store_min_reviews: Google-review threshold defining "big".

        Output:
            BasketResult dict with:
                min_total_usd / min_total_stores_used: the unconstrained optimum
                single_store_total_usd / single_store_name: best single-store plan, if any
                frontier: list of {stores_used, total_usd, delta_vs_min_total_usd,
                          extra_savings_vs_prev_step_usd, solution}
        """

        resolved = await _resolve_all(items)

        eligible: Optional[set[str]] = None
        downgraded: list[str] = []
        if prefer_big_stores:
            client = _get_client()
            directory = await fetch_directory(client)
            eligible = big_store_names(directory, min_reviews=big_store_min_reviews)
            # Surface items where no big store carries them — solver falls back
            # to their full store list (handled inside _build_matrix), but the
            # user should know.
            for item in resolved:
                if not any(o.store_name in eligible for o in item.offers):
                    downgraded.append(item.label)

        result = solve_pareto(
            resolved,
            max_stores=max_stores,
            blocked_stores=blocked_stores or [],
            preferred_stores=preferred_stores or [],
            eligible_stores=eligible,
        )
        if downgraded:
            existing = result.note or ""
            extra = (
                f" Items with no offer at a big store (full store list kept for them): "
                f"{', '.join(downgraded)}."
            )
            result.note = (existing + extra).strip()

        # Enrich every frontier point with store addresses (parallel, deduped per call).
        unique_solutions = [opt.solution for opt in result.frontier]
        await asyncio.gather(*[_enrich_addresses(s) for s in unique_solutions])
        return result.model_dump()

    @mcp.tool()
    async def format_basket(
        solution: dict,
        format: str = "markdown",
        out_path: Optional[str] = None,
        title: Optional[str] = None,
    ) -> dict:
        """Format a BasketSolution into a buyer-friendly handoff document.

        Pass either:
            - a top-level BasketSolution dict (from optimise_within_stores), OR
            - a single ParetoOption dict (from one row of optimise_basket's frontier),
              in which case its `.solution` is used.

        Args:
            solution: dict as described above.
            format: "markdown" (chat/display), "whatsapp" (paste-ready, no markdown),
                or "pdf" (printable handoff with thumbnails — requires the [pdf] extra).
            out_path: required for "pdf"; defaults to data/baskets/basket-<ts>.pdf.
            title: optional header text.

        Returns:
            For text formats: {"format": "...", "content": "<the rendered string>"}
            For pdf:           {"format": "pdf", "path": "/abs/path/to/file.pdf"}
        """

        if "solution" in solution and "stores_used" in solution.get("solution", {}):
            sol = BasketSolution.model_validate(solution["solution"])
        else:
            sol = BasketSolution.model_validate(solution)

        path: Optional[Path] = None
        if format.lower() == "pdf":
            from datetime import datetime as _dt

            if out_path:
                path = Path(out_path)
            else:
                ts = _dt.now().strftime("%Y%m%d-%H%M%S")
                path = Path("data/baskets") / f"basket-{ts}.pdf"

        return _format_basket(sol, format, out_path=path, title=title)

    @mcp.tool()
    async def optimise_within_stores(
        items: list[dict],
        stores: list[str],
        blocked_stores: Optional[list[str]] = None,
        preferred_stores: Optional[list[str]] = None,
    ) -> dict:
        """Optimise spend across a USER-CHOSEN list of stores only.

        Use this when the user has already decided where they're going and just
        wants the cheapest assignment within that set. Items unavailable at every
        allowed store are returned in `missing_items` instead of being silently
        dropped.

        Args:
            items: same shape as optimise_basket.
            stores: the list of store names the user is willing to visit.
            blocked_stores / preferred_stores: same semantics as optimise_basket.

        Output:
            BasketSolution dict (single plan, not a frontier).
        """

        resolved = await _resolve_all(items)
        sol = solve_within_stores(
            resolved,
            allowed_stores=stores,
            blocked_stores=blocked_stores or [],
            preferred_stores=preferred_stores or [],
        )
        await _enrich_addresses(sol)
        return sol.model_dump()
