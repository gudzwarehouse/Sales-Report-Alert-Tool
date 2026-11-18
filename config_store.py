"""Load/save/validate config/thresholds.json and config/exceptions.json.

Single source of truth for both the pipeline (run_sales_alerts.py) and the
Streamlit editor (app.py) -- neither should read/write these files directly,
so the two can never disagree about shape or validation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = BASE_DIR / "config"
THRESHOLDS_PATH = CONFIG_DIR / "thresholds.json"
EXCEPTIONS_PATH = CONFIG_DIR / "exceptions.json"

WINDOWS = ("1h", "4h")
_EMAIL_RE = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")


def load_thresholds() -> dict:
    return json.loads(THRESHOLDS_PATH.read_text(encoding="utf-8"))


def validate_thresholds(data: dict) -> None:
    """Raise ValueError with a human-readable message if data is malformed."""
    if not isinstance(data, dict) or set(data.keys()) != set(WINDOWS):
        raise ValueError(f"Config must have exactly these windows: {', '.join(WINDOWS)}")
    for window in WINDOWS:
        buckets = data[window]
        if not isinstance(buckets, list) or not buckets:
            raise ValueError(f"{window}: must have at least one threshold bucket")
        for i, bucket in enumerate(buckets, start=1):
            label = f"{window} bucket {i}"
            for key in ("sku_threshold", "total_threshold"):
                value = bucket.get(key)
                if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                    raise ValueError(f"{label}: {key} must be a positive whole number")
            recipients = bucket.get("recipients")
            if not isinstance(recipients, list) or not recipients:
                raise ValueError(f"{label}: must have at least one recipient")
            for address in recipients:
                if not isinstance(address, str) or not _EMAIL_RE.fullmatch(address.strip()):
                    raise ValueError(f"{label}: invalid recipient address: {address!r}")


def save_thresholds(data: dict) -> None:
    validate_thresholds(data)
    THRESHOLDS_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def load_exceptions() -> list[str]:
    data = json.loads(EXCEPTIONS_PATH.read_text(encoding="utf-8"))
    return list(data.get("skus", []))


def parse_skus_from_xlsx(file) -> list[str]:
    """Read SKUs from an uploaded .xlsx (path or file-like) -- first sheet only.

    If row 1 has a header containing "sku" (e.g. "SKU", "OurSKU"), that
    column is used and the header row is skipped; otherwise column A is used
    and every row is treated as data. Blank cells and duplicates are dropped.
    """
    from openpyxl import load_workbook

    wb = load_workbook(file, read_only=True, data_only=True)
    try:
        rows = list(wb.worksheets[0].iter_rows(values_only=True))
    finally:
        wb.close()
    if not rows:
        return []
    col, start = 0, 0
    for i, cell in enumerate(rows[0]):
        if cell is not None and "sku" in str(cell).lower():
            col, start = i, 1
            break
    out: list[str] = []
    seen: set[str] = set()
    for row in rows[start:]:
        if col >= len(row) or row[col] is None:
            continue
        value = row[col]
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        sku = str(value).strip()
        if sku and sku not in seen:
            out.append(sku)
            seen.add(sku)
    return out


def exceptions_template_xlsx() -> bytes:
    """Header-only .xlsx template for bulk-adding excluded SKUs."""
    import io

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Excluded SKUs"
    ws["A1"] = "OurSKU"
    ws.column_dimensions["A"].width = 24
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def save_exceptions(skus: list[str]) -> list[str]:
    """Validate, de-dupe (case-sensitive -- SKUs are case-sensitive identifiers
    here, unlike the supplier-name whitelist in VB Stock update), and save.
    Returns the saved list.
    """
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in skus:
        sku = str(raw).strip()
        if not sku:
            continue
        if sku not in seen:
            cleaned.append(sku)
            seen.add(sku)
    EXCEPTIONS_PATH.write_text(json.dumps({"skus": cleaned}, indent=2) + "\n", encoding="utf-8")
    return cleaned
