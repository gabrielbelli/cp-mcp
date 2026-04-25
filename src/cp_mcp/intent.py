"""Query-intent resolution.

Given a natural-language intent like "MacBook Air M4 16/512" or a structured Intent,
- parse it into normalised fields,
- generate a sequence of search queries from broad to narrow,
- score returned product cards against the intent,
- iteratively relax tokens when no candidate scores high enough,
- return ranked candidates with their match scores and missing fields.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from pydantic import BaseModel, ConfigDict

from .models import Condition, ProductCard

# ---- Brand / category lexicon ---------------------------------------------

# Keep this small and pragmatic — extend when new categories are added.
BRAND_ALIASES: dict[str, str] = {
    "apple": "apple",
    "samsung": "samsung",
    "asus": "asus",
    "msi": "msi",
    "gigabyte": "gigabyte",
    "evga": "evga",
    "zotac": "zotac",
    "nvidia": "nvidia",
    "amd": "amd",
    "intel": "intel",
    "xiaomi": "xiaomi",
    "motorola": "motorola",
    "wd": "wd",
    "western": "wd",
    "kingston": "kingston",
    "crucial": "crucial",
    "corsair": "corsair",
}

# Phrases that hint product family / type.
FAMILY_HINTS: dict[str, str] = {
    "macbook air": "macbook-air",
    "macbook pro": "macbook-pro",
    "macbook neo": "macbook-neo",
    "iphone": "iphone",
    "ipad": "ipad",
    "rtx": "rtx",
    "geforce": "geforce",
    "radeon": "radeon",
    "990 pro": "990-pro",
    "980 pro": "980-pro",
    "970 evo": "970-evo",
    "990 evo": "990-evo",
    "evo plus": "evo-plus",
}


# ---- Regex extractors -----------------------------------------------------

_RAM_HINT_RE = re.compile(
    r"(\d{1,3})\s*gb\b\s*(ram|memoria|memória|de\s+ram)?", re.I
)
_STORAGE_RE = re.compile(r"(\d{1,4})\s*(tb|gb)\b", re.I)
_YEAR_RE = re.compile(r"\b(20\d{2})\b")
_SCREEN_RE = re.compile(r'(\d{1,2}(?:[.,]\d)?)\s*(?:"|inch|polegadas|pol)\b', re.I)
_INCHES_NUM_RE = re.compile(r'(\d{1,2})\.(\d)')  # "13.6" form

_APPLE_CHIP_RE = re.compile(r"\b([am]\d{1,2})\s*(pro|max|ultra)?\b", re.I)
_RTX_RE = re.compile(r"\brtx\s*(\d{4})\s*(super|ti|ti\s*super)?\b", re.I)
_GTX_RE = re.compile(r"\bgtx\s*(\d{4})\s*(super|ti)?\b", re.I)
_IPHONE_MODEL_RE = re.compile(
    r"iphone\s*(\d{1,2})\s*(pro\s*max|pro|plus|mini|e)?",
    re.I,
)
_GALAXY_S_RE = re.compile(
    r"galaxy\s*s\s*(\d{1,2})\s*(ultra|plus|fe)?",
    re.I,
)


def _norm(s: str) -> str:
    return s.lower().strip()


def _to_storage_gb(value: int, unit: str) -> int:
    return value * 1024 if unit.lower() == "tb" else value


# ---- Intent and Features --------------------------------------------------


class Intent(BaseModel):
    """Structured representation of what the user wants to buy."""

    model_config = ConfigDict(extra="ignore")

    raw: str = ""
    brand: Optional[str] = None
    family: Optional[str] = None  # e.g. "macbook-air", "iphone", "rtx"
    chip: Optional[str] = None  # e.g. "m4", "a18-pro", "rtx-4070"
    ram_gb: Optional[int] = None
    storage_gb: Optional[int] = None
    year: Optional[int] = None
    screen_inches: Optional[float] = None
    color: Optional[str] = None
    conditions: list[Condition] = []

    def known_fields(self) -> list[str]:
        return [
            f
            for f, v in self.model_dump().items()
            if f not in ("raw", "conditions") and v not in (None, "", [])
        ]


@dataclass
class Features:
    """Fields extracted from a product card's title/slug for scoring."""

    brand: Optional[str] = None
    family: Optional[str] = None
    chip: Optional[str] = None
    ram_gb: Optional[int] = None
    storage_gb: Optional[int] = None
    year: Optional[int] = None
    screen_inches: Optional[float] = None
    raw_text: str = ""


def parse_intent(text: str) -> Intent:
    """Extract a structured Intent from a free-text string."""

    raw = text or ""
    lower = _norm(raw)

    brand: Optional[str] = None
    for token, canonical in BRAND_ALIASES.items():
        if re.search(rf"\b{re.escape(token)}\b", lower):
            brand = canonical
            break

    family: Optional[str] = None
    if m := _IPHONE_MODEL_RE.search(lower):
        variant = m.group(2)
        variant_slug = re.sub(r"\s+", "-", variant.strip()) if variant else ""
        family = f"iphone-{m.group(1)}" + (f"-{variant_slug}" if variant_slug else "")
    elif m := _GALAXY_S_RE.search(lower):
        variant = m.group(2)
        variant_slug = variant.strip() if variant else ""
        family = f"galaxy-s{m.group(1)}" + (f"-{variant_slug}" if variant_slug else "")
    else:
        for hint, canonical in FAMILY_HINTS.items():
            if hint in lower:
                family = canonical
                break

    chip: Optional[str] = None
    if m := _RTX_RE.search(lower):
        suffix = (m.group(2) or "").replace(" ", "-").strip("-")
        chip = f"rtx-{m.group(1)}" + (f"-{suffix}" if suffix else "")
    elif m := _GTX_RE.search(lower):
        suffix = (m.group(2) or "").replace(" ", "-").strip("-")
        chip = f"gtx-{m.group(1)}" + (f"-{suffix}" if suffix else "")
    elif (
        brand == "apple"
        or (family and family.startswith("macbook"))
    ) and (m := _APPLE_CHIP_RE.search(lower)):
        suffix = (m.group(2) or "").strip().replace(" ", "-")
        chip = m.group(1).lower() + (f"-{suffix}" if suffix else "")

    # RAM vs storage disambiguation:
    # Look for explicit 'ram' / 'memoria' to anchor RAM. Storage is the *largest*
    # remaining capacity, or any TB value.
    ram_gb: Optional[int] = None
    storage_gb: Optional[int] = None
    capacities: list[tuple[int, str, int]] = []  # (value, unit, span_start)
    for m in _STORAGE_RE.finditer(lower):
        capacities.append((int(m.group(1)), m.group(2), m.start()))

    # Anchor RAM: capacity directly before "ram" / "memoria"
    for m in re.finditer(r"(\d{1,3})\s*gb\b\s*(?:ram|memoria|memória)", lower):
        ram_gb = int(m.group(1))
        break

    # Slash-form "16/512" — first number = RAM (GB), second = storage (GB if <=4096 else TB)
    if not ram_gb and (m := re.search(r"\b(\d{1,3})\s*/\s*(\d{1,4})\s*(tb|gb)?\b", lower)):
        ram_gb = int(m.group(1))
        unit = m.group(3) or ("tb" if int(m.group(2)) <= 8 else "gb")
        storage_gb = _to_storage_gb(int(m.group(2)), unit)

    if storage_gb is None:
        # Largest non-RAM capacity is storage
        candidates = [(v, u) for (v, u, _) in capacities if u.lower() == "tb" or v != ram_gb]
        if candidates:
            value, unit = max(candidates, key=lambda x: _to_storage_gb(x[0], x[1]))
            storage_gb = _to_storage_gb(value, unit)

    year: Optional[int] = None
    if m := _YEAR_RE.search(lower):
        year = int(m.group(1))

    screen: Optional[float] = None
    if m := _SCREEN_RE.search(lower):
        screen = float(m.group(1).replace(",", "."))

    # Conditions
    conds: list[Condition] = []
    if "recondicionado" in lower or "refurbish" in lower or "refurb" in lower:
        conds.append(Condition.REFURBISHED)
    if re.search(r"\bcpo\b", lower):
        conds.append(Condition.CPO)
    if not conds:
        conds = [Condition.NEW]

    return Intent(
        raw=raw,
        brand=brand,
        family=family,
        chip=chip,
        ram_gb=ram_gb,
        storage_gb=storage_gb,
        year=year,
        screen_inches=screen,
        conditions=conds,
    )


def extract_features(card: ProductCard) -> Features:
    """Pull comparable fields from a card's title/slug."""

    text = " ".join([card.title or "", card.slug or ""])
    lower = _norm(text)

    brand: Optional[str] = None
    for token, canonical in BRAND_ALIASES.items():
        if re.search(rf"\b{re.escape(token)}\b", lower):
            brand = canonical
            break

    family: Optional[str] = None
    if m := _IPHONE_MODEL_RE.search(lower):
        variant = m.group(2)
        variant_slug = re.sub(r"\s+", "-", variant.strip()) if variant else ""
        family = f"iphone-{m.group(1)}" + (f"-{variant_slug}" if variant_slug else "")
    elif m := _GALAXY_S_RE.search(lower):
        variant = m.group(2)
        variant_slug = variant.strip() if variant else ""
        family = f"galaxy-s{m.group(1)}" + (f"-{variant_slug}" if variant_slug else "")
    else:
        for hint, canonical in FAMILY_HINTS.items():
            if hint in lower:
                family = canonical
                break

    chip: Optional[str] = None
    if m := _RTX_RE.search(lower):
        suffix = (m.group(2) or "").replace(" ", "-").strip("-")
        chip = f"rtx-{m.group(1)}" + (f"-{suffix}" if suffix else "")
    elif m := _GTX_RE.search(lower):
        suffix = (m.group(2) or "").replace(" ", "-").strip("-")
        chip = f"gtx-{m.group(1)}" + (f"-{suffix}" if suffix else "")
    elif brand == "apple" and (m := _APPLE_CHIP_RE.search(lower)):
        suffix = (m.group(2) or "").strip().replace(" ", "-")
        chip = m.group(1).lower() + (f"-{suffix}" if suffix else "")

    # RAM: look for "memoria-16gb" or "memoria 16gb" patterns specifically
    ram_gb: Optional[int] = None
    for m in re.finditer(r"memoria[-\s]+(?:ram[-\s]+)?(\d{1,3})\s*gb\b", lower):
        ram_gb = int(m.group(1))
        break

    # Storage: SSD/HD slot in slug — "ssd-512gb" / "ssd-2tb" / direct capacity tokens
    storage_gb: Optional[int] = None
    if m := re.search(r"(?:ssd|hd|hdd|emmc|nvme)[-\s]+(\d{1,4})\s*(tb|gb)\b", lower):
        storage_gb = _to_storage_gb(int(m.group(1)), m.group(2))
    if storage_gb is None:
        # Phones / SSDs without ssd- prefix: pick the largest capacity that isn't the RAM
        for m in _STORAGE_RE.finditer(lower):
            value = int(m.group(1))
            unit = m.group(2)
            gb = _to_storage_gb(value, unit)
            if value == ram_gb and unit.lower() == "gb":
                continue
            if storage_gb is None or gb > storage_gb:
                storage_gb = gb

    year: Optional[int] = None
    if m := _YEAR_RE.search(lower):
        year = int(m.group(1))

    screen: Optional[float] = None
    if m := _SCREEN_RE.search(lower):
        screen = float(m.group(1).replace(",", "."))
    else:
        # Apple slugs encode "13.6" as a trailing 136 token; e.g. "...-ssd-512gb-136"
        if m := re.search(r"-(\d{2})(\d)\b", lower):
            screen = float(f"{m.group(1)}.{m.group(2)}")
        elif m := re.search(r"-(\d{2})\b", lower):
            n = int(m.group(1))
            if 10 <= n <= 18:
                screen = float(n)

    return Features(
        brand=brand,
        family=family,
        chip=chip,
        ram_gb=ram_gb,
        storage_gb=storage_gb,
        year=year,
        screen_inches=screen,
        raw_text=text,
    )


# ---- Scoring --------------------------------------------------------------


@dataclass
class MatchScore:
    score: int
    matched: list[str] = field(default_factory=list)
    mismatched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "matched": self.matched,
            "mismatched": self.mismatched,
            "missing": self.missing,
        }


# Field weights: positive on match, negative on mismatch, zero when intent doesn't specify.
WEIGHTS: dict[str, tuple[int, int]] = {
    # field: (match_bonus, mismatch_penalty)
    "family": (5, -8),
    "brand": (3, -3),
    "chip": (4, -4),
    "ram_gb": (3, -3),
    "storage_gb": (3, -3),
    "year": (2, -2),
    "screen_inches": (2, -1),
}


def score_card(card: ProductCard, intent: Intent) -> MatchScore:
    feats = extract_features(card)
    score = 0
    matched: list[str] = []
    mismatched: list[str] = []
    missing: list[str] = []

    def cmp(field_name: str, intent_val, feat_val, equal=lambda a, b: a == b) -> None:
        nonlocal score
        if intent_val in (None, ""):
            return
        bonus, penalty = WEIGHTS[field_name]
        if feat_val in (None, ""):
            missing.append(field_name)
            return
        if equal(intent_val, feat_val):
            score += bonus
            matched.append(field_name)
        else:
            score += penalty
            mismatched.append(field_name)

    cmp("family", intent.family, feats.family)
    cmp("brand", intent.brand, feats.brand)
    cmp("chip", intent.chip, feats.chip)
    cmp("ram_gb", intent.ram_gb, feats.ram_gb)
    cmp("storage_gb", intent.storage_gb, feats.storage_gb)
    cmp("year", intent.year, feats.year)
    cmp(
        "screen_inches",
        intent.screen_inches,
        feats.screen_inches,
        equal=lambda a, b: abs(float(a) - float(b)) < 0.5,
    )
    return MatchScore(score=score, matched=matched, mismatched=mismatched, missing=missing)


# ---- Query strategies + relaxation ----------------------------------------


def query_strategies(intent: Intent) -> list[str]:
    """Generate progressively narrower search queries.

    Order: broad -> specific. The resolver tries them broad-first; the first
    strategy that produces good matches wins.
    """

    parts: list[list[str]] = []

    base: list[str] = []
    if intent.family:
        base.append(intent.family.replace("-", " "))
    elif intent.brand:
        base.append(intent.brand)

    # 1) brand + family
    s1 = []
    if intent.brand:
        s1.append(intent.brand)
    s1 += base
    if s1:
        parts.append(s1)

    # 2) + chip
    if intent.chip:
        s2 = list(s1) + [intent.chip.replace("-", " ")]
        parts.append(s2)

    # 3) + ram or storage (whichever is set)
    if intent.chip and (intent.ram_gb or intent.storage_gb):
        s3 = list(parts[-1])
        if intent.ram_gb:
            s3.append(f"{intent.ram_gb}gb")
        if intent.storage_gb:
            s3.append(_format_storage(intent.storage_gb))
        parts.append(s3)

    # Dedup while preserving order, drop empties.
    seen: set[str] = set()
    queries: list[str] = []
    for p in parts:
        q = " ".join(t for t in p if t).strip()
        if q and q not in seen:
            seen.add(q)
            queries.append(q)
    if not queries and intent.raw:
        queries.append(intent.raw)
    return queries


def _format_storage(gb: int) -> str:
    if gb % 1024 == 0:
        return f"{gb // 1024}tb"
    return f"{gb}gb"


# ---- Public orchestration result types ------------------------------------


class ResolvedCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    card: ProductCard
    score: int
    matched: list[str]
    mismatched: list[str]
    missing: list[str]


class ResolveResult(BaseModel):
    intent: Intent
    tried_queries: list[str]
    candidates: list[ResolvedCandidate]
    note: Optional[str] = None


def rank_candidates(
    cards: Iterable[ProductCard], intent: Intent, *, min_score: int = 5
) -> list[ResolvedCandidate]:
    out: list[ResolvedCandidate] = []
    for card in cards:
        if intent.conditions and card.condition not in (
            *intent.conditions,
            Condition.UNKNOWN,
        ):
            continue
        m = score_card(card, intent)
        if m.score < min_score:
            continue
        out.append(
            ResolvedCandidate(
                card=card,
                score=m.score,
                matched=m.matched,
                mismatched=m.mismatched,
                missing=m.missing,
            )
        )
    out.sort(key=lambda c: (-c.score, c.card.price_usd_from or 1e9))
    return out
