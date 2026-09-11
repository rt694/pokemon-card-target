import argparse
import random
import signal
import time

from .checkout import DryRunCheckout
from .config import load_config
from .engine import log, scan_once
from .ingestion import ingest_target_file
from .notifications import Notification, build_notifier
from .retailers import MockRetailer, TargetFeedRetailer
from .storage import PurchaseStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor Pokemon card inventory")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--once", action="store_true", help="scan once and exit")
    parser.add_argument(
        "--test-notification", action="store_true", help="send a test notification and exit"
    )
    parser.add_argument(
        "--ingest-target",
        metavar="JSON_FILE",
        help="validate and merge Target offers from an authorized JSON export",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    if args.ingest_target:
        target_feeds = [item for item in config.retailers if item.type == "target_feed"]
        if len(target_feeds) != 1:
            parser.error("--ingest-target requires exactly one target_feed retailer")
        feed_path = target_feeds[0].feed_path
        quarantine_path = feed_path + ".quarantine.jsonl"
        accepted, rejected = ingest_target_file(
            args.ingest_target, feed_path, quarantine_path
        )
        log(
            "target_ingestion_finished",
            accepted=accepted,
            rejected=rejected,
            feed_path=feed_path,
            quarantine_path=quarantine_path if rejected else "",
        )
        return 0 if rejected == 0 else 2
    retailers = [
        TargetFeedRetailer(item.name, item.feed_path)
        if item.type == "target_feed"
        else MockRetailer(item.name, item.feed_path)
        for item in config.retailers
    ]
    checkout = DryRunCheckout()
    notifier = build_notifier(config.notification)
    if args.test_notification:
        notifier.send(
            Notification(
                title="Pokemon card monitor test",
                message="Notifications are configured correctly.",
                url="https://www.target.com/s?searchTerm=pokemon+cards",
            )
        )
        return 0
    store = PurchaseStore(config.database_path)
    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    log("bot_started", dry_run=config.dry_run, retailers=len(retailers))
    try:
        while not stopping:
            scan_once(config, retailers, store, checkout, notifier)
            if args.once:
                break
            delay = config.poll_interval_seconds + random.uniform(
                0, config.poll_jitter_seconds
            )
            time.sleep(delay)
    finally:
        store.close()
        log("bot_stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
