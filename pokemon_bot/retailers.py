import json
from pathlib import Path
from typing import Iterable, Protocol

from .models import Offer


class Retailer(Protocol):
    name: str

    def offers(self) -> Iterable[Offer]:
        ...


class MockRetailer:
    def __init__(self, name: str, feed_path: str) -> None:
        self.name = name
        self.feed_path = Path(feed_path)

    def offers(self) -> Iterable[Offer]:
        with self.feed_path.open("r", encoding="utf-8") as handle:
            items = json.load(handle)
        for item in items:
            yield Offer(
                retailer=self.name,
                offer_id=str(item["id"]),
                title=item["title"],
                url=item["url"],
                price_cents=int(item["price_cents"]),
                currency=item.get("currency", "USD"),
                in_stock=bool(item["in_stock"]),
                sku=str(item.get("sku", item["id"])),
                category=item.get("category", ""),
                seller=item.get("seller", ""),
                fulfillment_methods=tuple(item.get("fulfillment_methods", ())),
                purchase_limit=int(item.get("purchase_limit", 1)),
            )


class TargetFeedRetailer(MockRetailer):
    """Consumes Target offers exported by an approved source; never calls Target."""

    def offers(self) -> Iterable[Offer]:
        for offer in super().offers():
            if offer.retailer != "target":
                raise ValueError("Target feed retailer name must be 'target'")
            if not offer.sku:
                raise ValueError("Target offer is missing its TCIN")
            if not offer.url.startswith("https://www.target.com/"):
                raise ValueError("Target offer URL must use https://www.target.com/")
            if offer.purchase_limit < 1:
                raise ValueError("Target purchase_limit must be at least 1")
            yield offer
