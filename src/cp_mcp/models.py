"""Pydantic models for parsed comprasparaguai.com.br data."""

from __future__ import annotations

from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class SortOrder(StrEnum):
    RELEVANCE = "relevancia"
    LOWEST_PRICE = "menor-preco"
    HIGHEST_PRICE = "maior-preco"
    PRODUCT_ASC = "produto-asc"
    PRODUCT_DESC = "produto-desc"
    NEWEST = "novos"


class Condition(StrEnum):
    NEW = "new"
    REFURBISHED = "recondicionado"
    CPO = "cpo"
    UNKNOWN = "unknown"


class ProductCard(BaseModel):
    """A single product as it appears in a search-result list."""

    model_config = ConfigDict(extra="ignore")

    product_id: int
    slug: str
    title: str
    url: str
    condition: Condition = Condition.UNKNOWN
    image_url: Optional[str] = None
    price_usd_from: Optional[float] = None
    price_brl_from: Optional[float] = None
    offer_count: Optional[int] = None
    description: Optional[str] = None


class Specification(BaseModel):
    key: str
    value: str


class Offer(BaseModel):
    """One store's offer on a product."""

    model_config = ConfigDict(extra="ignore")

    offer_id: Optional[int] = None
    store_name: str
    price_usd: float
    price_brl: Optional[float] = None
    store_url: Optional[str] = None
    whatsapp_phone: Optional[str] = None
    whatsapp_url: Optional[str] = None


class PricePoint(BaseModel):
    """One month of price-history data (site shows monthly minimum in US$)."""

    month: str  # "MM/YYYY"
    price_usd: float


class Product(BaseModel):
    """A product detail page, fully parsed."""

    model_config = ConfigDict(extra="ignore")

    product_id: int
    slug: str
    title: str
    url: str
    brand: Optional[str] = None
    category: Optional[str] = None
    condition: Condition = Condition.UNKNOWN
    image_url: Optional[str] = None
    description: Optional[str] = None
    specifications: list[Specification] = Field(default_factory=list)
    offers: list[Offer] = Field(default_factory=list)
    price_history: list[PricePoint] = Field(default_factory=list)


class Suggestion(BaseModel):
    label: str
    url: str
    count: Optional[int] = None


class SearchResult(BaseModel):
    """A page of search results."""

    model_config = ConfigDict(extra="ignore")

    query: str
    page: int
    total_results: Optional[int] = None
    total_pages: Optional[int] = None
    sort: SortOrder = SortOrder.RELEVANCE
    products: list[ProductCard] = Field(default_factory=list)
    suggestions: list[Suggestion] = Field(default_factory=list)


class StoreAddress(BaseModel):
    """One physical branch of a store."""

    model_config = ConfigDict(extra="ignore")

    address: str  # full inline address as the site presents it
    city: Optional[str] = None  # parsed trailing city ("Ciudad del Este", etc.)
    lat: Optional[float] = None
    lng: Optional[float] = None


class Store(BaseModel):
    """A store from the /lojas/ directory."""

    model_config = ConfigDict(extra="allow")

    name: str
    slug: Optional[str] = None
    url: Optional[str] = None
    addresses: list[StoreAddress] = Field(default_factory=list)
    phone: Optional[str] = None
    whatsapp_url: Optional[str] = None
    website_url: Optional[str] = None
    review_count: Optional[int] = None
    rating: Optional[float] = None
    is_premium: bool = False
