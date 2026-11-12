"""Core sales-threshold alerting logic -- pure-function port of
ReadLastHourSKU / ReadLastHour / ReadLast4Hours (Module1.bas).

Deliberately has no Excel/filesystem side effects except load_orders_csv and
the two run_*_check entry points, so the counting/threshold logic is
unit-testable against plain in-memory rows.

Note: OldReadLast4Hours (a second, separate total-count check against the
same 4-hour file, with its own delete-the-file step) is NOT ported here --
it duplicates ReadLast4Hours's total-count logic and looks like leftover
code rather than an intentional second check. Revisit with the business
owner before adding an equivalent, don't assume it's needed.

Note: recipients are per-threshold-column, not per-time-window. The real
Emails sheet's column C (the higher threshold, e.g. 6 orders/SKU) has a
longer recipient list than column B (the lower threshold, e.g. 3) --
column C's extra two addresses are Zapier email-to-SMS and
email-to-phone-call triggers (feedcheckersms.../feedcheckphonecall...@
zapiermail.com), so crossing the higher threshold also fires an SMS + a
phone call, not just more emails. config/thresholds.json therefore stores
recipients per threshold bucket, not once per window.
"""

from __future__ import annotations

import csv
import os
from collections import Counter
from pathlib import Path
from typing import Callable

from notifications import render_product_breach_html

# 0-based column indices, matching the VBA's lettered columns (A, D).
COL_ORDER_ID = 0
COL_SUPPLIER_SKU = 3


def load_orders_csv(path: str | Path) -> list[dict]:
    """Parse an orders CSV into rows of {order_id, supplier_sku}.

    Column positions match TextFileColumnDataTypes in Module1.bas
    (col A = order id, col D = supplier SKU). A short/malformed row is
    skipped rather than raising, matching the VBA's tolerance of blank
    trailing cells.
    """
    rows: list[dict] = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        next(reader, None)  # header
        for raw in reader:
            if len(raw) <= max(COL_ORDER_ID, COL_SUPPLIER_SKU):
                continue
            rows.append({
                "order_id": raw[COL_ORDER_ID],
                "supplier_sku": raw[COL_SUPPLIER_SKU],
            })
    return rows


# Online SKUs starting with these are ignored everywhere (per-SKU AND total
# alerts): FBA* listings are Amazon-fulfilled, not shipped from our warehouse.
IGNORED_ONLINE_SKU_PREFIXES = ("FBA",)


def drop_ignored_online_skus(rows: list[dict]) -> list[dict]:
    return [
        row for row in rows
        if not str(row.get("supplier_sku") or "").upper().startswith(IGNORED_ONLINE_SKU_PREFIXES)
    ]


def map_to_our_sku(rows: list[dict], sku_lookup: dict[str, str]) -> list[dict]:
    """Attach our_sku to each row via the lookup; unmatched rows get "" (VBA leaves the cell blank)."""
    return [
        {**row, "our_sku": sku_lookup.get(row["supplier_sku"], "")}
        for row in rows
    ]


def count_by_sku(rows: list[dict]) -> dict[str, int]:
    """our_sku -> order count. Rows with no our_sku (unmatched in the lookup)
    are NOT pooled into a blank group -- they can't trigger a per-SKU alert,
    but still count toward the total-orders check."""
    counts: dict[str, int] = {}
    for row in rows:
        sku = row["our_sku"]
        if not sku:
            continue
        counts[sku] = counts.get(sku, 0) + 1
    return counts


def count_distinct_orders(rows: list[dict]) -> int:
    """Distinct order_id count, matching ReadLastHour's RemoveDuplicates on column A."""
    return len({row["order_id"] for row in rows})


def check_sku_thresholds(
    counts: dict[str, int],
    buckets: list[dict],
    exceptions: set[str],
) -> list[tuple[int, list[str], list[tuple[str, int]]]]:
    """For each bucket's sku_threshold (highest first), flag SKUs >= it and not excepted, then remove them.

    ``buckets`` is config["1h"]/config["4h"] -- each has its own
    sku_threshold AND its own recipients (the real sheet's column B vs
    column C recipient lists differ: column C, the higher threshold, also
    includes the Zapier SMS/phone-call trigger addresses that column B
    doesn't -- so recipients cannot be shared across thresholds).

    Matches the VBA's behavior of deleting a flagged row from the calc sheet
    so a lower threshold's pass doesn't re-flag the same SKU.
    Returns [(threshold, recipients, [(sku, count), ...]), ...] for buckets that matched anything.
    """
    remaining = dict(counts)
    results: list[tuple[int, list[str], list[tuple[str, int]]]] = []
    for bucket in sorted(buckets, key=lambda b: b["sku_threshold"], reverse=True):
        threshold = bucket["sku_threshold"]
        matched: list[tuple[str, int]] = []
        for sku in list(remaining.keys()):
            if remaining[sku] >= threshold and sku not in exceptions:
                matched.append((sku, remaining[sku]))
                del remaining[sku]
        if matched:
            matched.sort(key=lambda pair: pair[1], reverse=True)
            results.append((threshold, bucket["recipients"], matched))
    return results


def check_total_threshold(total_count: int, buckets: list[dict]) -> tuple[int, list[str]] | None:
    """Highest total_threshold crossed (with its own recipients), matching ReadLastHour's first-match-wins, Exit For."""
    for bucket in sorted(buckets, key=lambda b: b["total_threshold"], reverse=True):
        if total_count >= bucket["total_threshold"]:
            return bucket["total_threshold"], bucket["recipients"]
    return None


def _aggregate_sku_stats(rows: list[dict]) -> dict[str, dict]:
    """Per our_sku: display title (first non-blank seen), total quantity,
    total sales value (unit_price * quantity), and a Counter of orders by
    sales channel (e.g. {"Amazon UK": 3, "Temu UK": 1}) -- the product/
    channel-level detail the plain order-count fields don't carry. Rows
    missing title/quantity/unit_price/channel (e.g. the legacy CSV path,
    which never read those columns) default to "", 1, 0.0, "" respectively
    so this still works, just with less detail."""
    stats: dict[str, dict] = {}
    for row in rows:
        sku = row["our_sku"]
        entry = stats.setdefault(sku, {"title": "", "quantity": 0, "sales_value": 0.0, "channels": Counter(), "online_skus": {}})
        qty = row.get("quantity") or 1
        price = row.get("unit_price") or 0.0
        entry["quantity"] += qty
        entry["sales_value"] += qty * price
        if not entry["title"] and row.get("title"):
            entry["title"] = row["title"]
        online_sku = row.get("supplier_sku")
        if online_sku:
            entry["online_skus"][online_sku] = True  # ordered set
        channel = row.get("channel")
        if channel:
            entry["channels"][channel] += 1
    return stats


def _format_online_skus(entry: dict) -> str:
    """Marketplace/listing SKU(s) as sold online (ChannelAdvisor item Sku), comma-joined."""
    return ", ".join(entry.get("online_skus", {}))


def _format_channels(channels: Counter) -> str:
    """"Amazon UK (3), Temu UK (1)" -- most frequent channel first."""
    return ", ".join(f"{name} ({count})" for name, count in channels.most_common())


def _severity_for_threshold(threshold: int, buckets: list[dict], key: str) -> str:
    """HIGH for the highest-configured bucket (the one whose recipients also
    include the Zapier SMS/phone-call trigger addresses), MEDIUM for any
    lower bucket."""
    highest = max(bucket[key] for bucket in buckets)
    return "HIGH" if threshold >= highest else "MEDIUM"


def _sku_breach_items(matched: list[tuple[str, int]], stats: dict[str, dict]) -> list[dict]:
    items = []
    for sku, count in matched:
        s = stats.get(sku, {"title": "", "quantity": count, "sales_value": 0.0, "channels": Counter()})
        items.append({
            "sku": sku,
            "title": s["title"] or sku,
            "orders": count,
            "quantity": s["quantity"],
            "sales_value": s["sales_value"],
            "channels": _format_channels(s["channels"]),
            "online_sku": _format_online_skus(s),
        })
    return items


def _window_totals(stats: dict[str, dict], total_orders: int) -> dict:
    return {
        "orders": total_orders,
        "quantity": sum(s["quantity"] for s in stats.values()),
        "sales_value": sum(s["sales_value"] for s in stats.values()),
    }


def _format_sku_alert_body(threshold: int, items: list[dict]) -> str:
    lines = [f"The following SKUs have over {threshold} orders", "", "Product\tSKU\tOnline SKU\tChannel(s)\tOrders\tQty\tSales"]
    for item in items:
        lines.append(
            f"{item['title']}\t{item['sku']}\t{item.get('online_sku', '')}\t{item.get('channels', '')}\t{item['orders']}\t"
            f"{item['quantity']}\t£{item['sales_value']:.2f}"
        )
    return "\n".join(lines)


def _format_total_alert_body(threshold: int, total: int, totals: dict) -> str:
    return "\n".join([
        f"Total orders are greater than {threshold}",
        f"Total Orders: {total}",
        f"Total Quantity: {totals['quantity']}",
        f"Total Sales Value: £{totals['sales_value']:.2f}",
    ])


def evaluate_1h(
    raw_rows: list[dict],
    sku_lookup: dict[str, str],
    config: dict,
    exceptions: set[str],
    send_alert: Callable[..., None],
) -> None:
    """Port of ReadLastHourSKU + ReadLastHour's counting/alerting logic, no I/O.

    ``raw_rows`` is {order_id, supplier_sku} per row -- same shape
    load_orders_csv produces, so this works identically whether the rows
    came from a CSV file or ca_orders_client.fetch_recent_order_rows().
    """
    rows = map_to_our_sku(drop_ignored_online_skus(raw_rows), sku_lookup)
    buckets = config["1h"]
    stats = _aggregate_sku_stats(rows)

    counts = count_by_sku(rows)
    for threshold, recipients, matched in check_sku_thresholds(counts, buckets, exceptions):
        severity = _severity_for_threshold(threshold, buckets, "sku_threshold")
        items = _sku_breach_items(matched, stats)
        send_alert(
            f"[1h TH] [{severity}] CA Sales - Orders last hour",
            _format_sku_alert_body(threshold, items),
            to=recipients,
            html_body=render_product_breach_html(
                title="Orders last hour -- SKU threshold breach",
                severity=severity,
                subtitle=f"The following SKUs have over {threshold} orders in the last hour",
                items=items,
            ),
        )

    total = count_distinct_orders(rows)
    hit = check_total_threshold(total, buckets)
    if hit is not None:
        threshold, recipients = hit
        severity = _severity_for_threshold(threshold, buckets, "total_threshold")
        totals = _window_totals(stats, total)
        send_alert(
            f"[1h TH] [{severity}] CA Sales - Orders last 1 hour (Total: {total})",
            _format_total_alert_body(threshold, total, totals),
            to=recipients,
            html_body=render_product_breach_html(
                title="Orders last 1 hour -- Total threshold breach",
                severity=severity,
                subtitle=f"Total orders are greater than {threshold} (actual: {total})",
                items=[],
                totals=totals,
            ),
        )


def evaluate_4h(
    raw_rows: list[dict],
    sku_lookup: dict[str, str],
    config: dict,
    exceptions: set[str],
    send_alert: Callable[..., None],
) -> None:
    """Port of ReadLast4Hours's counting/alerting logic, no I/O (see evaluate_1h)."""
    rows = map_to_our_sku(drop_ignored_online_skus(raw_rows), sku_lookup)
    buckets = config["4h"]
    stats = _aggregate_sku_stats(rows)

    counts = count_by_sku(rows)
    for threshold, recipients, matched in check_sku_thresholds(counts, buckets, exceptions):
        severity = _severity_for_threshold(threshold, buckets, "sku_threshold")
        items = _sku_breach_items(matched, stats)
        send_alert(
            f"[4h TH] [{severity}] CA Sales - Orders last 4 hours",
            _format_sku_alert_body(threshold, items),
            to=recipients,
            html_body=render_product_breach_html(
                title="Orders last 4 hours -- SKU threshold breach",
                severity=severity,
                subtitle=f"The following SKUs have over {threshold} orders in the last 4 hours",
                items=items,
            ),
        )

    total = count_distinct_orders(rows)
    hit = check_total_threshold(total, buckets)
    if hit is not None:
        threshold, recipients = hit
        severity = _severity_for_threshold(threshold, buckets, "total_threshold")
        totals = _window_totals(stats, total)
        send_alert(
            f"[4h TH] [{severity}] CA Sales - Orders last 4 hours (Total: {total})",
            _format_total_alert_body(threshold, total, totals),
            to=recipients,
            html_body=render_product_breach_html(
                title="Orders last 4 hours -- Total threshold breach",
                severity=severity,
                subtitle=f"Total orders are greater than {threshold} (actual: {total})",
                items=[],
                totals=totals,
            ),
        )


def run_1h_check(
    csv_path: str | Path,
    sku_lookup: dict[str, str],
    config: dict,
    exceptions: set[str],
    send_alert: Callable[..., None],
    *,
    delete_after: bool = True,
) -> None:
    """CSV-file wrapper around evaluate_1h -- loads the file, evaluates, then
    deletes it (matching ReadLastHour's Kill after processing)."""
    raw_rows = load_orders_csv(csv_path)
    evaluate_1h(raw_rows, sku_lookup, config, exceptions, send_alert)
    if delete_after:
        os.remove(csv_path)


def run_4h_check(
    csv_path: str | Path,
    sku_lookup: dict[str, str],
    config: dict,
    exceptions: set[str],
    send_alert: Callable[..., None],
) -> None:
    """CSV-file wrapper around evaluate_4h. Does NOT delete the CSV afterwards
    -- matches ReadLast4Hours's own comment ("don't delete. Need this for the
    next one") and the decision not to port OldReadLast4Hours (see module
    docstring)."""
    raw_rows = load_orders_csv(csv_path)
    evaluate_4h(raw_rows, sku_lookup, config, exceptions, send_alert)
