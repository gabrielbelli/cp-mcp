# cp-mcp — Plan

An MCP server that searches **comprasparaguai.com.br** and helps decide *what to buy and where*, optimising for **best total price with the fewest store visits**.

---

## 1. Site reconnaissance (what we know)

### URL surface
- **Search:** `https://www.comprasparaguai.com.br/busca/?q=<term>&page=<n>&ordem=<sort>`
  - `ordem`: `relevancia` (default), `menor-preco`, `maior-preco`, `produto-asc`, `produto-desc`, `novos`
  - Pagination via `page=N` (e.g. "macbook" returns 363 results, ~19 pages)
- **Category-scoped search:** `/<categoria>/?q=<term>` (e.g. `/notebook/?q=macbook`) — used by the "Sugestões" panel to narrow scope.
- **Product detail:** slug + numeric ID, e.g. `/notebook-apple-macbook-air-2025-apple-m4-memoria-16gb-ssd-512gb-136_59619/`. The trailing `_<id>/` is the canonical identifier.
- **Categories:** `/celular/`, `/notebook/`, `/eletronicos/`, `/informatica/`, etc.
- **Stores list:** `/lojas/`.
- **Brands:** `/marcas/`.

### Rendering & access
- HTML is **server-side rendered**. A `curl` with a normal desktop User-Agent returns the full product list inline (verified: 363 results for "macbook", 281 occurrences of the literal "MacBook" in the HTML).
- Cloudflare sits in front but does not challenge basic GETs. No JS execution required for v1.
- **No public API** found. Scraping is the only path.
- `data-historico` attribute on `<canvas id="grafico-modelo">` carries the price history as a JSON array of `{x: "MM/YYYY", y: <USD>}` (monthly minimum). No extra request needed.

### Data model on each product page
- Title, brand, model, condition (new / `recondicionado` / `CPO`).
- Specifications **table** (CPU, RAM, storage, screen, ports, connectivity) — useful for canonicalisation.
- **Offers list** — multiple stores per product, each with:
  - Store name (in `gtag('advertiser', …)` calls and in `data-` attributes)
  - Price in **US$** and converted **R$**
  - WhatsApp deep-link with phone number → identifies the store contact
  - Outbound "ir para a loja" button
  - Per-offer numeric ID
- Price history JSON (12+ months of monthly minima in US$).

### Caveats observed
- **Variants are different products.** "iPhone 15 128GB" and "iPhone 15 256GB" have separate IDs and pages. Reconditioned and CPO variants are also separate products.
- **Over-specific queries miss matches.** Slugs use specific phrasings ("memoria-16gb-ssd-512gb-136"). Adding "16GB 512GB 13 inch space gray M4" to a query can drop matches that *would* satisfy the spec because the slug only contains some of those tokens.
- **Wrong keyword → wrong category.** "macbook" alone surfaces unrelated processors and accessories ranked by relevance, alongside MacBooks.
- The "Sugestões" panel is gold: it returns category-scoped versions of your query (e.g. for "macbook" it suggests `/notebook/?q=macbook` with 257 results). Use this as a first refinement step.

---

## 2. The hard problem: query intent → canonical products

The user's mental query (e.g. "MacBook Air M4 16/512") rarely maps cleanly onto site search. The plan:

1. **Broad query, then filter.** Fetch with a *minimum-keywords* query (brand + model family). Pull the first ~3 pages.
2. **Spec-aware re-ranking.** Parse each candidate's slug + spec table for: brand, model, year, RAM, storage, condition, screen size, chip. Score each against the user's *intended* spec.
3. **Iterative relaxation.** If scoring yields zero strong matches, drop tokens in this order: storage > RAM > year > screen > color > model variant. Stop when matches ≥ N.
4. **Use category suggestions.** When the broad search spans categories, prefer the category-scoped variant the site itself proposes.
5. **Persist a "canonical product" map.** Once we identify that "MBA M4 16/512 13.6"" maps to product ID `59619`, cache that mapping by spec hash so future queries skip steps 1–3.

This logic lives in a `resolve_intent()` layer **above** raw search.

---

## 3. Architecture

```
┌──────────────────────────────────────────────────────────────┐
│ MCP server (FastMCP)                                         │
│                                                              │
│  High-level tools (intent, basket optimisation)              │
│      │                                                       │
│  Mid-level tools (resolve, compare, history)                 │
│      │                                                       │
│  Low-level tools (search, get_product, get_offers)           │
│      │                                                       │
│  Scraper core (httpx + selectolax)                           │
│      │                                                       │
│  Cache (sqlite) + rate limiter + Cloudflare-friendly client  │
└──────────────────────────────────────────────────────────────┘
```

### Module layout
```
cp-mcp/
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── .env
├── README.md
├── PLAN.md
├── venv/                         # local only, gitignored
├── src/cp_mcp/
│   ├── __init__.py
│   ├── server.py                 # FastMCP entry, registers tools
│   ├── client.py                 # httpx AsyncClient w/ UA, retries, rate-limit
│   ├── parsers/
│   │   ├── search.py             # search results page → list[ProductCard]
│   │   ├── product.py            # product page → Product, Offer[], PriceHistory
│   │   └── suggestions.py        # parse the "Sugestões" panel
│   ├── models.py                 # pydantic: ProductCard, Product, Offer, Store, ...
│   ├── intent.py                 # query intent resolver (spec scoring + relaxation)
│   ├── basket.py                 # multi-store optimisation
│   ├── cache.py                  # sqlite-backed cache with TTLs per resource
│   └── tools/
│       ├── search.py             # tool: search_products
│       ├── product.py            # tool: get_product, get_price_history, get_offers
│       ├── compare.py            # tool: compare_prices
│       └── basket.py             # tool: optimise_basket, suggest_itinerary
└── tests/
    ├── fixtures/                 # saved HTML for offline parser tests
    ├── test_parsers.py
    ├── test_intent.py
    └── test_basket.py
```

### Stack choices
- **Python 3.12** in a `venv` (per user preference).
- **httpx** (async) — keep-alive, HTTP/2 optional, easier than aiohttp.
- **selectolax** for parsing (CSS selectors, ~30× faster than BeautifulSoup) with `BeautifulSoup` as a fallback for messy bits.
- **pydantic v2** for models.
- **mcp** (`pip install mcp`) — official Python SDK with FastMCP-style decorators, runs over stdio.
- **sqlite (aiosqlite)** for cache — single-file, easy to mount as a Docker volume.
- **structlog** for JSON logs.
- **pytest + pytest-asyncio + respx** for tests with HTTP mocking.

---

## 4. MCP tool surface

### Low-level (one site action each)
| Tool | Purpose |
|------|---------|
| `search_products(query, page=1, sort="relevancia", category=None)` | Raw search; returns a page of `ProductCard`s (id, slug, title, lowest US$/R$, offer count, image). |
| `get_product(product_id)` | Full product: title, specs (parsed table), brand, condition, category, image. |
| `get_offers(product_id)` | List of `Offer`s with store name, price US$/R$, WhatsApp phone, outbound URL, per-offer ID. |
| `get_price_history(product_id)` | Parsed `data-historico` array → `[{month, price_usd}]`. |
| `list_stores()` | The `/lojas/` directory. |
| `list_categories()` / `list_brands()` | Static-ish references for filtering. |

### Mid-level (composed, with intent + caching)
| Tool | Purpose |
|------|---------|
| `resolve_query(intent, max_candidates=10)` | Run the *broad → filter → relax* pipeline; return ranked candidates with match scores and spec deltas. |
| `compare_prices(product_id)` | Returns offers sorted by total price (US$ + payment method nuances if found), with per-store delta vs lowest, plus a 3-line price-history summary (current vs 30/90/365-day low). |
| `find_best_deal(intent, condition="any")` | One-shot: resolve intent → compare → return the single best offer with rationale. |

### High-level (the why-we-built-this tools)
| Tool | Purpose |
|------|---------|
| `optimise_basket(items, max_stores=None, store_visit_cost_usd=15)` | Given a list of intents (e.g. `["MBA M4 16/512", "iPhone 16 Pro 256", "Samsung 990 Pro 2TB"]`), pick which store sells which item to minimise `Σprice + max_stores * visit_cost`. Returns a per-store shopping list. |
| `suggest_itinerary(items, ...)` | Same, but with a human-readable plan: "Go to *Shopping China* for items 1+3 (US$ 1,545) and *Cellshop* for item 2 (US$ 870). Total: US$ 2,415, 2 stores." |
| `watch_price(product_id, target_usd)` | Returns whether current price is at/below target, plus where it sits in the 12-month band. (No background polling in v1 — caller decides cadence.) |

### MCP resources (read-only data, not tool calls)
- `cp://product/<id>` — full JSON for a product (cached snapshot).
- `cp://history/<id>` — price history JSON.

### MCP prompts
- `find-laptop-prompt` — pre-baked prompt that asks the LLM to elicit chip/RAM/storage/condition before calling `resolve_query`.

---

## 5. Multi-store basket optimisation

This is the differentiator. The shape:

**Inputs:**
- `items: list[CanonicalProduct]` (each already resolved to a product ID with offers)
- `max_stores: int | None` — hard cap on stores
- `store_visit_cost_usd: float` — soft cost per store visited (default ~US$15: time + transport)
- Optional `excluded_stores`, `preferred_stores`.

**Approach:**
- For ≤8 items × ≤30 stores, **brute-force / DP** is fine: enumerate subsets of stores up to `max_stores`, for each subset assign each item to its cheapest available offer, pick the subset minimising total.
- For larger, fall back to **ILP** with `pulp` (free CBC solver):
  - Vars: `y_s ∈ {0,1}` (visit store s), `x_{i,s} ∈ {0,1}` (buy item i at store s).
  - Constraints: `Σ_s x_{i,s} = 1` for each i; `x_{i,s} ≤ y_s`.
  - Objective: `min Σ x_{i,s} * price_{i,s} + visit_cost * Σ y_s`.
- Output: per-store basket + total + savings vs "cheapest-per-item ignoring stores" baseline + savings vs "single best store" baseline.

**Nice extras (v2):**
- Penalty for stores without WhatsApp contact.
- Bonus for stores listed in `Premium / Authorised retailers` section (lower fraud risk).
- Currency awareness: some stores price in PYG; we already have US$ for all.

---

## 6. Caching, rate limiting, robustness

- **Cache TTLs (sqlite):**
  - Search pages: 1 h (prices change but slowly within a day).
  - Product pages: 6 h.
  - Price history: 24 h.
  - Stores/categories/brands: 7 days.
- **Rate limit:** token bucket, default 1 req/s with burst 3. Configurable via env.
- **Politeness:**
  - Honour `robots.txt` (fetch once, cache).
  - Realistic UA, `Accept-Language: pt-BR,pt;q=0.9,en;q=0.8`.
  - Exponential backoff on 429/503; 5 retries max.
  - Surface a `User-Agent` env var so it's traceable to the user if the site asks.
- **Cloudflare:** v1 assumes plain GETs work (verified). If a challenge appears later, options are `cloudscraper` or `curl_cffi`. Don't pre-optimise.
- **No login flow.** All data we need is public.

---

## 7. Docker deployment

- Multi-stage `Dockerfile`: builder installs into `/opt/venv`, runtime is `python:3.12-slim` + the venv.
- Container runs the MCP server over **stdio** by default (typical Claude Desktop / Claude Code config).
- Optional: expose an **HTTP+SSE** transport for remote MCP clients (FastMCP supports both).
- `docker-compose.yml` mounts `./data` for the sqlite cache and exposes env vars (`CP_USER_AGENT`, `CP_RATE_LIMIT`, `CP_LOG_LEVEL`).
- Image goal: <150 MB.

Sample run:
```bash
docker compose run --rm cp-mcp                   # stdio mode
docker compose up cp-mcp-http                    # HTTP/SSE mode on :8765
```

Claude Desktop config snippet (will live in README):
```json
{
  "mcpServers": {
    "cp": {
      "command": "docker",
      "args": ["run", "--rm", "-i", "-v", "cp-mcp-data:/data", "cp-mcp:latest"]
    }
  }
}
```

---

## 8. Test plan (training products)

Each must give sane results before we call v1 done:

| Intent | Expected behaviour |
|--------|---------------------|
| "MacBook Air M4 16GB 512GB 13" | Resolves to product `_59619`; offers from ~28 stores; lowest US$ ~1,025–1,075. |
| "iPhone 16 Pro 256GB" | Distinguishes new vs CPO vs reconditioned; returns the *new* unless asked. |
| "RTX 4070 Super" | Disambiguates brand variants (ASUS, MSI, Gigabyte) — show top 5, not just one. |
| "Samsung 990 Pro 2TB NVMe" | Spec match on the slug; relaxes "NVMe" if needed (most slugs say "SSD"). |
| Basket: MBA + iPhone + RTX | Suggests 1–2 store itinerary with visible savings vs "buy each cheapest". |

Parser tests use **saved HTML fixtures** so they don't hit the network.

---

## 9. Phased roadmap

- **Phase 0 — scaffolding** (1 PR): repo skeleton, venv, Dockerfile, FastMCP "hello" tool, CI.
- **Phase 1 — read-only scraping** (1 PR): `search_products`, `get_product`, `get_offers`, `get_price_history` + fixture-based parser tests.
- **Phase 2 — intent resolver** (1 PR): `resolve_query` with spec scoring + relaxation; canonical-product cache.
- **Phase 3 — comparison tools** (1 PR): `compare_prices`, `find_best_deal`, `watch_price`.
- **Phase 4 — basket optimisation** (1 PR): brute-force solver + `optimise_basket` + `suggest_itinerary`.
- **Phase 5 — packaging** (1 PR): final Docker image, README, Claude Desktop config example.
- **Phase 6 — polish** (later): ILP solver, premium-store bonus, price-watch persistence, CLI wrapper for ad-hoc use.

---

## 10. Skill vs MCP — recommendation

User asked "MCP or skill?". Both, but:
- The **MCP server** is the right home for the work — it's stateful, networked, and shareable across Claude clients (Desktop, Code, web).
- A thin **Claude Code skill** (one markdown file) can be added later as a friendly entry point: `/cp <intent>` → calls `find_best_deal` and renders the result. Ship MCP first; skill is a 1-hour wrapper.

---

## 11. Open questions for you

1. **Visit cost**: does US$15/store match your reality, or do you want to set it per session?
2. **Currency display**: prefer US$, R$, or both? (Site shows both; we'd default to US$ since stores price natively in US$.)
3. **Condition default**: when ambiguous, prefer *new* and only show recondicionado/CPO on request — agree?
4. **Stores you avoid?** Any blocklist we should hardcode (e.g. ones that have flaked on you)?
5. **Premium stores**: want a soft preference for the site's "Authorised retailers" list, even if a few US$ more expensive?
6. **Deployment target**: local Docker only, or also a remote host (e.g. a small VPS) so you can hit it from your phone?
7. **Price watch**: needed in v1, or fine to defer? It implies a background loop somewhere.
