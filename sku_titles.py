"""Product-name cache for our SKUs (state/sku_titles.json), shown under each
SKU on the Excluded SKUs page. StockFeeds has no product-name column, so the
names come from ChannelAdvisor order titles seen by the pipeline (latest wins).
"""

from __future__ import annotations

import json
from pathlib import Path

TITLES_PATH = Path(__file__).parent / "state" / "sku_titles.json"


def load_titles(path: Path = TITLES_PATH) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def update_from_rows(rows: list[dict], sku_lookup: dict[str, str], path: Path = TITLES_PATH) -> int:
    """Merge {our_sku: title} from fetched order rows; returns count of new/changed names.
    Never raises (a cache failure must not break alerting)."""
    try:
        found: dict[str, str] = {}
        for row in rows:
            our = sku_lookup.get(row.get("supplier_sku"), "")
            title = (row.get("title") or "").strip()
            if our and title:
                found[our] = title
        titles = load_titles(path)
        changed = sum(1 for k, v in found.items() if titles.get(k) != v)
        if changed:
            titles.update(found)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(titles, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        return changed
    except Exception:  # noqa: BLE001
        return 0


def fetch_missing_names(skus: list[str], path: Path = TITLES_PATH) -> tuple[int, list[str]]:
    """Look up product names (ChannelAdvisor Products API, GET-only) for the SKUs
    that have none yet, trying each SKU's online SKUs from StockFeeds. Saves what
    it finds; returns (found_count, still_missing)."""
    import os

    from dotenv import load_dotenv

    import ca_orders_client
    import httpx
    import sku_lookup

    load_dotenv(override=True)
    titles = load_titles(path)
    todo = [s for s in skus if s not in titles]
    if not todo:
        return 0, []
    lookup = sku_lookup.load_sku_lookup(os.environ["STOCKFEEDS_XLSM_PATH"], os.environ["STOCKFEEDS_XLSM_PASSWORD"])
    cfg = ca_orders_client._config()
    url = os.environ["CA_PRODUCTS_API_URL"]
    found: dict[str, str] = {}
    with httpx.Client() as client:
        headers = {"Authorization": "Bearer " + ca_orders_client._get_access_token(client, cfg),
                   "Accept": "application/json"}
        for our in todo:
            candidates = [k for k, v in lookup.items() if v == our] + [our]
            for sku in candidates:
                escaped = sku.replace("'", "''")
                resp = ca_orders_client._get_orders_page(client, headers, url, {
                    "$filter": f"ProfileID eq {cfg['profile_id']} and Sku eq '{escaped}'",
                    "$select": "Sku,Title", "$top": 1})
                value = resp.json().get("value", [])
                if value and (value[0].get("Title") or "").strip():
                    found[our] = value[0]["Title"].strip()
                    break
    if found:
        titles.update(found)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(titles, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return len(found), [s for s in todo if s not in found]
