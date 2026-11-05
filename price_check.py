"""Price-drop detection logic (Phase 4) -- pure functions, same style as
sales_pipeline.py, so the percentage math/selection is unit-testable
without real order data.

The actual purpose: catch a mispriced listing being exploited (bulk-bought
at a wrong price) by comparing today's lowest sale price per SKU against
the last price recorded for that SKU, whenever that was (price_tracking.py)
-- real transaction data, not a ChannelAdvisor catalog field. BuyItNowPrice/
RetailPrice were tried as a baseline and dropped: confirmed live that
BuyItNowPrice is unset on effectively the whole catalog and RetailPrice is
unreliable (shared placeholder value on some SKUs, lower than the real
sold price on others, missing for suffixed SKU variants). A SKU with no
prior recorded price at all (never seen before) isn't covered -- nothing
to compare against yet.

Deliberately never filters by config/exceptions.json -- a price mistake
can happen on any SKU, including ones normally excluded from the volume
thresholds for unrelated reasons.
"""

from __future__ import annotations


def build_today_price_snapshot(rows: list[dict]) -> tuple[dict[str, float], dict[str, dict]]:
    """Per non-blank our_sku in rows (post map_to_our_sku): the minimum
    unit_price seen in these rows, plus a title/channel for display.

    Returns (prices_by_sku, details_by_sku) where details_by_sku[sku] =
    {"title": ..., "channel": ...}. Meant to be called with one run's
    worth of rows (the same rows_4h already fetched for the 4h
    volume-threshold check).
    """
    prices: dict[str, float] = {}
    details: dict[str, dict] = {}
    for row in rows:
        sku = row.get("our_sku")
        if not sku:
            continue
        price = row.get("unit_price")
        if price is None:
            continue
        if sku not in prices or price < prices[sku]:
            prices[sku] = price
            details[sku] = {"title": row.get("title") or sku, "channel": row.get("channel") or ""}
    return prices, details


def check_and_update_price_tracking(
    today_prices: dict[str, float],
    tracking: dict[str, dict],
    today_date: str,
    threshold_percent: float,
) -> list[dict]:
    """For each SKU seen today, compare against price_tracking.py's stored
    record (sku -> {"unit_price", "last_updated_date"}) and decide what to
    do, per the three cases:

    1. No record at all for this SKU -- nothing to compare against yet,
       just record today's price as the new baseline.
    2. A record exists, dated before today -- this is the first sighting
       of this SKU today, so compare today's price against that stored
       price; flag if the drop is >= threshold_percent. Then overwrite the
       record with today's price/date either way, so it becomes the
       reference point for next time.
    3. A record exists, already dated today -- this SKU was already
       checked once today (by this run or an earlier one), so skip the
       comparison (that's the same-day dedup -- no separate ledger file
       needed). Still keep the LOWEST price seen today in the record,
       same "don't lose a lower price already seen" principle used
       elsewhere in this pipeline.

    Mutates ``tracking`` in place (the caller persists it afterwards via
    price_tracking.save_tracking) and returns the newly-flagged drops:
    [{"sku", "baseline_price", "today_price", "pct_drop", "baseline_source"}, ...]
    sorted by pct_drop descending.
    """
    drops: list[dict] = []
    for sku, price in today_prices.items():
        existing = tracking.get(sku)
        if existing is None:
            tracking[sku] = {"unit_price": price, "last_updated_date": today_date}
            continue

        if existing["last_updated_date"] != today_date:
            baseline_price = existing["unit_price"]
            if baseline_price > 0:
                pct_drop = (baseline_price - price) / baseline_price * 100
                if pct_drop >= threshold_percent:
                    drops.append({
                        "sku": sku,
                        "baseline_price": baseline_price,
                        "today_price": price,
                        "pct_drop": pct_drop,
                        "baseline_source": f"Last recorded {existing['last_updated_date']}",
                    })
            tracking[sku] = {"unit_price": price, "last_updated_date": today_date}
        else:
            if price < existing["unit_price"]:
                tracking[sku]["unit_price"] = price

    drops.sort(key=lambda item: item["pct_drop"], reverse=True)
    return drops
