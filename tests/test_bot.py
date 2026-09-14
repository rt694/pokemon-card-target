import tempfile
import unittest
import plistlib
import subprocess
from pathlib import Path
from unittest.mock import patch

from pokemon_bot.checkout import DryRunCheckout
from pokemon_bot.models import Offer, PurchaseRule
from pokemon_bot.engine import deliver_pending_notifications
from pokemon_bot.ingestion import InvalidOffer, ingest_target_file, normalize_target_record
from pokemon_bot.notifications import DiscordNotifier, Notification
from pokemon_bot.config import NotificationConfig
from pokemon_bot.notifications import build_notifier
from pokemon_bot.retailers import TargetFeedRetailer
from pokemon_bot.rules import classify_product, evaluate, normalize_title
from pokemon_bot.service import generate_launch_agent
from pokemon_bot.secrets import read_keychain_secret, validate_discord_webhook_url
from pokemon_bot.storage import PurchaseStore


class RuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rule = PurchaseRule("ETB", ("prismatic", "trainer box"), 6000, 1)

    def test_matches_in_stock_offer_under_limit(self) -> None:
        offer = Offer("shop", "1", "Prismatic Elite Trainer Box", "url", 5499, "USD", True)
        decision = evaluate(offer, [self.rule])
        self.assertIsNotNone(decision)
        self.assertEqual(decision.total_cents, 5499)

    def test_rejects_overpriced_offer(self) -> None:
        offer = Offer("shop", "1", "Prismatic Trainer Box", "url", 7000, "USD", True)
        self.assertIsNone(evaluate(offer, [self.rule]))

    def test_classifies_changing_set_name_without_set_keyword(self) -> None:
        offer = Offer(
            "shop", "1", "Pokémon TCG: Future Set Pokémon Center Elite Trainer Box",
            "url", 5999, "USD", True, "sku-1", "tcg-cards"
        )
        self.assertEqual(classify_product(offer), "pokemon_center_etb")

    def test_rejects_tcg_accessory(self) -> None:
        offer = Offer(
            "shop", "1", "Pokémon TCG: Future Set Double Deck Box",
            "url", 1299, "USD", True, "sku-1", "tcg-cards"
        )
        self.assertIsNone(classify_product(offer))

    def test_normalizes_punctuation_and_accents(self) -> None:
        self.assertEqual(normalize_title("Pokémon—TCG"), "pokemon tcg")

    def test_target_rule_rejects_marketplace_seller(self) -> None:
        rule = PurchaseRule(
            "Target cards", (), 6000, 1, ("elite_trainer_box",), (), ("target",), ("shipping",)
        )
        offer = Offer(
            "target", "1", "Pokemon TCG: New Set Elite Trainer Box", "https://www.target.com/p/-/A-1",
            5499, "USD", True, "1", "trading-card-game", "Marketplace Seller", ("shipping",), 1
        )
        self.assertIsNone(evaluate(offer, [rule]))

    def test_target_rule_respects_purchase_limit(self) -> None:
        rule = PurchaseRule(
            "Target cards", (), 12000, 2, ("elite_trainer_box",), (), ("target",), ("shipping",)
        )
        offer = Offer(
            "target", "1", "Pokemon TCG: New Set Elite Trainer Box", "https://www.target.com/p/-/A-1",
            5499, "USD", True, "1", "trading-card-game", "Target", ("shipping",), 1
        )
        self.assertIsNone(evaluate(offer, [rule]))

    def test_target_feed_validates_target_url(self) -> None:
        retailer = TargetFeedRetailer("target", "fixtures/target_offers.json")
        offers = list(retailer.offers())
        self.assertEqual(offers[0].sku, "94681735")
        self.assertIn("shipping", offers[0].fulfillment_methods)


class StoreTests(unittest.TestCase):
    def test_duplicate_and_budget_guards(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db") as handle:
            store = PurchaseStore(handle.name)
            rule = PurchaseRule("ETB", ("box",), 6000, 1)
            first = evaluate(Offer("shop", "1", "Box", "url", 5000, "USD", True), [rule])
            second = evaluate(Offer("shop", "2", "Box", "url", 5000, "USD", True), [rule])
            self.assertIsNotNone(store.claim(first, 7500))
            self.assertIsNone(store.claim(first, 7500))
            self.assertIsNone(store.claim(second, 7500))
            store.close()

    def test_catalog_baseline_prevents_old_products_being_new(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db") as handle:
            store = PurchaseStore(handle.name)
            offer = Offer("shop", "1", "Pokemon TCG: Set Booster Pack", "url", 499, "USD", True, "sku-1", "tcg-cards")
            self.assertFalse(store.observe(offer, 30, eligible_on_first_seen=False))
            self.assertFalse(store.has_baseline("shop"))
            store.complete_baseline("shop")
            self.assertTrue(store.has_baseline("shop"))
            self.assertFalse(store.observe(offer, 30, eligible_on_first_seen=True))
            new_offer = Offer("shop", "2", "Pokemon TCG: New Set Booster Pack", "url", 499, "USD", True, "sku-2", "tcg-cards")
            self.assertTrue(store.observe(new_offer, 30, eligible_on_first_seen=True))
            store.close()

    def test_dry_run_checkout(self) -> None:
        rule = PurchaseRule("ETB", ("box",), 6000, 2)
        decision = evaluate(
            Offer(
                "shop", "1", "Box", "url", 5000, "USD", True,
                purchase_limit=2,
            ),
            [rule],
        )
        result = DryRunCheckout().purchase(decision)
        self.assertTrue(result.success)
        self.assertEqual(result.status, "dry_run")

    def test_scan_health_is_persisted(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db") as handle:
            store = PurchaseStore(handle.name)
            scan_id = store.begin_scan()
            store.finish_scan(scan_id, offers_seen=4, matches=1, retailer_errors=0)
            status = store.status_summary()
            self.assertEqual(status["last_scan"]["status"], "healthy")
            self.assertEqual(status["last_scan"]["offers_seen"], 4)
            self.assertEqual(status["pending_notifications"], 0)
            store.close()


class NotificationTests(unittest.TestCase):
    def test_discord_notification_has_clickable_link_and_no_mentions(self) -> None:
        payload = DiscordNotifier.payload(
            Notification("New product", "Open it", "https://www.pokemoncenter.com/product/1", 5999)
        )
        self.assertEqual(payload["embeds"][0]["url"], "https://www.pokemoncenter.com/product/1")
        self.assertEqual(payload["embeds"][0]["fields"][0]["value"], "$59.99")
        self.assertEqual(payload["allowed_mentions"], {"parse": []})

    def test_discord_url_validation_rejects_lookalike_host(self) -> None:
        with self.assertRaises(ValueError):
            validate_discord_webhook_url(
                "https://discord.com.example.org/api/webhooks/123/token"
            )

    def test_keychain_secret_is_used_to_build_notifier(self) -> None:
        config = NotificationConfig(
            "discord",
            keychain_service="pokemon-card-target.discord",
            keychain_account="webhook",
        )
        with patch(
            "pokemon_bot.notifications.read_keychain_secret",
            return_value="https://discord.com/api/webhooks/123/token",
        ):
            notifier = build_notifier(config)
        self.assertEqual(
            notifier.webhook_url, "https://discord.com/api/webhooks/123/token"
        )

    def test_missing_keychain_secret_has_clear_error(self) -> None:
        error = subprocess.CalledProcessError(44, ["security"])

        def failing_runner(*args, **kwargs):
            raise error

        with self.assertRaisesRegex(RuntimeError, "was not found in macOS Keychain"):
            read_keychain_secret("service", "account", runner=failing_runner)

    def test_failed_notification_remains_pending_for_retry(self) -> None:
        class FailingNotifier:
            def send(self, notification: Notification) -> None:
                raise RuntimeError("temporary failure")

        class RecordingNotifier:
            def __init__(self) -> None:
                self.sent = []

            def send(self, notification: Notification) -> None:
                self.sent.append(notification)

        with tempfile.NamedTemporaryFile(suffix=".db") as handle:
            store = PurchaseStore(handle.name)
            rule = PurchaseRule("ETB", ("box",), 6000, 1)
            decision = evaluate(
                Offer("shop", "1", "Box", "url", 5000, "USD", True), [rule]
            )
            attempt_id = store.claim(decision, 10000)
            store.enqueue_notification(attempt_id, "New product", "Open it", "url", 5000)
            deliver_pending_notifications(store, FailingNotifier())
            self.assertEqual(len(store.pending_notifications()), 1)
            recorder = RecordingNotifier()
            deliver_pending_notifications(store, recorder)
            self.assertEqual(len(recorder.sent), 1)
            self.assertEqual(store.pending_notifications(), [])
            store.close()


class TargetIngestionTests(unittest.TestCase):
    def valid_record(self):
        return {
            "tcin": "94681736",
            "title": "Pokemon TCG: New Set Booster Bundle",
            "url": "https://www.target.com/p/-/A-94681736",
            "price_cents": 2699,
            "in_stock": True,
            "category": "trading-card-game",
            "seller": "Target",
            "fulfillment_methods": ["pickup", "shipping", "shipping"],
            "purchase_limit": 1,
        }

    def test_normalizes_target_record_by_tcin(self) -> None:
        record = normalize_target_record(self.valid_record())
        self.assertEqual(record["id"], "target-94681736")
        self.assertEqual(record["sku"], "94681736")
        self.assertEqual(record["fulfillment_methods"], ["pickup", "shipping"])

    def test_rejects_lookalike_target_hostname(self) -> None:
        record = self.valid_record()
        record["url"] = "https://www.target.com.example.org/p/-/A-94681736"
        with self.assertRaises(InvalidOffer):
            normalize_target_record(record)

    def test_ingestion_merges_and_quarantines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = "%s/incoming.json" % directory
            feed = "%s/feed.json" % directory
            quarantine = "%s/rejected.jsonl" % directory
            invalid = self.valid_record()
            invalid["tcin"] = "bad"
            import json

            with open(source, "w", encoding="utf-8") as handle:
                json.dump([self.valid_record(), invalid], handle)
            accepted, rejected = ingest_target_file(source, feed, quarantine)
            self.assertEqual((accepted, rejected), (1, 1))
            with open(feed, "r", encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)[0]["sku"], "94681736")
            with open(quarantine, "r", encoding="utf-8") as handle:
                self.assertIn("tcin must contain exactly 8 digits", handle.read())


class ServiceTests(unittest.TestCase):
    def test_launch_agent_uses_absolute_paths_and_restarts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = "%s/monitor.plist" % directory
            generated = generate_launch_agent(directory, "%s/config.json" % directory, output)
            with open(generated, "rb") as handle:
                payload = plistlib.load(handle)
            self.assertTrue(payload["RunAtLoad"])
            self.assertTrue(payload["KeepAlive"])
            self.assertEqual(payload["WorkingDirectory"], str(Path(directory).resolve()))
            self.assertIn("--config", payload["ProgramArguments"])


if __name__ == "__main__":
    unittest.main()
