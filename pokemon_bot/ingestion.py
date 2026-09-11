import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple
from urllib.parse import urlparse


ALLOWED_FULFILLMENT = {"shipping", "pickup", "delivery"}


class InvalidOffer(ValueError):
    pass


def normalize_target_record(raw: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise InvalidOffer("record must be a JSON object")
    tcin = str(raw.get("tcin") or raw.get("sku") or "")
    if not re.fullmatch(r"\d{8}", tcin):
        raise InvalidOffer("tcin must contain exactly 8 digits")

    title = raw.get("title")
    if not isinstance(title, str) or not title.strip():
        raise InvalidOffer("title must be a non-empty string")

    url = raw.get("url")
    if not isinstance(url, str):
        raise InvalidOffer("url must be a string")
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ("target.com", "www.target.com"):
        raise InvalidOffer("url must be an HTTPS target.com URL")
    if "/p/" not in parsed.path:
        raise InvalidOffer("url must be a Target product URL")

    price = raw.get("price_cents")
    if isinstance(price, bool) or not isinstance(price, int) or price <= 0:
        raise InvalidOffer("price_cents must be a positive integer")
    if not isinstance(raw.get("in_stock"), bool):
        raise InvalidOffer("in_stock must be true or false")

    seller = raw.get("seller")
    if not isinstance(seller, str) or not seller.strip():
        raise InvalidOffer("seller must be a non-empty string")
    methods = raw.get("fulfillment_methods")
    if not isinstance(methods, list) or not methods:
        raise InvalidOffer("fulfillment_methods must be a non-empty list")
    if any(method not in ALLOWED_FULFILLMENT for method in methods):
        raise InvalidOffer("unsupported fulfillment method")

    limit = raw.get("purchase_limit", 1)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise InvalidOffer("purchase_limit must be a positive integer")
    category = raw.get("category")
    if category not in ("trading-card-game", "tcg-cards"):
        raise InvalidOffer("category must identify trading card games")

    return {
        "id": "target-%s" % tcin,
        "tcin": tcin,
        "sku": tcin,
        "title": title.strip(),
        "url": url,
        "price_cents": price,
        "currency": "USD",
        "in_stock": raw["in_stock"],
        "category": category,
        "seller": seller.strip(),
        "fulfillment_methods": sorted(set(methods)),
        "purchase_limit": limit,
    }


def _records(value: Any) -> Iterable[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    raise InvalidOffer("input must be one object or a list of objects")


def _read_existing(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, list):
        raise InvalidOffer("destination feed must contain a JSON list")
    return [normalize_target_record(record) for record in value]


def _atomic_write(path: Path, records: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(records, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def ingest_target_file(
    input_path: str, feed_path: str, quarantine_path: str
) -> Tuple[int, int]:
    source = Path(input_path)
    destination = Path(feed_path)
    with source.open("r", encoding="utf-8") as handle:
        incoming = json.load(handle)

    valid: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for raw in _records(incoming):
        try:
            valid.append(normalize_target_record(raw))
        except (InvalidOffer, TypeError) as error:
            rejected.append(
                {
                    "rejected_at": datetime.now(timezone.utc).isoformat(),
                    "error": str(error),
                    "record": raw,
                }
            )

    existing = {record["sku"]: record for record in _read_existing(destination)}
    for record in valid:
        existing[record["sku"]] = record
    _atomic_write(destination, sorted(existing.values(), key=lambda item: item["sku"]))

    if rejected:
        quarantine = Path(quarantine_path)
        quarantine.parent.mkdir(parents=True, exist_ok=True)
        with quarantine.open("a", encoding="utf-8") as handle:
            for record in rejected:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
    return len(valid), len(rejected)
