"""Persistent per-SKU price tracker for price_check.py's price-drop
detection (Phase 4) -- a single growing CSV, config/price_tracking.csv,
columns: sku, unit_price, last_updated_date.

Replaces the earlier day-dated-snapshot-file design (price_history.py,
removed): instead of needing a specific "yesterday" file, each SKU's
baseline is simply whatever price was last recorded for it, however long
ago that was -- so a SKU that goes a few days between sales still gets a
real comparison, not just ones that sold exactly yesterday. The date
column also doubles as the same-day dedup: price_check.
check_and_update_price_tracking skips the comparison (but still updates
the record) once a SKU's row is already dated today, so it only ever
alerts once per SKU per day without a separate ledger file.

Lives in the local repo config/ dir, like thresholds.json/exceptions.json
-- this data is genuinely read back on the next run, unlike the
write-only shared activity_log.

Also mirrored out to a human-readable .xlsx on the management server
(export_to_shared_xlsx) -- same share as activity_log.py's SHARED_LOG_DIR,
in its own "Sales report" subfolder -- so staff can open and review the
current price-tracking table in Excel without needing repo/server access.
That export is fire-and-forget (never raises): a share hiccup must not
break the real pipeline run that's updating the local CSV.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

import openpyxl

BASE_DIR = Path(__file__).resolve().parent
TRACKING_PATH = BASE_DIR / "config" / "price_tracking.csv"

FIELDNAMES = ["sku", "unit_price", "last_updated_date"]

SHARED_XLSX_PATH = (
    r"\\mbc-nt01\Management\Final Automations\Sales Report Tool"
    r"\Sales report\Price Comparison Order.xlsx"
)


def load_tracking() -> dict[str, dict]:
    """sku -> {"unit_price": float, "last_updated_date": str}. {} if the file doesn't exist yet."""
    if not TRACKING_PATH.exists():
        return {}
    records: dict[str, dict] = {}
    with open(TRACKING_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            records[row["sku"]] = {
                "unit_price": float(row["unit_price"]),
                "last_updated_date": row["last_updated_date"],
            }
    return records


def save_tracking(records: dict[str, dict]) -> None:
    TRACKING_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(TRACKING_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for sku in sorted(records):
            record = records[sku]
            writer.writerow({
                "sku": sku,
                "unit_price": record["unit_price"],
                "last_updated_date": record["last_updated_date"],
            })


def export_to_shared_xlsx(records: dict[str, dict]) -> None:
    """Write records to SHARED_XLSX_PATH for human viewing -- fire-and-forget,
    never raises (a share/network hiccup must not break the real pipeline
    run that's updating the local CSV, same principle as activity_log.py).
    """
    try:
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = "Price Comparison Order"
        sheet.append(["SKU", "Unit Price", "Last Updated Date"])
        for sku in sorted(records):
            record = records[sku]
            sheet.append([sku, record["unit_price"], record["last_updated_date"]])

        os.makedirs(os.path.dirname(SHARED_XLSX_PATH), exist_ok=True)
        workbook.save(SHARED_XLSX_PATH)
    except Exception:  # noqa: BLE001 - fire-and-forget, see docstring
        pass
