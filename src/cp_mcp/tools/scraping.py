"""MCP tools: search_products, get_product, get_offers, get_price_history, resolve_query."""

from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

from ..cache import Cache
from ..client import CPClient
from ..compare import compare as compare_product
from ..compare import watch as watch_product
from ..intent import (
    Intent,
    ResolveResult,
    ResolvedCandidate,
    parse_intent,
    query_strategies,
    rank_candidates,
)
from ..models import Condition, Product, ProductCard, SearchResult, SortOrder
from ..parsers import parse_product_html, parse_search_html
from ..parsers.common import parse_product_path

# A single shared client + cache per process. FastMCP tool functions can be sync
# or async; we use async to respect the rate limiter.
_client: Optional[CPClient] = None
_cache: Optional[Cache] = None


def _get_client() -> CPClient:
    global _client, _cache
    if _client is None:
        _cache = Cache()
        _client = CPClient(cache=_cache)
    return _client


def register_scraping_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    async def search_products(
        query: str,
        page: int = 1,
        sort: str = "relevancia",
        include_conditions: Optional[list[str]] = None,
    ) -> dict:
        """Search comprasparaguai.com.br for products.

        Args:
            query: Search term in Portuguese (pt-BR). Examples: "macbook air m4", "rtx 4070".
            page: 1-based page number (20 results per page).
            sort: One of relevancia, menor-preco, maior-preco, produto-asc, produto-desc, novos.
            include_conditions: Conditions to keep in the results. Defaults to ["new"], which
                filters out refurbished ("recondicionado") and CPO products. Pass
                ["new", "recondicionado", "cpo"] to include everything, or just
                ["recondicionado"] / ["cpo"] when the user explicitly wants used.
                The pre-filter total is reported in `filtered_out` so the LLM can tell
                the user when many items were hidden.

        Returns:
            A SearchResult dict with `products`, `suggestions`, `total_results`, `total_pages`,
            plus `filtered_out` (count of cards removed by the condition filter).

        Tips:
            - Start with a broad query (brand + model family). Over-specifying tokens can drop matches
              because product slugs only contain a subset of spec keywords.
            - The `suggestions` list returns category-scoped variants (e.g. notebook/?q=...) which
              often produce a cleaner result set than the cross-category default.
            - Refurbished / CPO products are HIDDEN by default. If a search returns very few "new"
              hits and many were filtered, ask the user before including them.
        """

        try:
            sort_enum = SortOrder(sort)
        except ValueError:
            sort_enum = SortOrder.RELEVANCE
        client = _get_client()
        url, body = await client.fetch_search(query=query, page=page, sort=sort_enum)
        result: SearchResult = parse_search_html(body, query=query, page=page)

        allowed = {c.lower() for c in (include_conditions or ["new"])}
        kept = [p for p in result.products if p.condition.value in allowed]
        filtered_out = len(result.products) - len(kept)
        result.products = kept
        payload = result.model_dump()
        payload["filtered_out"] = filtered_out
        payload["include_conditions"] = sorted(allowed)
        return payload

    @mcp.tool()
    async def get_product(
        product_id: int,
        slug: Optional[str] = None,
    ) -> dict:
        """Fetch a product detail page (specs, full offers list, price history).

        Args:
            product_id: Numeric product ID (the trailing _<id>/ in the product URL).
            slug: Optional URL slug. Required if it is the first time we look up this product.
                  After a search, callers can pass the slug from the ProductCard.

        Returns:
            A Product dict, including `offers`, `specifications`, `price_history`.
        """

        client = _get_client()
        if slug:
            path = f"/{slug}_{product_id}/"
        else:
            # Without a slug we can't construct the URL — ask the caller to search first.
            raise ValueError(
                "slug is required for the first fetch of a product; pass the slug from a search result"
            )
        url, body = await client.fetch_product(slug, product_id)
        product: Product = parse_product_html(body, product_id=product_id, slug=slug, url=path)
        return product.model_dump()

    @mcp.tool()
    async def get_offers(product_id: int, slug: str) -> list[dict]:
        """Return just the per-store offers for a product, sorted by US$ ascending.

        Same inputs as get_product, but returns a flat list of offers — useful for
        comparison without paying for the full product payload.
        """

        client = _get_client()
        url, body = await client.fetch_product(slug, product_id)
        product = parse_product_html(body, product_id=product_id, slug=slug, url=url)
        offers = sorted(product.offers, key=lambda o: o.price_usd)
        return [o.model_dump() for o in offers]

    @mcp.tool()
    async def get_price_history(product_id: int, slug: str) -> list[dict]:
        """Return the price history (monthly minima in US$) for a product.

        Returns a list of {month: "MM/YYYY", price_usd: float} sorted oldest-first
        as the site provides it.
        """

        client = _get_client()
        url, body = await client.fetch_product(slug, product_id)
        product = parse_product_html(body, product_id=product_id, slug=slug, url=url)
        return [p.model_dump() for p in product.price_history]

    @mcp.tool()
    async def resolve_query(
        intent_text: Optional[str] = None,
        brand: Optional[str] = None,
        family: Optional[str] = None,
        chip: Optional[str] = None,
        ram_gb: Optional[int] = None,
        storage_gb: Optional[int] = None,
        year: Optional[int] = None,
        screen_inches: Optional[float] = None,
        include_conditions: Optional[list[str]] = None,
        max_pages_per_strategy: int = 1,
        max_candidates: int = 10,
        min_score: int = 5,
    ) -> dict:
        """Resolve a buying intent into ranked canonical-product candidates.

        Pass either a free-text `intent_text` ("MacBook Air M4 16/512") OR structured fields,
        OR both — structured fields override the parsed text. The tool then runs progressively
        broader-to-narrower searches, scores results against the full intent (chip/RAM/
        storage/year/screen weighted), and returns the top candidates.

        Args:
            intent_text: Optional free-text description of the product.
            brand: e.g. "apple", "samsung". Lower-case.
            family: e.g. "macbook-air", "iphone", "rtx", "990-pro".
            chip: e.g. "m4", "a18-pro", "rtx-4070-super".
            ram_gb: RAM in GB.
            storage_gb: storage in GB (e.g. 1024 for 1TB).
            year: model year.
            screen_inches: e.g. 13.6.
            include_conditions: defaults to ["new"]. Use ["new","recondicionado"] etc. to
                broaden — refurbished/CPO is hidden by default and the tool will note when
                the user might want them included.
            max_pages_per_strategy: how many search pages to fetch per query attempt.
            max_candidates: cap on returned candidates.
            min_score: minimum match score for inclusion. 5 ≈ "definitely the right family".

        Returns:
            ResolveResult dict with: parsed `intent`, `tried_queries` (debug), `candidates`
            (ranked best-first), `note` (e.g. "no exact match — relaxed to family-only").
        """

        if intent_text is None and not any([brand, family, chip, ram_gb, storage_gb]):
            raise ValueError("Provide intent_text or at least one structured field")

        intent = parse_intent(intent_text or "") if intent_text else Intent(raw="")
        # Structured args override parsed text.
        if brand is not None:
            intent.brand = brand.lower()
        if family is not None:
            intent.family = family.lower()
        if chip is not None:
            intent.chip = chip.lower()
        if ram_gb is not None:
            intent.ram_gb = ram_gb
        if storage_gb is not None:
            intent.storage_gb = storage_gb
        if year is not None:
            intent.year = year
        if screen_inches is not None:
            intent.screen_inches = screen_inches
        if include_conditions:
            try:
                intent.conditions = [Condition(c.lower()) for c in include_conditions]
            except ValueError as e:
                raise ValueError(
                    f"unknown condition; use 'new', 'recondicionado', or 'cpo': {e}"
                ) from e

        client = _get_client()
        strategies = query_strategies(intent)
        tried: list[str] = []
        all_cards: dict[int, ProductCard] = {}

        for q in strategies:
            tried.append(q)
            for page in range(1, max_pages_per_strategy + 1):
                _, body = await client.fetch_search(query=q, page=page)
                page_result = parse_search_html(body, query=q, page=page)
                for card in page_result.products:
                    all_cards.setdefault(card.product_id, card)
                if not page_result.products:
                    break

            ranked = rank_candidates(all_cards.values(), intent, min_score=min_score)
            if ranked:
                # Stop expanding once we have at least one strong candidate.
                trimmed = ranked[:max_candidates]
                return ResolveResult(
                    intent=intent, tried_queries=tried, candidates=trimmed
                ).model_dump()

        # Nothing scored high enough — return the closest near-misses for transparency.
        all_ranked = rank_candidates(all_cards.values(), intent, min_score=-1000)[:max_candidates]
        note = (
            "No candidate met the minimum match score. Returning closest near-misses sorted by score; "
            "consider relaxing intent (e.g. drop year or storage) or asking the user to confirm a substitute."
        )
        return ResolveResult(
            intent=intent, tried_queries=tried, candidates=all_ranked, note=note
        ).model_dump()

    @mcp.tool()
    async def compare_prices(product_id: int, slug: str) -> dict:
        """Fetch a product and return per-store offer comparison + price-history context.

        Output:
            - cheapest_usd / cheapest_store: the single best price right now.
            - by_store: one row per store (their cheapest offer), sorted ascending.
              Each row carries delta_vs_cheapest_usd / pct so the LLM can describe
              the spread quickly.
            - history: 12-month low/high/median, latest, percent-above-min, trend
              ("rising" / "falling" / "flat").

        Use this for "should I buy now or wait?" questions and for picking a single
        store when you've already settled on a product.
        """

        client = _get_client()
        url, body = await client.fetch_product(slug, product_id)
        product = parse_product_html(body, product_id=product_id, slug=slug, url=url)
        return compare_product(product).model_dump()

    @mcp.tool()
    async def watch_price(
        product_id: int, slug: str, target_usd: float
    ) -> dict:
        """Stateless: is the current price at or below `target_usd`, and where does
        it sit in the 12-month band?

        Returns target_met, current vs target delta, history min/max, pct_of_band
        (0.0 = at all-time low, 1.0 = at all-time high), and a short human note like
        "at or near the 12-month low".

        Caller decides how often to poll — there's no background loop.
        """

        client = _get_client()
        url, body = await client.fetch_product(slug, product_id)
        product = parse_product_html(body, product_id=product_id, slug=slug, url=url)
        return watch_product(product, float(target_usd)).model_dump()

    @mcp.tool()
    async def find_best_deal(
        intent_text: Optional[str] = None,
        brand: Optional[str] = None,
        family: Optional[str] = None,
        chip: Optional[str] = None,
        ram_gb: Optional[int] = None,
        storage_gb: Optional[int] = None,
        year: Optional[int] = None,
        screen_inches: Optional[float] = None,
        include_conditions: Optional[list[str]] = None,
    ) -> dict:
        """One-shot: resolve intent → pick top candidate → compare prices → return the best offer.

        Use the same args as `resolve_query`. Returns:
            - product: the candidate that won
            - match: the matched/mismatched fields and score
            - best_offer: the cheapest store + price + WhatsApp link
            - history: 12-month context for the chosen product
            - alternatives: up to 4 other candidates (so the LLM can offer choices)
            - note: any caveats (e.g. "no exact spec match — relaxed to family")
        """

        resolve_payload = await resolve_query(
            intent_text=intent_text,
            brand=brand,
            family=family,
            chip=chip,
            ram_gb=ram_gb,
            storage_gb=storage_gb,
            year=year,
            screen_inches=screen_inches,
            include_conditions=include_conditions,
        )

        candidates = resolve_payload.get("candidates") or []
        if not candidates:
            return {
                "ok": False,
                "reason": "no candidates from resolve_query",
                "tried_queries": resolve_payload.get("tried_queries", []),
            }

        winner = candidates[0]
        winner_card = winner["card"]
        client = _get_client()
        url, body = await client.fetch_product(winner_card["slug"], winner_card["product_id"])
        product = parse_product_html(
            body,
            product_id=winner_card["product_id"],
            slug=winner_card["slug"],
            url=url,
        )
        comparison = compare_product(product)
        cheapest = comparison.by_store[0] if comparison.by_store else None

        return {
            "ok": True,
            "product": {
                "product_id": product.product_id,
                "title": product.title,
                "url": product.url,
                "condition": product.condition.value,
            },
            "match": {
                "score": winner["score"],
                "matched": winner["matched"],
                "mismatched": winner["mismatched"],
                "missing": winner["missing"],
            },
            "best_offer": cheapest.model_dump() if cheapest else None,
            "history": comparison.history.model_dump(),
            "alternatives": candidates[1:5],
            "note": resolve_payload.get("note"),
        }

    @mcp.tool()
    async def parse_product_url(url: str) -> dict:
        """Helper: split a product URL or path into its slug and product_id.

        Use this when an LLM has a full URL but needs the structured (slug, product_id)
        pair to call get_product.
        """

        parsed = parse_product_path(url)
        if parsed is None:
            return {"ok": False, "error": "not a product URL"}
        slug, pid = parsed
        return {"ok": True, "slug": slug, "product_id": pid}
