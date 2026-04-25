# cp-mcp

MCP server for searching and comparing prices on **comprasparaguai.com.br**, with multi-store basket optimisation tuned for Paraguayan border-shopping (Ciudad del Este by default).

See [`PLAN.md`](./PLAN.md) for the full design and roadmap.

## Status

**Phase 0 — scaffolding.** The server starts and registers a `ping` tool. Real search / scraping tools land in Phase 1.

## Local development

Requires Python ≥ 3.12.

```bash
python3 -m venv venv
./venv/bin/pip install -e ".[dev]"
./venv/bin/pytest
./venv/bin/cp-mcp        # runs the MCP server over stdio
```

## Docker

```bash
docker compose build
docker compose run --rm cp-mcp
```

A named volume `cp-mcp-data` holds the sqlite cache at `/data/cache.sqlite`.

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

### Claude Desktop / Claude Code (local venv)

```json
{
  "mcpServers": {
    "cp": {
      "command": "/absolute/path/to/cp-mcp/venv/bin/cp-mcp"
    }
  }
}
```

### Claude Desktop / Claude Code (Docker)

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

## Layout

```
src/cp_mcp/
├── server.py        # FastMCP entry, registers tools
├── client.py        # HTTP client (Phase 1)
├── models.py        # pydantic models (Phase 1)
├── parsers/         # search / product / suggestions parsers (Phase 1)
└── tools/           # MCP tool implementations (Phases 1–4)
tests/
├── fixtures/        # saved HTML for offline parser tests
└── test_smoke.py
```

## Notes

- All product/store data is scraped from the public site (no API exists). Be polite — don't lower the rate limit without reason.
- Prices are reported primarily in **US$** (the native currency on the site), with R$ alongside.
