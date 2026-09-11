import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from .models import CheckoutResult, Decision, Offer


class PurchaseStore:
    def __init__(self, path: str) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS attempts (
                id INTEGER PRIMARY KEY,
                retailer TEXT NOT NULL,
                offer_id TEXT NOT NULL,
                rule_name TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                total_cents INTEGER NOT NULL,
                status TEXT NOT NULL,
                order_id TEXT NOT NULL DEFAULT '',
                detail TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(retailer, offer_id, rule_name)
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS observed_products (
                retailer TEXT NOT NULL,
                product_key TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                last_title TEXT NOT NULL,
                eligible_new INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(retailer, product_key)
            )
            """
        )
        columns = {
            row[1] for row in self.connection.execute("PRAGMA table_info(observed_products)")
        }
        if "eligible_new" not in columns:
            self.connection.execute(
                "ALTER TABLE observed_products ADD COLUMN eligible_new INTEGER NOT NULL DEFAULT 0"
            )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS retailer_state (
                retailer TEXT PRIMARY KEY,
                baseline_completed_at TEXT NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_outbox (
                id INTEGER PRIMARY KEY,
                attempt_id INTEGER NOT NULL UNIQUE,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                url TEXT NOT NULL,
                price_cents INTEGER NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                sent_at TEXT,
                FOREIGN KEY(attempt_id) REFERENCES attempts(id)
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS scan_runs (
                id INTEGER PRIMARY KEY,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                status TEXT NOT NULL,
                offers_seen INTEGER NOT NULL DEFAULT 0,
                matches INTEGER NOT NULL DEFAULT 0,
                retailer_errors INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self.connection.commit()

    def begin_scan(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.connection.execute(
            "INSERT INTO scan_runs(started_at, status) VALUES (?, 'running')", (now,)
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def finish_scan(
        self,
        scan_id: int,
        offers_seen: int,
        matches: int,
        retailer_errors: int,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        status = "healthy" if retailer_errors == 0 else "degraded"
        self.connection.execute(
            """UPDATE scan_runs SET completed_at = ?, status = ?, offers_seen = ?,
               matches = ?, retailer_errors = ? WHERE id = ?""",
            (now, status, offers_seen, matches, retailer_errors, scan_id),
        )
        self.connection.commit()

    def status_summary(self) -> Dict[str, object]:
        last_scan = self.connection.execute(
            """SELECT started_at, completed_at, status, offers_seen, matches,
                      retailer_errors FROM scan_runs ORDER BY id DESC LIMIT 1"""
        ).fetchone()
        pending = self.connection.execute(
            "SELECT COUNT(*) FROM notification_outbox WHERE sent_at IS NULL"
        ).fetchone()[0]
        observed = self.connection.execute(
            "SELECT COUNT(*) FROM observed_products"
        ).fetchone()[0]
        attempts = self.connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
        baselines = self.connection.execute(
            "SELECT retailer, baseline_completed_at FROM retailer_state ORDER BY retailer"
        ).fetchall()
        return {
            "last_scan": None
            if last_scan is None
            else {
                "started_at": last_scan[0],
                "completed_at": last_scan[1],
                "status": last_scan[2],
                "offers_seen": last_scan[3],
                "matches": last_scan[4],
                "retailer_errors": last_scan[5],
            },
            "pending_notifications": pending,
            "observed_products": observed,
            "purchase_attempts": attempts,
            "baselines": [
                {"retailer": retailer, "completed_at": completed_at}
                for retailer, completed_at in baselines
            ],
        }

    def has_baseline(self, retailer: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM retailer_state WHERE retailer = ?", (retailer,)
        ).fetchone()
        return row is not None

    def complete_baseline(self, retailer: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            "INSERT OR IGNORE INTO retailer_state(retailer, baseline_completed_at) VALUES (?, ?)",
            (retailer, now),
        )
        self.connection.commit()

    def observe(
        self, offer: Offer, window_days: int, eligible_on_first_seen: bool = True
    ) -> bool:
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        product_key = offer.sku or offer.offer_id
        row = self.connection.execute(
            """SELECT first_seen, eligible_new FROM observed_products
               WHERE retailer = ? AND product_key = ?""",
            (offer.retailer, product_key),
        ).fetchone()
        if row is None:
            self.connection.execute(
                """INSERT INTO observed_products
                   (retailer, product_key, first_seen, last_seen, last_title, eligible_new)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    offer.retailer,
                    product_key,
                    now,
                    now,
                    offer.title,
                    int(eligible_on_first_seen),
                ),
            )
            first_seen = now_dt
            eligible_new = eligible_on_first_seen
        else:
            first_seen = datetime.fromisoformat(row[0])
            eligible_new = bool(row[1])
            self.connection.execute(
                """UPDATE observed_products SET last_seen = ?, last_title = ?
                   WHERE retailer = ? AND product_key = ?""",
                (now, offer.title, offer.retailer, product_key),
            )
        self.connection.commit()
        return eligible_new and first_seen >= now_dt - timedelta(days=window_days)

    def claim(self, decision: Decision, daily_budget_cents: int) -> Optional[int]:
        now = datetime.now(timezone.utc).isoformat()
        today = now[:10]
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            spent = self.connection.execute(
                """SELECT COALESCE(SUM(total_cents), 0) FROM attempts
                   WHERE substr(created_at, 1, 10) = ?
                     AND status IN ('claimed', 'dry_run', 'purchased')""",
                (today,),
            ).fetchone()[0]
            if spent + decision.total_cents > daily_budget_cents:
                self.connection.rollback()
                return None
            cursor = self.connection.execute(
                """INSERT INTO attempts
                   (retailer, offer_id, rule_name, quantity, total_cents, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'claimed', ?, ?)""",
                (
                    decision.offer.retailer,
                    decision.offer.offer_id,
                    decision.rule.name,
                    decision.rule.quantity,
                    decision.total_cents,
                    now,
                    now,
                ),
            )
            self.connection.commit()
            return int(cursor.lastrowid)
        except sqlite3.IntegrityError:
            self.connection.rollback()
            return None
        except Exception:
            self.connection.rollback()
            raise

    def finish(self, attempt_id: int, result: CheckoutResult) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            "UPDATE attempts SET status = ?, order_id = ?, detail = ?, updated_at = ? WHERE id = ?",
            (result.status, result.order_id, result.detail, now, attempt_id),
        )
        self.connection.commit()

    def enqueue_notification(
        self,
        attempt_id: int,
        title: str,
        message: str,
        url: str,
        price_cents: int,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            """INSERT OR IGNORE INTO notification_outbox
               (attempt_id, title, message, url, price_cents, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (attempt_id, title, message, url, price_cents, now),
        )
        self.connection.commit()

    def pending_notifications(
        self, limit: int = 20
    ) -> List[Tuple[int, str, str, str, int]]:
        return self.connection.execute(
            """SELECT id, title, message, url, price_cents
               FROM notification_outbox WHERE sent_at IS NULL
               ORDER BY id LIMIT ?""",
            (limit,),
        ).fetchall()

    def mark_notification_sent(self, notification_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            """UPDATE notification_outbox
               SET sent_at = ?, attempts = attempts + 1, last_error = '' WHERE id = ?""",
            (now, notification_id),
        )
        self.connection.commit()

    def mark_notification_failed(self, notification_id: int, error: str) -> None:
        self.connection.execute(
            """UPDATE notification_outbox
               SET attempts = attempts + 1, last_error = ? WHERE id = ?""",
            (error[:1000], notification_id),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()
