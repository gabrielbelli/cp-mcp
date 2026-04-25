"""HTTP client for comprasparaguai.com.br.

- httpx.AsyncClient with realistic UA and pt-BR Accept-Language
- Token-bucket rate limiter (RPS configurable via env)
- Retries with backoff on 429/5xx
- Synchronous sqlite cache so repeat calls during a session don't re-hit the site
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from typing import Optional
from urllib.parse import urlencode, urljoin

import httpx
import structlog

from .cache import Cache
from .models import SortOrder

log = structlog.get_logger("cp_mcp.client")

BASE_URL = "https://www.comprasparaguai.com.br"

DEFAULT_TTLS = {
    "search": 60 * 60,
    "product": 6 * 60 * 60,
    "store": 7 * 24 * 60 * 60,
}


class TokenBucket:
    def __init__(self, rps: float, burst: int) -> None:
        self.capacity = max(1, int(burst))
        self.tokens = float(self.capacity)
        self.rate = max(0.1, float(rps))
        self.updated_at = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(
                    self.capacity, self.tokens + (now - self.updated_at) * self.rate
                )
                self.updated_at = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                wait = (1 - self.tokens) / self.rate
                await asyncio.sleep(wait)


class CPClient:
    """Polite HTTP client. Each instance shares one httpx connection pool."""

    def __init__(
        self,
        *,
        user_agent: Optional[str] = None,
        rate_limit_rps: Optional[float] = None,
        rate_limit_burst: Optional[int] = None,
        cache: Optional[Cache] = None,
        timeout: float = 20.0,
        max_retries: int = 3,
    ) -> None:
        ua = user_agent or os.getenv("CP_USER_AGENT") or (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 cp-mcp/0.1"
        )
        rps = rate_limit_rps if rate_limit_rps is not None else float(
            os.getenv("CP_RATE_LIMIT_RPS", "1.0")
        )
        burst = rate_limit_burst if rate_limit_burst is not None else int(
            os.getenv("CP_RATE_LIMIT_BURST", "3")
        )
        self.bucket = TokenBucket(rps=rps, burst=burst)
        self.cache = cache  # may be None for tests
        self.max_retries = max_retries
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": ua,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            },
            http2=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "CPClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def _get_text(self, url: str, ttl_seconds: float) -> str:
        absolute = url if url.startswith("http") else urljoin(BASE_URL + "/", url.lstrip("/"))

        if self.cache is not None:
            cached = self.cache.get(absolute)
            if cached is not None:
                log.debug("cache_hit", url=absolute)
                return cached

        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            await self.bucket.acquire()
            try:
                resp = await self._client.get(absolute)
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last_exc = e
                await asyncio.sleep(_backoff(attempt))
                continue
            if resp.status_code in (429, 502, 503, 504):
                last_exc = httpx.HTTPStatusError(
                    f"retryable {resp.status_code}", request=resp.request, response=resp
                )
                retry_after = resp.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else _backoff(attempt)
                await asyncio.sleep(delay)
                continue
            resp.raise_for_status()
            body = resp.text
            if self.cache is not None:
                self.cache.put(absolute, body, ttl_seconds)
            return body

        assert last_exc is not None
        raise last_exc

    async def fetch_search(
        self, query: str, page: int = 1, sort: SortOrder = SortOrder.RELEVANCE
    ) -> tuple[str, str]:
        params = {"q": query}
        if page and page > 1:
            params["page"] = str(page)
        if sort != SortOrder.RELEVANCE:
            params["ordem"] = sort.value
        url = "/busca/?" + urlencode(params)
        body = await self._get_text(url, ttl_seconds=DEFAULT_TTLS["search"])
        return urljoin(BASE_URL, url), body

    async def fetch_product(self, slug_or_path: str, product_id: Optional[int] = None) -> tuple[str, str]:
        if product_id is not None:
            path = f"/{slug_or_path}_{product_id}/"
        else:
            path = slug_or_path if slug_or_path.startswith("/") else f"/{slug_or_path}"
        body = await self._get_text(path, ttl_seconds=DEFAULT_TTLS["product"])
        return urljoin(BASE_URL, path), body

    async def fetch_store(self, slug: str) -> tuple[str, str]:
        path = f"/lojas/{slug.strip('/')}/"
        body = await self._get_text(path, ttl_seconds=DEFAULT_TTLS["store"])
        return urljoin(BASE_URL, path), body

    async def fetch_store_directory_page(self, page: int = 1) -> tuple[str, str]:
        path = "/lojas/" if page <= 1 else f"/lojas/?page={page}"
        body = await self._get_text(path, ttl_seconds=DEFAULT_TTLS["store"])
        return urljoin(BASE_URL, path), body


def _backoff(attempt: int) -> float:
    base = 0.5 * (2**attempt)
    return base + random.uniform(0, base * 0.2)
