import json
from typing import Iterable

from .checkout import Checkout
from .models import Decision
from .notifications import Notification, Notifier, purchase_notification
from .retailers import Retailer
from .rules import evaluate
from .storage import PurchaseStore
from .config import Config


def log(event: str, **fields: object) -> None:
    print(json.dumps({"event": event, **fields}, sort_keys=True), flush=True)


def scan_once(
    config: Config,
    retailers: Iterable[Retailer],
    store: PurchaseStore,
    checkout: Checkout,
    notifier: Notifier,
) -> int:
    deliver_pending_notifications(store, notifier)
    scan_id = store.begin_scan()
    purchases = 0
    offers_seen = 0
    retailer_errors = 0
    for retailer in retailers:
        had_baseline = store.has_baseline(retailer.name)
        scan_completed = False
        try:
            offers = retailer.offers()
            for offer in offers:
                offers_seen += 1
                eligible_new = store.observe(
                    offer,
                    config.new_product_window_days,
                    eligible_on_first_seen=had_baseline,
                )
                if config.new_products_only and not eligible_new:
                    continue
                decision = evaluate(offer, config.rules)
                if decision is None:
                    continue
                attempt_id = store.claim(decision, config.daily_budget_cents)
                if attempt_id is None:
                    log("purchase_skipped", retailer=offer.retailer, offer_id=offer.offer_id)
                    continue
                _checkout(attempt_id, decision, store, checkout, notifier)
                purchases += 1
            scan_completed = True
        except Exception as error:
            retailer_errors += 1
            log("retailer_error", retailer=retailer.name, error=repr(error))
        if scan_completed and not had_baseline:
            store.complete_baseline(retailer.name)
            log("catalog_baseline_completed", retailer=retailer.name)
    store.finish_scan(scan_id, offers_seen, purchases, retailer_errors)
    log(
        "scan_finished",
        offers_seen=offers_seen,
        matches=purchases,
        retailer_errors=retailer_errors,
    )
    return purchases


def deliver_pending_notifications(store: PurchaseStore, notifier: Notifier) -> None:
    for notification_id, title, message, url, price_cents in store.pending_notifications():
        try:
            notifier.send(Notification(title, message, url, price_cents))
            store.mark_notification_sent(notification_id)
        except Exception as error:
            store.mark_notification_failed(notification_id, repr(error))
            log("notification_failed", notification_id=notification_id, error=repr(error))


def _checkout(
    attempt_id: int,
    decision: Decision,
    store: PurchaseStore,
    checkout: Checkout,
    notifier: Notifier,
) -> None:
    try:
        result = checkout.purchase(decision)
    except Exception as error:
        from .models import CheckoutResult

        result = CheckoutResult(False, "failed", detail=repr(error))
    store.finish(attempt_id, result)
    notification = purchase_notification(decision, result)
    store.enqueue_notification(
        attempt_id,
        notification.title,
        notification.message,
        notification.url,
        notification.price_cents,
    )
    deliver_pending_notifications(store, notifier)
    log(
        "checkout_finished",
        retailer=decision.offer.retailer,
        offer_id=decision.offer.offer_id,
        rule=decision.rule.name,
        total_cents=decision.total_cents,
        status=result.status,
        detail=result.detail,
    )
