"""Parse product detail pages from comprasparaguai.com.br."""

from __future__ import annotations

import ast
import re
from typing import Optional
from urllib.parse import parse_qs, urlparse

from selectolax.parser import HTMLParser, Node

from ..models import (
    Condition,
    Offer,
    PricePoint,
    Product,
    Specification,
)
from .common import abs_url, clean_text, detect_condition, parse_price, parse_product_path

# Per-offer trailing slug uses a double-underscore (e.g. "...silver__4967788/").
OFFER_PATH_RE = re.compile(r"__(\d+)/?$")
ADVERTISER_RE = re.compile(r"['\"]advertiser['\"]\s*:\s*['\"]([^'\"]+)['\"]")


def _attr(node: Optional[Node], name: str) -> Optional[str]:
    if node is None:
        return None
    return node.attributes.get(name)


def _text(node: Optional[Node]) -> str:
    if node is None:
        return ""
    return clean_text(node.text())


def _meta_content(tree: HTMLParser, prop: str) -> Optional[str]:
    node = tree.css_first(f'meta[property="{prop}"]') or tree.css_first(f'meta[name="{prop}"]')
    if node is None:
        return None
    return clean_text(node.attributes.get("content"))


def _parse_specs(tree: HTMLParser) -> list[Specification]:
    table = tree.css_first("table.table-details")
    if not table:
        return []
    out: list[Specification] = []
    for row in table.css("tr"):
        cells = row.css("td")
        if len(cells) >= 2:
            key = clean_text(cells[0].text())
            value = clean_text(cells[1].text())
            if key:
                out.append(Specification(key=key, value=value))
    return out


def _parse_history(tree: HTMLParser) -> list[PricePoint]:
    canvas = tree.css_first("canvas#grafico-modelo")
    if not canvas:
        return []
    raw = canvas.attributes.get("data-historico")
    if not raw:
        return []
    try:
        # The site embeds Python-dict-style with single quotes.
        data = ast.literal_eval(raw)
    except (SyntaxError, ValueError):
        return []
    out: list[PricePoint] = []
    if not isinstance(data, list):
        return []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        x = entry.get("x")
        y = entry.get("y")
        if x is None or y is None:
            continue
        try:
            out.append(PricePoint(month=str(x), price_usd=float(y)))
        except (TypeError, ValueError):
            continue
    return out


def _parse_offer(node: Node) -> Optional[Offer]:
    # Title link: skip ad/promo wrappers that don't carry an anchor inside .promocao-item-nome
    name_link = node.css_first(".promocao-item-nome a")
    if not name_link:
        return None
    href = _attr(name_link, "href") or ""

    # Price USD lives in <strong> inside the first .promocao-item-preco-oferta
    price_block = node.css_first(".promocao-item-preco-oferta")
    if not price_block:
        return None
    usd_node = price_block.css_first("strong") or price_block.css_first("span")
    price_usd = parse_price(_text(usd_node))
    if price_usd is None:
        return None
    brl_node = price_block.css_first(".promocao-item-preco-text")
    price_brl = parse_price(_text(brl_node))

    # Offer ID = data-id on the wishlist heart button.
    wishlist = node.css_first(".btn-add-lista-desejos")
    offer_id: Optional[int] = None
    raw_id = _attr(wishlist, "data-id")
    if raw_id and raw_id.isdigit():
        offer_id = int(raw_id)
    if offer_id is None:
        m = OFFER_PATH_RE.search(href)
        if m:
            offer_id = int(m.group(1))

    # Store name lives inside gtag(...) onclick payloads on the wishlist / whatsapp / redir buttons.
    store_name: Optional[str] = None
    for candidate in node.css("a[onclick]"):
        onclick = candidate.attributes.get("onclick") or ""
        m = ADVERTISER_RE.search(onclick)
        if m:
            store_name = clean_text(m.group(1))
            break
    if not store_name:
        return None

    # WhatsApp link
    whatsapp_url: Optional[str] = None
    whatsapp_phone: Optional[str] = None
    for a in node.css("a[href*='api.whatsapp.com']"):
        whatsapp_url = a.attributes.get("href")
        if whatsapp_url:
            qs = parse_qs(urlparse(whatsapp_url).query)
            phones = qs.get("phone")
            if phones:
                whatsapp_phone = phones[0]
            break

    # Outbound store URL
    store_url: Optional[str] = None
    redir = node.css_first("a.btn-store-redirect, a[href].btn-store-redirect")
    if redir is None:
        # Fall back to any external anchor with the gtag external_website_advertiser event
        for a in node.css("a[onclick]"):
            onclick = a.attributes.get("onclick") or ""
            if "external_website_advertiser" in onclick:
                redir = a
                break
    if redir is not None:
        store_url = redir.attributes.get("href")

    return Offer(
        offer_id=offer_id,
        store_name=store_name,
        price_usd=price_usd,
        price_brl=price_brl,
        store_url=store_url,
        whatsapp_phone=whatsapp_phone,
        whatsapp_url=whatsapp_url,
    )


def _parse_offers(tree: HTMLParser) -> list[Offer]:
    out: list[Offer] = []
    seen: set[tuple[Optional[int], str, float]] = set()
    for node in tree.css(".promocao-produtos-item"):
        # Search-card variants in this page (related products etc.) carry col-sm-12.
        cls = (node.attributes.get("class") or "").split()
        if "col-sm-12" in cls:
            continue
        offer = _parse_offer(node)
        if offer is None:
            continue
        key = (offer.offer_id, offer.store_name, offer.price_usd)
        if key in seen:
            continue
        seen.add(key)
        out.append(offer)
    return out


def _parse_breadcrumb_category(tree: HTMLParser) -> Optional[str]:
    crumb = tree.css_first(".breadcrumbs")
    if not crumb:
        return None
    links = crumb.css("a")
    if not links:
        return None
    return clean_text(links[-1].text())


def parse_product_html(
    html: str,
    *,
    product_id: int = 0,
    slug: str = "",
    url: str = "",
) -> Product:
    tree = HTMLParser(html)

    title_node = tree.css_first("h1")
    title = clean_text(title_node.text()) if title_node else ""
    if not title:
        title = _meta_content(tree, "og:title") or ""

    specs = _parse_specs(tree)
    spec_map = {s.key.lower(): s.value for s in specs}
    brand = spec_map.get("marca")

    category = _parse_breadcrumb_category(tree)
    image_url = _meta_content(tree, "og:image")
    description = _meta_content(tree, "og:description")

    history = _parse_history(tree)
    offers = _parse_offers(tree)

    if (not product_id or not slug) and url:
        parsed = parse_product_path(url)
        if parsed:
            slug = slug or parsed[0]
            product_id = product_id or parsed[1]

    condition = Condition(detect_condition(title, slug))

    return Product(
        product_id=product_id,
        slug=slug,
        title=title,
        url=abs_url(url) or url,
        brand=brand,
        category=category,
        condition=condition,
        image_url=image_url,
        description=description,
        specifications=specs,
        offers=offers,
        price_history=history,
    )
