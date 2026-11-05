"""Read-only ChannelAdvisor Orders API client -- replaces polling the
Orders Last 1 hour*.csv / Orders Last 4 hours*.csv exports.

Confirmed this session (real field-level diff, zero mismatches) that those
CSV exports ARE ChannelAdvisor scheduled exports for profile
CA_UK_PROFILE_ID -- every row in both had ShippingStatus=Unshipped, and
order dates ranged well past the nominal "1 hour"/"4 hours" (a file
generated at 11:00 held orders from 09:03), so this is really "currently-
Unshipped order backlog," not a strict rolling time slice. fetch_recent_
order_rows() reproduces that directly against the API: CreatedDateUtc in
the trailing window + ShippingStatus eq 'Unshipped'.

GET-only, per CLAUDE.md's hard rule -- the only POST here is the OAuth
token exchange itself (an auth handshake, not a ChannelAdvisor data
mutation), never a call against the Orders endpoint.

No incremental watermark: every call re-queries its own full trailing
window from "now". A late/skipped scheduled run is self-healing as long as
the next run happens before the window rolls past it (run_sales_alerts.py
runs every 40 min; the 1h/4h windows give 20/200 minutes of margin). What
this does NOT cover is a multi-hour ChannelAdvisor/scheduler outage -- that
pre-existing limitation (same under the old CSV export) is caught by
activity_log/daily_summary's "did the scheduler run at all" visibility,
not backfilled here.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from dotenv import load_dotenv

MAX_TRANSIENT_RETRIES = 4  # network-level failures only (timeouts, connection resets)
MAX_RATE_LIMIT_RETRIES = 5
_RETRYABLE_EXCEPTIONS = (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError)

PAGE_SIZE = 100


def _config() -> dict[str, str]:
    load_dotenv(override=True)
    values = {
        "application_id": os.getenv("CA_UK_APPLICATION_ID", "").strip(),
        "shared_secret": os.getenv("CA_UK_SHARED_SECRET", "").strip(),
        "refresh_token": os.getenv("CA_UK_REFRESH_TOKEN", "").strip(),
        "profile_id": os.getenv("CA_UK_PROFILE_ID", "").strip(),
        "token_url": os.getenv("CA_TOKEN_URL", "").strip(),
        "orders_url": os.getenv("CA_ORDERS_API_URL", "").strip(),
    }
    missing = [key for key, value in values.items() if not value]
    if missing:
        raise RuntimeError(f"Missing ChannelAdvisor config in .env: {', '.join(missing)}")
    return values


def _request_with_retry(client: httpx.Client, method: str, url: str, **kwargs: Any) -> httpx.Response:
    """Exponential-backoff retry for transient network failures only (never-
    got-a-response cases). HTTP status codes (429) are handled by the caller."""
    attempt = 0
    while True:
        try:
            return client.request(method, url, **kwargs)
        except _RETRYABLE_EXCEPTIONS:
            if attempt >= MAX_TRANSIENT_RETRIES:
                raise
            time.sleep(2**attempt)  # 1s, 2s, 4s, 8s
            attempt += 1


def _get_access_token(client: httpx.Client, config: dict[str, str]) -> str:
    response = _request_with_retry(
        client,
        "POST",
        config["token_url"],
        data={
            "grant_type": "refresh_token",
            "client_id": config["application_id"],
            "client_secret": config["shared_secret"],
            "refresh_token": config["refresh_token"],
        },
        timeout=30,
    )
    response.raise_for_status()
    token = response.json().get("access_token")
    if not token:
        raise RuntimeError("ChannelAdvisor OAuth response did not contain an access token.")
    return token


def _get_orders_page(
    client: httpx.Client,
    headers: dict[str, str],
    orders_url: str,
    params: dict[str, Any],
) -> httpx.Response:
    """GET one page of Orders, with exponential + Retry-After-aware backoff on 429.

    Exhausting retries raises (via raise_for_status) rather than returning a
    partial/empty page silently -- a failed page must abort the whole run,
    never be mistaken for "no orders found"."""
    response = _request_with_retry(client, "GET", orders_url, headers=headers, params=params, timeout=60)
    retries = 0
    while response.status_code == 429 and retries < MAX_RATE_LIMIT_RETRIES:
        wait = 2**retries  # 1s, 2s, 4s, 8s, 16s
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                wait = max(wait, float(retry_after))
            except ValueError:
                pass
        time.sleep(wait)
        response = _request_with_retry(client, "GET", orders_url, headers=headers, params=params, timeout=60)
        retries += 1
    response.raise_for_status()
    return response


def fetch_recent_order_rows(window_minutes: int, *, status: str = "Unshipped") -> list[dict]:
    """Orders with CreatedDateUtc in the trailing window_minutes, as rows
    shaped like sales_pipeline.load_orders_csv's output: {order_id,
    supplier_sku}. One row per line item -- quantity is not multiplied in,
    matching the CSV path (which only ever reads columns A/D and counts
    rows, never the Quantity column).

    CA_UK only (not CA_IRE) -- the live diff this session showed IE-
    shipping orders already appear inside the UK profile's own export, so
    CA_IRE looks unrelated to this tool; revisit if that assumption turns
    out wrong.
    """
    config = _config()
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=window_minutes)
    filter_value = (
        f"ProfileID eq {config['profile_id']} and "
        f"CreatedDateUtc ge {start.isoformat().replace('+00:00', 'Z')} and "
        f"CreatedDateUtc lt {end.isoformat().replace('+00:00', 'Z')} and "
        f"ShippingStatus eq '{status}'"
    )

    rows: list[dict] = []
    with httpx.Client() as client:
        token = _get_access_token(client, config)
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        skip = 0
        while True:
            response = _get_orders_page(
                client,
                headers,
                config["orders_url"],
                {
                    "$filter": filter_value,
                    "$expand": "Items",
                    "$select": "SiteOrderID",
                    "$top": PAGE_SIZE,
                    "$skip": skip,
                },
            )
            page = response.json().get("value", [])
            for order in page:
                order_id = order.get("SiteOrderID")
                for item in order.get("Items", []) or []:
                    rows.append({"order_id": order_id, "supplier_sku": item.get("Sku")})
            if len(page) < PAGE_SIZE:
                break
            skip += len(page)
    return rows
