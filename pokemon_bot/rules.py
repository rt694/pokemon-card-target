import re
import unicodedata
from typing import Iterable, Optional

from .models import Decision, Offer, PurchaseRule


PRODUCT_PATTERNS = (
    ("pokemon_center_etb", ("pokemon center elite trainer box",)),
    ("elite_trainer_box", ("elite trainer box",)),
    ("booster_display", ("booster display box", "booster box")),
    ("booster_bundle", ("booster bundle",)),
    ("sleeved_booster", ("sleeved booster pack",)),
    ("booster_pack", ("booster pack",)),
    ("ultra_premium_collection", ("ultra premium collection",)),
    ("collection", ("collection",)),
    ("tin", ("tin",)),
)


def normalize_title(value: str) -> str:
    value = re.sub(r"[\W_]+", " ", value.casefold(), flags=re.UNICODE)
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def classify_product(offer: Offer) -> Optional[str]:
    title = normalize_title(offer.title)
    category = normalize_title(offer.category)
    is_tcg = "pokemon tcg" in title or "pokemon trading card game" in title
    if category and "tcg" not in category and "trading card" not in category:
        return None
    if not is_tcg:
        return None
    # Accessory names often contain words such as "deck box" or "sleeves" but no packs.
    accessory_words = ("deck box", "sleeves", "playmat", "binder", "portfolio", "dice")
    if any(word in title for word in accessory_words):
        return None
    for product_type, phrases in PRODUCT_PATTERNS:
        if any(phrase in title for phrase in phrases):
            return product_type
    return None


def evaluate(offer: Offer, rules: Iterable[PurchaseRule]) -> Optional[Decision]:
    if not offer.in_stock or offer.currency != "USD" or offer.price_cents <= 0:
        return None
    title = normalize_title(offer.title)
    product_type = classify_product(offer)
    for rule in rules:
        keywords = tuple(normalize_title(keyword) for keyword in rule.keywords)
        exclusions = tuple(normalize_title(word) for word in rule.exclude_keywords)
        allowed_type = not rule.product_types or product_type in rule.product_types
        allowed_seller = (
            not rule.allowed_sellers or offer.seller.casefold() in rule.allowed_sellers
        )
        allowed_fulfillment = (
            not rule.allowed_fulfillment
            or bool(set(rule.allowed_fulfillment) & set(offer.fulfillment_methods))
        )
        if (
            allowed_type
            and allowed_seller
            and allowed_fulfillment
            and rule.quantity <= offer.purchase_limit
            and all(keyword in title for keyword in keywords)
            and not any(word in title for word in exclusions)
        ):
            total = offer.price_cents * rule.quantity
            if offer.price_cents <= rule.max_price_cents:
                return Decision(offer=offer, rule=rule, total_cents=total)
    return None
