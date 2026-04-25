"""Parse a single store's page (/lojas/<slug>/) — addresses + branches."""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

from selectolax.parser import HTMLParser, Node

from ..models import Store, StoreAddress
from .common import abs_url, clean_text

# Trailing-city detector. The site mentions only three "cidades" in nav.
_KNOWN_CITIES = (
    "Ciudad del Este",
    "Salto del Guaíra",
    "Salto del Guaira",
    "Pedro Juan Caballero",
    "Asunción",
    "Asuncion",
)


def slugify(value: str) -> str:
    """Lowercase + ASCII-fold + non-alnum→hyphen. Mirrors what the site uses for /lojas/<slug>/."""

    norm = unicodedata.normalize("NFKD", value or "")
    ascii_only = norm.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", ascii_only.lower())).strip("-")


def _attr(node: Optional[Node], name: str) -> Optional[str]:
    if node is None:
        return None
    return node.attributes.get(name)


def _split_city(text: str) -> tuple[str, Optional[str]]:
    """Pull the trailing city off an inline address string."""

    if not text:
        return text, None
    for city in _KNOWN_CITIES:
        if text.lower().endswith(city.lower()):
            return text[: -len(city)].rstrip(" -—,").strip(), city
    return text, None


def _parse_addresses(tree: HTMLParser) -> list[StoreAddress]:
    section = tree.css_first("#enderecos") or tree.css_first(".str-detail-storeslist")
    out: list[StoreAddress] = []
    if section is None:
        return out
    for li in section.css(".str-detail-storelist-item"):
        strong = li.css_first(".btn-accordion strong") or li.css_first("strong")
        text = clean_text(strong.text()) if strong else ""
        if not text:
            continue
        addr_text, city = _split_city(text)
        map_div = li.css_first("[data-lat]")
        lat = lng = None
        if map_div is not None:
            try:
                lat = float(_attr(map_div, "data-lat") or "")
            except (TypeError, ValueError):
                lat = None
            try:
                lng = float(_attr(map_div, "data-lng") or "")
            except (TypeError, ValueError):
                lng = None
        out.append(StoreAddress(address=addr_text or text, city=city, lat=lat, lng=lng))
    return out


def parse_store_directory_html(html: str) -> list[Store]:
    """Parse one page of /lojas/ — returns shallow Store summaries with review_count
    rolled into Store.phone? No — we attach review_count via the .phone field is hacky;
    instead, return a richer dict-like Store. We use a free-form `name` + `slug` plus
    a `review_count` carried on a sidecar dict via the model's extras.
    """

    tree = HTMLParser(html)
    out: list[Store] = []
    for node in tree.css(".str-results-group-body-item"):
        a = node.css_first("a[href^='/lojas/']")
        if a is None:
            continue
        href = _attr(a, "href") or ""
        # /lojas/<slug>/...
        m = re.search(r"^/lojas/([^/]+)/", href)
        slug = m.group(1) if m else None
        name_node = node.css_first("h2.establishment-name") or node.css_first("h2")
        name = clean_text(name_node.text()) if name_node else ""
        if not name:
            continue
        # Reviews count: "(25170 Avaliações)"
        votes_node = node.css_first(".establishment-votes")
        review_count: Optional[int] = None
        if votes_node is not None:
            m2 = re.search(r"\(\s*(\d[\d.,]*)\s*Avalia", votes_node.text())
            if m2:
                review_count = int(re.sub(r"[^\d]", "", m2.group(1)))
        # Stars width → 0..5
        stars_node = node.css_first(".str-col-reviews-stars-inner")
        rating: Optional[float] = None
        if stars_node is not None:
            style = _attr(stars_node, "style") or ""
            m3 = re.search(r"width:\s*([\d.]+)\s*%", style)
            if m3:
                rating = round(float(m3.group(1)) / 20.0, 2)
        cls = (node.attributes.get("class") or "").split()
        is_premium = "premium" in cls

        store = Store(name=name, slug=slug, url=abs_url(f"/lojas/{slug}/") if slug else None)
        # Stash directory-only signals on the model via extras (model_config allows extras).
        store_dict = store.model_dump()
        store_dict["review_count"] = review_count
        store_dict["rating"] = rating
        store_dict["is_premium"] = is_premium
        # Re-validate to keep type safety
        out.append(Store.model_validate(store_dict))
    return out


def parse_store_html(html: str, *, slug: Optional[str] = None) -> Store:
    tree = HTMLParser(html)
    name_node = tree.css_first("h1") or tree.css_first("h2.str-detail-section-tit")
    name = clean_text(name_node.text()) if name_node else ""

    site_node = tree.css_first("a.str-detail-website") or tree.css_first(".str-detail-cover a[href^='http']")
    website = _attr(site_node, "href")

    addresses = _parse_addresses(tree)
    return Store(
        name=name,
        slug=slug,
        url=abs_url(f"/lojas/{slug}/") if slug else None,
        addresses=addresses,
        website_url=website,
    )
