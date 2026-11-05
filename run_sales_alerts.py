"""Entry point: one pass checking the trailing 1h/4h order windows.

Port of RunProcess (Module1.bas), minus the Application.OnTime-equivalent
re-arm loop -- scheduling this on an interval is an OS-level job (Task
Scheduler), not something this script does itself.

Reads orders straight from ChannelAdvisor's read-only Orders API
(ca_orders_client) rather than polling the Orders Last 1 hour/4 hours CSV
exports -- confirmed this session that those CSVs ARE ChannelAdvisor
exports for the same profile, so this removes a dependency on that export
job without changing what's actually being counted. See ca_orders_client's
module docstring for why no incremental watermark is needed here.

Run: python run_sales_alerts.py
"""

from __future__ import annotations

import datetime
import os
import sys
import time

from dotenv import load_dotenv

import activity_log
import ca_orders_client
import config_store
import notifications
import sku_lookup as sku_lookup_module
from sales_pipeline import evaluate_1h, evaluate_4h


def log(message: str, level: str = "INFO") -> None:
    timestamp = datetime.datetime.now().isoformat(timespec="seconds")
    print(f"{timestamp} [{level}] {message}", flush=True)


def load_config() -> tuple[dict, set[str]]:
    return config_store.load_thresholds(), set(config_store.load_exceptions())


def make_send_alert(counters: dict):
    def send_alert(subject: str, text_body: str, *, to: list[str], html_body: str | None = None) -> None:
        counters["alerts_sent"] += 1
        log(f"Alert triggered: \"{subject}\" -> {len(to)} recipient(s)")
        activity_log.log_activity("INFO", f"Alert sent: \"{subject}\" to {len(to)} recipient(s)")
        notifications.send_alert(subject, text_body, to=to, html_body=html_body, log=log)
    return send_alert


MAX_WINDOW_RETRIES = 3
WINDOW_RETRY_BASE_DELAY_SECONDS = 5  # 5s, 10s, 20s


def _check_window(window_label: str, window_minutes: int, evaluate_fn, lookup, config, exceptions, send_alert) -> int:
    """Fetch + evaluate one window (1h/4h), self-healing: if the fetch or the
    check itself blows up (e.g. a ChannelAdvisor blip ca_orders_client's own
    per-page retries didn't absorb), retry the WHOLE step with exponential
    backoff before giving up. Every attempt -- success or failure -- is
    logged both to the console and the shared activity_log, so a run that
    needed 2 tries to succeed is visible, not silent.

    Still raises after MAX_WINDOW_RETRIES attempts -- this never swallows a
    failure as "0 orders found"; main()'s except block still sends the usual
    error alert if every retry is exhausted.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            log(f"[{window_label}] Fetching trailing {window_minutes}min order window "
                f"(attempt {attempt}/{MAX_WINDOW_RETRIES})...")
            rows = ca_orders_client.fetch_recent_order_rows(window_minutes)
            log(f"[{window_label}] Fetched {len(rows)} order line(s) on attempt {attempt}.")
            evaluate_fn(rows, lookup, config, exceptions, send_alert)
            log(f"[{window_label}] Checked successfully on attempt {attempt}.")
            activity_log.log_activity("INFO", f"[{window_label}] OK on attempt {attempt}: {len(rows)} order line(s)")
            return len(rows)
        except Exception as error:  # noqa: BLE001 - retry here, re-raise once exhausted
            if attempt >= MAX_WINDOW_RETRIES:
                log(f"[{window_label}] Giving up after {attempt} attempt(s): {error}", level="ERROR")
                activity_log.log_activity(
                    "ERROR", f"[{window_label}] FAILED after {attempt} attempt(s): {error}"
                )
                raise
            wait = WINDOW_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
            log(f"[{window_label}] Attempt {attempt} failed ({error}); retrying in {wait}s...", level="WARN")
            activity_log.log_activity(
                "WARN", f"[{window_label}] attempt {attempt}/{MAX_WINDOW_RETRIES} failed, retrying in {wait}s: {error}"
            )
            time.sleep(wait)


def main() -> None:
    start_time = time.monotonic()
    counters = {"alerts_sent": 0, "orders_1h": 0, "orders_4h": 0}

    log("Pipeline run starting.")
    activity_log.log_activity("INFO", "Run started")

    load_dotenv(override=True)
    required_ca_vars = [
        "CA_UK_APPLICATION_ID", "CA_UK_SHARED_SECRET", "CA_UK_REFRESH_TOKEN",
        "CA_UK_PROFILE_ID", "CA_TOKEN_URL", "CA_ORDERS_API_URL",
    ]
    missing_ca_vars = [name for name in required_ca_vars if not os.getenv(name, "").strip()]
    if missing_ca_vars:
        log(f"Missing ChannelAdvisor config in .env: {', '.join(missing_ca_vars)}", level="ERROR")
        activity_log.log_activity("ERROR", f"Run FAILED: missing ChannelAdvisor config: {', '.join(missing_ca_vars)}")
        sys.exit(1)

    stockfeeds_path = os.getenv("STOCKFEEDS_XLSM_PATH", "").strip()
    stockfeeds_password = os.getenv("STOCKFEEDS_XLSM_PASSWORD", "").strip()
    if not stockfeeds_path or not stockfeeds_password:
        log("STOCKFEEDS_XLSM_PATH / STOCKFEEDS_XLSM_PASSWORD not set in .env.", level="ERROR")
        activity_log.log_activity("ERROR", "Run FAILED: STOCKFEEDS_XLSM_PATH / STOCKFEEDS_XLSM_PASSWORD not set")
        sys.exit(1)

    config, exceptions = load_config()
    log(f"Loading SKU lookup fresh from {stockfeeds_path} ...")
    lookup = sku_lookup_module.load_sku_lookup(stockfeeds_path, stockfeeds_password)
    log(f"Loaded {len(lookup)} SKU mappings.")
    activity_log.log_activity("INFO", f"Loaded {len(lookup)} SKU mappings from StockFeeds.xlsm")
    send_alert = make_send_alert(counters)

    try:
        counters["orders_1h"] = _check_window("1h", 60, evaluate_1h, lookup, config, exceptions, send_alert)
        counters["orders_4h"] = _check_window("4h", 240, evaluate_4h, lookup, config, exceptions, send_alert)
    except Exception as error:  # noqa: BLE001 - report then exit, matching RunProcess's ErrorHandler
        duration = time.monotonic() - start_time
        log(f"Run FAILED after {duration:.1f}s: {error}", level="ERROR")
        activity_log.log_activity("ERROR", f"Run FAILED after {duration:.1f}s: {error}")
        notifications.send_alert(
            "Error in CA Sales Reports",
            f"Error Source: run_sales_alerts\nError Message: {error}",
            html_body=notifications.render_error_html(source="run_sales_alerts", message=str(error)),
            log=log,
        )
        raise

    duration = time.monotonic() - start_time
    summary = (
        f"Run completed in {duration:.1f}s: {counters['orders_1h']} 1h order line(s), "
        f"{counters['orders_4h']} 4h order line(s), {counters['alerts_sent']} alert(s) sent"
    )
    log(summary)
    activity_log.log_activity("INFO", summary)


if __name__ == "__main__":
    main()
