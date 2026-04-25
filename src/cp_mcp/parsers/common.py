"""Shared helpers for HTML parsing."""

from __future__ import annotations

import html as html_lib
import re
from typing import Optional
from urllib.parse import urljoin

BASE_URL = "https://www.comprasparaguai.com.br"

PRODUCT_PATH_RE = re.compile(r"^/([a-z0-9\-]+)_(\d+)/?$")


def abs_url(href: Optional[str]) -> Optional[str]:
    if not href:
        return None
    href = href.strip()
    if not href:
        return None
    return urljoin(BASE_URL + "/", href)


def parse_product_path(path: str) -> Optional[tuple[str, int]]:
    """Return (slug, product_id) for a product URL/path, or None if it's not one."""

    if not path:
        return None
    # Strip query/fragment
    path = path.split("#", 1)[0].split("?", 1)[0]
    # Drop scheme + host
    path = re.sub(r"^https?://[^/]+", "", path)
    if not path.startswith("/"):
        path = "/" + path
    if not path.endswith("/"):
        path = path + "/"
    m = PRODUCT_PATH_RE.match(path)
    if not m:
        return None
    return m.group(1), int(m.group(2))


_PRICE_PREFIX_RE = re.compile(r"^(US\$|R\$|U\$|USD|BRL)\s*", re.I)


def parse_price(text: Optional[str]) -> Optional[float]:
    """Parse a pt-BR formatted currency string into a float.

    Examples: "US$ 665,00" -> 665.0, "R$ 3.404,80" -> 3404.8, "US$&nbsp;1.250,00" -> 1250.0.
    Returns None on failure or empty input.
    """

    if not text:
        return None
    s = html_lib.unescape(text).replace("\xa0", " ").strip()
    s = _PRICE_PREFIX_RE.sub("", s)
    s = s.replace(" ", "")
    if not s:
        return None
    # pt-BR: '.' is thousands, ',' is decimal
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def clean_text(text: Optional[str]) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", html_lib.unescape(text)).strip()


_CPO_TOKEN_RE = re.compile(r"(?:^|[\s\-_/])cpo(?:[\s\-_/]|$)", re.I)


def detect_condition(*texts: Optional[str]) -> str:
    """Classify a product's condition from its title and/or slug.

    Returns one of: 'recondicionado', 'cpo', 'new'. Defaults to 'new' when no
    refurbished/CPO marker is found.
    """

    haystack = " ".join(t for t in texts if t).lower()
    if "recondicionado" in haystack:
        return "recondicionado"
    if _CPO_TOKEN_RE.search(haystack):
        return "cpo"
    return "new"


def first_int(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    m = re.search(r"\d[\d.]*", text)
    if not m:
        return None
    try:
        return int(m.group(0).replace(".", ""))
    except ValueError:
        return None
