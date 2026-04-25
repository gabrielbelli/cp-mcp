"""Parse search-result pages from comprasparaguai.com.br."""

from __future__ import annotations

import re
from typing import Optional

from selectolax.parser import HTMLParser, Node

from ..models import Condition, ProductCard, SearchResult, SortOrder, Suggestion
from .common import abs_url, clean_text, detect_condition, first_int, parse_price, parse_product_path


def _attr(node: Optional[Node], name: str) -> Optional[str]:
    if node is None:
        return None
    return node.attributes.get(name)


def _text(node: Optional[Node]) -> str:
    if node is None:
        return ""
    return clean_text(node.text())


def _parse_card(node: Node) -> Optional[ProductCard]:
    """Parse a single .promocao-produtos-item element. Returns None if it's not a product."""

    link = node.css_first(".promocao-item-nome a") or node.css_first(".promocao-item-img a")
    href = _attr(link, "href")
    if not href:
        return None
    parsed = parse_product_path(href)
    if not parsed:
        return None  # Likely an ad slot reusing the wrapper
    slug, product_id = parsed

    img = node.css_first(".promocao-item-img img")
    image_url = _attr(img, "data-src") or _attr(img, "src")
    if image_url and image_url.endswith("loading-images.svg"):
        image_url = _attr(img, "data-src")
    title = _attr(img, "title") or _text(node.css_first(".promocao-item-nome a"))
    title = clean_text(title)

    desc_node = node.css_first(".promocao-item-caracteristicas")
    description = _text(desc_node).rstrip(". ") or None

    price_usd = parse_price(_text(node.css_first(".container-price .price-model span")))
    price_brl = parse_price(_text(node.css_first(".container-price .promocao-item-preco-text")))

    offer_count = first_int(_text(node.css_first(".ver-detalhes button")))

    return ProductCard(
        product_id=product_id,
        slug=slug,
        title=title,
        url=abs_url(href) or "",
        condition=Condition(detect_condition(title, slug)),
        image_url=image_url,
        price_usd_from=price_usd,
        price_brl_from=price_brl,
        offer_count=offer_count,
        description=description,
    )


def _parse_total_results(tree: HTMLParser) -> Optional[int]:
    span = tree.css_first(".content-header-category .content-span") or tree.css_first(".content-span")
    if not span:
        return None
    return first_int(_text(span))


def _parse_pagination(tree: HTMLParser) -> tuple[Optional[int], Optional[int]]:
    """Return (current_page, total_pages)."""

    pag = tree.css_first(".pagination")
    if not pag:
        return None, None
    current = pag.css_first(".current.page") or pag.css_first(".current")
    current_page = first_int(_text(current)) if current else None
    last = 1
    for a in pag.css(".page"):
        n = first_int(_text(a))
        if n is not None and n > last:
            last = n
    return current_page, last


def _parse_sort(tree: HTMLParser) -> SortOrder:
    sel = tree.css_first("select#id_ordem")
    if not sel:
        return SortOrder.RELEVANCE
    selected = sel.css_first("option[selected]")
    if selected is None:
        return SortOrder.RELEVANCE
    value = _attr(selected, "value")
    try:
        return SortOrder(value) if value else SortOrder.RELEVANCE
    except ValueError:
        return SortOrder.RELEVANCE


_SUGG_COUNT_RE = re.compile(r"\((\d[\d.]*)\)")


def _parse_suggestions(tree: HTMLParser) -> list[Suggestion]:
    box = tree.css_first(".js-suggestion-list")
    if not box:
        return []
    out: list[Suggestion] = []
    raw = box.html or ""
    counts: dict[str, int] = {}
    # Counts appear in plaintext like "(257), " between <li>s. Map by listing them in order.
    text_blob = clean_text(box.text(separator=" "))
    matched_counts = [int(c.replace(".", "")) for c in _SUGG_COUNT_RE.findall(text_blob)]
    for i, a in enumerate(box.css("a")):
        href = abs_url(_attr(a, "href"))
        if not href:
            continue
        label = _text(a)
        count = matched_counts[i] if i < len(matched_counts) else None
        out.append(Suggestion(label=label, url=href, count=count))
    return out


def parse_search_html(html: str, query: str = "", page: int = 1) -> SearchResult:
    tree = HTMLParser(html)

    # The query rendered in <h1> is authoritative if we weren't given one
    if not query:
        h1 = tree.css_first("h1.content-title")
        query = _text(h1)

    products: list[ProductCard] = []
    seen_ids: set[int] = set()

    container = tree.css_first(".row.resultados-busca") or tree
    for node in container.css(".promocao-produtos-item"):
        # Skip nested wrappers — we want top-level cards only.
        # Each real card has a `.promocao-produtos-item-box > .promocao-produtos-item-text` child.
        if node.css_first(".promocao-produtos-item-box") is None:
            continue
        card = _parse_card(node)
        if card is None:
            continue
        if card.product_id in seen_ids:
            continue
        seen_ids.add(card.product_id)
        products.append(card)

    current_page, total_pages = _parse_pagination(tree)

    return SearchResult(
        query=query,
        page=current_page or page,
        total_results=_parse_total_results(tree),
        total_pages=total_pages,
        sort=_parse_sort(tree),
        products=products,
        suggestions=_parse_suggestions(tree),
    )
