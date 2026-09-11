from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class Offer:
    retailer: str
    offer_id: str
    title: str
    url: str
    price_cents: int
    currency: str
    in_stock: bool
    sku: str = ""
    category: str = ""
    seller: str = ""
    fulfillment_methods: Tuple[str, ...] = ()
    purchase_limit: int = 1


@dataclass(frozen=True)
class PurchaseRule:
    name: str
    keywords: Tuple[str, ...]
    max_price_cents: int
    quantity: int
    product_types: Tuple[str, ...] = ()
    exclude_keywords: Tuple[str, ...] = ()
    allowed_sellers: Tuple[str, ...] = ()
    allowed_fulfillment: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Decision:
    offer: Offer
    rule: PurchaseRule
    total_cents: int


@dataclass(frozen=True)
class CheckoutResult:
    success: bool
    status: str
    order_id: str = ""
    detail: str = ""
