import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from .models import PurchaseRule


@dataclass(frozen=True)
class RetailerConfig:
    type: str
    name: str
    feed_path: str


@dataclass(frozen=True)
class NotificationConfig:
    type: str
    webhook_url: str = ""


@dataclass(frozen=True)
class Config:
    database_path: str
    poll_interval_seconds: float
    poll_jitter_seconds: float
    daily_budget_cents: int
    dry_run: bool
    new_products_only: bool
    new_product_window_days: int
    rules: List[PurchaseRule]
    retailers: List[RetailerConfig]
    notification: NotificationConfig


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("%s must be a positive integer" % field)
    return value


def load_config(path: str) -> Config:
    config_path = Path(path).resolve()
    with config_path.open("r", encoding="utf-8") as handle:
        raw: Dict[str, Any] = json.load(handle)

    interval = float(raw.get("poll_interval_seconds", 30))
    jitter = float(raw.get("poll_jitter_seconds", 5))
    if interval < 10:
        raise ValueError("poll_interval_seconds must be at least 10")
    if jitter < 0:
        raise ValueError("poll_jitter_seconds cannot be negative")
    if raw.get("dry_run") is not True:
        raise ValueError("Only dry_run=true is supported until a real checkout adapter is configured")

    rules = [
        PurchaseRule(
            name=item["name"],
            keywords=tuple(word.casefold() for word in item["keywords"]),
            max_price_cents=_positive_int(item["max_price_cents"], "max_price_cents"),
            quantity=_positive_int(item.get("quantity", 1), "quantity"),
            product_types=tuple(item.get("product_types", ())),
            exclude_keywords=tuple(
                word.casefold() for word in item.get("exclude_keywords", ())
            ),
            allowed_sellers=tuple(
                seller.casefold() for seller in item.get("allowed_sellers", ())
            ),
            allowed_fulfillment=tuple(item.get("allowed_fulfillment", ())),
        )
        for item in raw["rules"]
    ]
    if not rules:
        raise ValueError("At least one purchase rule is required")

    retailers = []
    for item in raw["retailers"]:
        if item.get("type") not in ("mock", "target_feed"):
            raise ValueError("Unsupported retailer type: %s" % item.get("type"))
        feed_path = Path(item["feed_path"])
        if not feed_path.is_absolute():
            feed_path = config_path.parent / feed_path
        retailers.append(
            RetailerConfig(item["type"], item["name"], str(feed_path.resolve()))
        )
    if not retailers:
        raise ValueError("At least one retailer is required")

    database_path = Path(raw.get("database_path", "pokemon_bot.db"))
    if not database_path.is_absolute():
        database_path = config_path.parent / database_path

    notification_raw = raw.get("notification", {"type": "console"})
    notification_type = notification_raw.get("type", "console")
    if notification_type == "discord":
        env_name = notification_raw.get("webhook_env", "DISCORD_WEBHOOK_URL")
        webhook_url = os.environ.get(env_name, "")
        if not webhook_url:
            raise ValueError("Discord webhook environment variable %s is not set" % env_name)
        if not webhook_url.startswith("https://discord.com/api/webhooks/"):
            raise ValueError("Discord webhook URL must use discord.com/api/webhooks")
        notification = NotificationConfig("discord", webhook_url)
    elif notification_type == "console":
        notification = NotificationConfig("console")
    else:
        raise ValueError("Unsupported notification type: %s" % notification_type)

    return Config(
        database_path=str(database_path.resolve()),
        poll_interval_seconds=interval,
        poll_jitter_seconds=jitter,
        daily_budget_cents=_positive_int(raw["daily_budget_cents"], "daily_budget_cents"),
        dry_run=True,
        new_products_only=bool(raw.get("new_products_only", False)),
        new_product_window_days=_positive_int(
            raw.get("new_product_window_days", 30), "new_product_window_days"
        ),
        rules=rules,
        retailers=retailers,
        notification=notification,
    )
