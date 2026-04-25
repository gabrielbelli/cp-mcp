"""End-to-end smoke test of every cp-mcp tool against the live site.

Run from project root: ./venv/bin/python scripts/verify_all_tools.py

Writes:
- data/verify/<tool>.json for each tool's response
- data/baskets/buylist-<ts>.md
- data/baskets/buylist-<ts>.txt   (WhatsApp form)
- data/baskets/buylist-<ts>.basket.json   (input for the Docker PDF step)
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from cp_mcp.server import mcp  # noqa: E402

VERIFY = ROOT / "data" / "verify"
BASKETS = ROOT / "data" / "baskets"
VERIFY.mkdir(parents=True, exist_ok=True)
BASKETS.mkdir(parents=True, exist_ok=True)


async def call(name: str, args: dict):
    """FastMCP's call_tool can return a list of content items or a tuple of
    (content_list, structured_dict) depending on the tool's return type. Normalise."""

    out = await mcp.call_tool(name, args)
    content_list = None
    structured = None
    if isinstance(out, tuple) and len(out) == 2:
        content_list, structured = out
    else:
        content_list = out

    if structured:
        if isinstance(structured, dict) and set(structured.keys()) == {"result"}:
            return structured["result"]
        return structured
    if content_list and hasattr(content_list[0], "text"):
        try:
            return json.loads(content_list[0].text)
        except json.JSONDecodeError:
            return {"text": content_list[0].text}
    return {"text": str(content_list)}


def save(name: str, data) -> Path:
    p = VERIFY / f"{name}.json"
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    return p


async def main() -> int:
    results: dict[str, dict] = {}

    # 1) ping
    print("[1/13] ping ...", flush=True)
    r = await call("ping", {})
    results["ping"] = {"ok": r.get("server") == "cp-mcp", "version": r.get("version")}
    save("01_ping", r)

    # 2) parse_product_url
    print("[2/13] parse_product_url ...", flush=True)
    r = await call(
        "parse_product_url",
        {
            "url": "https://www.comprasparaguai.com.br/notebook-apple-macbook-air-2025-apple-m4-memoria-16gb-ssd-512gb-136_59619/"
        },
    )
    results["parse_product_url"] = {"ok": r.get("ok") and r.get("product_id") == 59619}
    save("02_parse_product_url", r)

    # 3) search_products (default new-only filter active)
    print("[3/13] search_products: 'iphone 16 pro' ...", flush=True)
    r = await call("search_products", {"query": "iphone 16 pro", "page": 1})
    results["search_products"] = {
        "ok": r.get("total_results", 0) > 0 and len(r.get("products", [])) >= 1,
        "filtered_out": r.get("filtered_out"),
        "kept": len(r.get("products", [])),
    }
    save("03_search_products", r)

    # 4) resolve_query
    print("[4/13] resolve_query: MBA M4 16/512 13.6'' ...", flush=True)
    r = await call(
        "resolve_query",
        {
            "family": "macbook-air",
            "chip": "m4",
            "ram_gb": 16,
            "storage_gb": 512,
            "screen_inches": 13.6,
        },
    )
    cands = r.get("candidates") or []
    top = cands[0] if cands else None
    results["resolve_query"] = {
        "ok": bool(top) and top["score"] >= 15 and top["card"]["product_id"] == 59619,
        "top_score": top["score"] if top else None,
        "top_id": top["card"]["product_id"] if top else None,
    }
    save("04_resolve_query", r)
    mba_id = top["card"]["product_id"]
    mba_slug = top["card"]["slug"]

    # 5) get_product
    print(f"[5/13] get_product: {mba_id} ...", flush=True)
    r = await call("get_product", {"product_id": mba_id, "slug": mba_slug})
    results["get_product"] = {
        "ok": r.get("product_id") == mba_id and r.get("brand") == "Apple",
        "offers": len(r.get("offers", [])),
        "specs": len(r.get("specifications", [])),
        "history": len(r.get("price_history", [])),
    }
    save("05_get_product", r)

    # 6) get_offers
    print("[6/13] get_offers ...", flush=True)
    r = await call("get_offers", {"product_id": mba_id, "slug": mba_slug})
    results["get_offers"] = {
        "ok": isinstance(r, list) and len(r) >= 5 and r[0]["price_usd"] <= r[-1]["price_usd"],
        "count": len(r) if isinstance(r, list) else 0,
    }
    save("06_get_offers", r)

    # 7) get_price_history
    print("[7/13] get_price_history ...", flush=True)
    r = await call("get_price_history", {"product_id": mba_id, "slug": mba_slug})
    results["get_price_history"] = {
        "ok": isinstance(r, list) and len(r) >= 6,
        "months": len(r) if isinstance(r, list) else 0,
    }
    save("07_get_price_history", r)

    # 8) compare_prices
    print("[8/13] compare_prices ...", flush=True)
    r = await call("compare_prices", {"product_id": mba_id, "slug": mba_slug})
    results["compare_prices"] = {
        "ok": r.get("cheapest_usd") and len(r.get("by_store", [])) > 1,
        "cheapest_usd": r.get("cheapest_usd"),
        "cheapest_store": r.get("cheapest_store"),
    }
    save("08_compare_prices", r)

    # 9) find_best_deal
    print("[9/13] find_best_deal ...", flush=True)
    r = await call(
        "find_best_deal",
        {
            "family": "macbook-air",
            "chip": "m4",
            "ram_gb": 16,
            "storage_gb": 512,
            "screen_inches": 13.6,
        },
    )
    results["find_best_deal"] = {
        "ok": r.get("ok") and r.get("best_offer", {}).get("price_usd"),
        "store": r.get("best_offer", {}).get("store_name"),
        "price_usd": r.get("best_offer", {}).get("price_usd"),
        "price_brl": r.get("best_offer", {}).get("price_brl"),
    }
    save("09_find_best_deal", r)

    # 10) watch_price
    print("[10/13] watch_price ...", flush=True)
    r = await call(
        "watch_price",
        {"product_id": mba_id, "slug": mba_slug, "target_usd": 1000.0},
    )
    results["watch_price"] = {
        "ok": "current_usd" in r and "pct_of_band" in r,
        "current_usd": r.get("current_usd"),
        "target_met": r.get("target_met"),
        "note": r.get("note"),
    }
    save("10_watch_price", r)

    # 11) optimise_basket — 4 items
    print("[11/13] optimise_basket: 4-item buy list ...", flush=True)
    items = [
        {
            "family": "macbook-air",
            "chip": "m4",
            "ram_gb": 16,
            "storage_gb": 512,
            "screen_inches": 13.6,
            "label": 'MacBook Air M4 16GB/512GB 13.6"',
        },
        {"intent_text": "iphone 16 pro max 256", "label": "iPhone 16 Pro Max 256GB"},
        {"intent_text": "samsung 990 pro 2tb", "label": "Samsung 990 Pro 2TB NVMe"},
        {"intent_text": "rtx 5070 ti", "label": "RTX 5070 Ti"},
    ]
    r = await call("optimise_basket", {"items": items})
    results["optimise_basket"] = {
        "ok": r.get("min_total_usd") and len(r.get("frontier", [])) >= 1,
        "min_total_usd": r.get("min_total_usd"),
        "min_total_stores": r.get("min_total_stores_used"),
        "single_store_total_usd": r.get("single_store_total_usd"),
        "frontier_steps": [
            (opt["stores_used"], opt["total_usd"]) for opt in r.get("frontier", [])
        ],
    }
    save("11_optimise_basket", r)

    # Pick the unconstrained-optimum frontier point for the buy-list demo
    optimum = max(r["frontier"], key=lambda o: o["stores_used"])

    # 12) optimise_within_stores — restrict to stores already in the optimum
    chosen_stores = [v["store_name"] for v in optimum["solution"]["visits"]]
    print(f"[12/13] optimise_within_stores: {chosen_stores} ...", flush=True)
    r2 = await call(
        "optimise_within_stores",
        {"items": items, "stores": chosen_stores},
    )
    results["optimise_within_stores"] = {
        "ok": r2.get("feasible") and r2.get("total_usd"),
        "total_usd": r2.get("total_usd"),
        "total_brl": r2.get("total_brl"),
        "stores_used": r2.get("stores_used"),
    }
    save("12_optimise_within_stores", r2)

    # 13) format_basket × 2 (markdown + whatsapp)
    print("[13/13] format_basket (markdown + whatsapp) ...", flush=True)
    md = await call("format_basket", {"solution": optimum, "format": "markdown"})
    wa = await call("format_basket", {"solution": optimum, "format": "whatsapp"})
    results["format_basket_markdown"] = {"ok": "Lista de compras" in md.get("content", "")}
    results["format_basket_whatsapp"] = {"ok": "TOTAL FINAL" in wa.get("content", "")}

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    md_path = BASKETS / f"buylist-{ts}.md"
    wa_path = BASKETS / f"buylist-{ts}.txt"
    basket_json = BASKETS / f"buylist-{ts}.basket.json"
    md_path.write_text(md["content"])
    wa_path.write_text(wa["content"])
    basket_json.write_text(json.dumps(optimum, indent=2, ensure_ascii=False))

    save("13_format_basket_markdown", md)
    save("13_format_basket_whatsapp", wa)

    # Summary
    print()
    print("=" * 60)
    print("VERIFICATION SUMMARY")
    print("=" * 60)
    failures: list[str] = []
    for name, info in results.items():
        ok = info.get("ok")
        marker = "PASS" if ok else "FAIL"
        if not ok:
            failures.append(name)
        # Show one short line per tool, with the most useful field after status.
        extras = {k: v for k, v in info.items() if k != "ok"}
        extra_str = ", ".join(f"{k}={v}" for k, v in extras.items()) if extras else ""
        print(f"  [{marker}] {name:30s}  {extra_str[:120]}")
    print()
    print(f"Wrote {len(list(VERIFY.iterdir()))} verification JSONs to data/verify/")
    print(f"Buy list:")
    print(f"  markdown:  {md_path}")
    print(f"  whatsapp:  {wa_path}")
    print(f"  basket json (for Docker PDF): {basket_json}")
    print()
    if failures:
        print("FAILURES:", failures)
        return 1
    print("ALL 13 TOOLS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
