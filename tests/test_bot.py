import tempfile
import unittest

from pokemon_bot.checkout import DryRunCheckout
from pokemon_bot.models import Offer, PurchaseRule
from pokemon_bot.engine import deliver_pending_notifications
from pokemon_bot.notifications import DiscordNotifier, Notification
from pokemon_bot.retailers import TargetFeedRetailer
from pokemon_bot.rules import classify_product, evaluate, normalize_title
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


class NotificationTests(unittest.TestCase):
    def test_discord_notification_has_clickable_link_and_no_mentions(self) -> None:
        payload = DiscordNotifier.payload(
            Notification("New product", "Open it", "https://www.pokemoncenter.com/product/1", 5999)
        )
        self.assertEqual(payload["embeds"][0]["url"], "https://www.pokemoncenter.com/product/1")
        self.assertEqual(payload["embeds"][0]["fields"][0]["value"], "$59.99")
        self.assertEqual(payload["allowed_mentions"], {"parse": []})

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


if __name__ == "__main__":
    unittest.main()
