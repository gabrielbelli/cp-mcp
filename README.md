# cp-mcp

[![CI](https://github.com/gabrielbelli/cp-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/gabrielbelli/cp-mcp/actions/workflows/ci.yml)
[![License: BSD-2-Clause](https://img.shields.io/badge/license-BSD--2--Clause-blue.svg)](./LICENSE)

MCP server for **comprasparaguai.com.br** — spec-aware product search and multi-store basket optimisation tuned for Paraguayan border-shopping (Ciudad del Este by default). Plans a buying trip with the fewest stores, surfaces price history, and exports a buyer-friendly handoff in Markdown / WhatsApp / PDF.

See [`PLAN.md`](./PLAN.md) for the full design and roadmap.

## Tools

| Tool | Purpose |
|------|---------|
| `search_products` | Site search; refurbished/CPO hidden by default. |
| `resolve_query` | Map a buy intent → ranked canonical products with match scores. |
| `get_product` / `get_offers` / `get_price_history` | Raw product, per-store offers, 12-month minima. |
| `compare_prices` | Per-store dedup + deltas + history percentile. |
| `find_best_deal` | One-shot: resolve → compare → return single best offer. |
| `watch_price` | Stateless target check + position in 12-month band. |
| `optimise_basket` | Pareto frontier over store-counts; prefers big stores by default, smaller stores fall to alternatives. |
| `optimise_within_stores` | Cheapest assignment across a user-fixed store list. |
| `format_basket` | Render solution to Markdown / WhatsApp / PDF. |
| `parse_product_url` | URL → (slug, product_id) helper. |
| `ping` | Liveness. |

## Quick start (local, venv)

Requires Python ≥ 3.12.

```bash
python3 -m venv venv
./venv/bin/pip install -e ".[dev]"
./venv/bin/pytest
./venv/bin/cp-mcp        # runs the MCP server over stdio
```

PDF export needs WeasyPrint + system pango/cairo:
```bash
pip install ".[pdf]"
brew install pango          # macOS
apt install libpango-1.0-0 libpangoft2-1.0-0 libcairo2  # Debian/Ubuntu
```

## Docker

Pre-built multi-arch images (linux/amd64, linux/arm64) are published on every push to `main`:

```bash
docker pull ghcr.io/gabrielbelli/cp-mcp:latest
docker run --rm -i -v cp-mcp-data:/data ghcr.io/gabrielbelli/cp-mcp:latest
```

Or build locally:
```bash
docker compose build
docker compose run --rm cp-mcp
```

The Docker image already includes pango/cairo for PDF rendering. A named volume holds the sqlite cache at `/data/cache.sqlite`.

> **First publish:** GHCR images inherit the source repository's visibility *only after* the image is linked. The label `org.opencontainers.image.source` is set automatically by the workflow; if you fork this repo, visit *Your profile → Packages → cp-mcp → Package settings* and either link to the repo or flip visibility to public manually.

## Configuration

Environment variables (see `.env.example`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `CP_USER_AGENT` | `cp-mcp/0.1` | UA sent with all HTTP requests. |
| `CP_RATE_LIMIT_RPS` | `1.0` | Max requests/sec to the site. |
| `CP_RATE_LIMIT_BURST` | `3` | Token-bucket burst. |
| `CP_CACHE_PATH` | `/data/cache.sqlite` (Docker) / `data/cache.sqlite` (local) | sqlite cache location. |
| `CP_LOG_LEVEL` | `INFO` | structlog level. |

## Wiring into Claude

### Claude Desktop / Claude Code — Docker (recommended)

```json
{
  "mcpServers": {
    "cp": {
      "command": "docker",
      "args": ["run", "--rm", "-i", "-v", "cp-mcp-data:/data", "ghcr.io/gabrielbelli/cp-mcp:latest"]
    }
  }
}
```

### Claude Desktop / Claude Code — local venv

```json
{
  "mcpServers": {
    "cp": {
      "command": "/absolute/path/to/cp-mcp/venv/bin/cp-mcp"
    }
  }
}
```

## Layout

```
src/cp_mcp/
├── server.py            # FastMCP entry, registers tools
├── client.py            # httpx async, rate limiter, sqlite cache
├── intent.py            # query intent parser, scoring, relaxation
├── basket.py            # Pareto-frontier solver
├── compare.py           # offer/history analytics
├── format.py            # markdown / whatsapp / PDF renderers
├── store_index.py       # /lojas/ directory cache + tier classification
├── parsers/             # search / product / store HTML parsers
└── tools/               # MCP tool registrations
tests/
└── fixtures/            # saved HTML for offline parser tests
scripts/
└── verify_all_tools.py  # end-to-end smoke against the live site
```

## Notes

- Data is scraped from the public site (no API). The default rate limit (1 req/s) is intentionally polite — don't lower it without reason.
- Prices always render in **US$ + R$** (audience is Brazilian).
- Refurbished / CPO products are excluded unless the caller opts in.
- Big-store preference is on by default. Smaller stores with cheaper prices stay visible in each item's `alternatives` so the user can override per item.

## Licence

BSD 2-Clause — see [`LICENSE`](./LICENSE).
