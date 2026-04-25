"""Cache + accessor for the directory of stores ranked by review count.

We treat the top-N stores (by Google review count visible on /lojas/) as the
"big" stores — established advertisers with consistent inventory and reputation.
Everything else is downgraded in the optimiser unless the user opts back in.
"""

from __future__ import annotations

from typing import Iterable, Optional

from .client import CPClient
from .models import Store
from .parsers import parse_store_directory_html


# In-process cache. The HTTP layer's sqlite cache also persists the raw HTML
# for 7 days, so a fresh process pays at most one round-trip per directory page.
_CACHE: Optional[list[Store]] = None


async def fetch_directory(client: CPClient, *, max_pages: int = 2) -> list[Store]:
    """Fetch up to `max_pages` of /lojas/ and return a flat list of stores."""

    global _CACHE
    if _CACHE is not None:
        return _CACHE
    out: list[Store] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        _, body = await client.fetch_store_directory_page(page)
        page_stores = parse_store_directory_html(body)
        if not page_stores:
            break
        new_count = 0
        for s in page_stores:
            key = (s.slug or s.name).lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(s)
            new_count += 1
        if new_count == 0:
            break
    _CACHE = out
    return out


def reset_cache() -> None:
    global _CACHE
    _CACHE = None


def big_store_names(stores: Iterable[Store], min_reviews: int = 1000) -> set[str]:
    """Return the set of store names with at least `min_reviews` Google reviews.

    `min_reviews=1000` works well in practice — it captures the established
    chains (Atacado Connect ~6k, Mega Eletrônicos ~11k, Shopping China ~25k, ...)
    and excludes one-shop boutiques.
    """

    out: set[str] = set()
    for s in stores:
        if s.review_count is not None and s.review_count >= min_reviews:
            out.add(s.name)
    return out
