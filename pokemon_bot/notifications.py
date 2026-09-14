import json
import urllib.request
from dataclasses import dataclass
from typing import Dict, Protocol

from .config import NotificationConfig
from .models import CheckoutResult, Decision
from .secrets import (
    read_environment_secret,
    read_keychain_secret,
    validate_discord_webhook_url,
)


@dataclass(frozen=True)
class Notification:
    title: str
    message: str
    url: str = ""
    price_cents: int = 0


class Notifier(Protocol):
    def send(self, notification: Notification) -> None:
        ...


class ConsoleNotifier:
    def send(self, notification: Notification) -> None:
        print(
            json.dumps(
                {
                    "event": "notification",
                    "title": notification.title,
                    "message": notification.message,
                    "url": notification.url,
                    "price_cents": notification.price_cents,
                },
                sort_keys=True,
            ),
            flush=True,
        )


class DiscordNotifier:
    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url

    @staticmethod
    def payload(notification: Notification) -> Dict[str, object]:
        fields = []
        if notification.price_cents:
            fields.append(
                {
                    "name": "Maximum matched total",
                    "value": "$%.2f" % (notification.price_cents / 100),
                    "inline": True,
                }
            )
        embed: Dict[str, object] = {
            "title": notification.title[:256],
            "description": notification.message[:4096],
            "color": 0xE3350D,
            "fields": fields,
        }
        if notification.url:
            embed["url"] = notification.url
        return {"embeds": [embed], "allowed_mentions": {"parse": []}}

    def send(self, notification: Notification) -> None:
        body = json.dumps(self.payload(notification)).encode("utf-8")
        request = urllib.request.Request(
            self.webhook_url,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "pokemon-card-monitor/0.1"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status not in (200, 204):
                raise RuntimeError("Discord returned HTTP %d" % response.status)


def build_notifier(config: NotificationConfig) -> Notifier:
    if config.type == "discord":
        if config.keychain_service:
            webhook_url = read_keychain_secret(
                config.keychain_service, config.keychain_account
            )
        else:
            webhook_url = read_environment_secret(config.webhook_env)
        return DiscordNotifier(validate_discord_webhook_url(webhook_url))
    return ConsoleNotifier()


def purchase_notification(
    decision: Decision, result: CheckoutResult
) -> Notification:
    action = "Open the product in your normal browser and complete any queue and checkout manually."
    return Notification(
        title="New sealed Pokemon TCG product matched",
        message="%s\n\n%s\n\nStatus: %s" % (decision.offer.title, action, result.status),
        url=decision.offer.url,
        price_cents=decision.total_cents,
    )
