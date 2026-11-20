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
import functools
import json
import os
import sys
import time

from dotenv import load_dotenv

import activity_log
import alert_state
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


def _check_window(
    window_label: str, window_minutes: int, evaluate_fn, lookup, config, exceptions, send_alert,
    align_hours: int = 1,
) -> tuple[int, list[dict]]:
    """Fetch + evaluate one window (1h/4h), self-healing: if the fetch or the
    check itself blows up (e.g. a ChannelAdvisor blip ca_orders_client's own
    per-page retries didn't absorb), retry the WHOLE step with exponential
    backoff before giving up. Every attempt -- success or failure -- is
    logged both to the console and the shared activity_log, so a run that
    needed 2 tries to succeed is visible, not silent.

    Still raises after MAX_WINDOW_RETRIES attempts -- this never swallows a
    failure as "0 orders found"; main()'s except block still sends the usual
    error alert if every retry is exhausted.

    Returns (row_count, raw_rows).
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            log(f"[{window_label}] Fetching trailing {window_minutes}min order window "
                f"(attempt {attempt}/{MAX_WINDOW_RETRIES})...")
            fetch_kwargs = {"align_hours": align_hours} if align_hours != 1 else {}
            rows = ca_orders_client.fetch_recent_order_rows(window_minutes, **fetch_kwargs)
            log(f"[{window_label}] Fetched {len(rows)} order line(s) on attempt {attempt}.")
            evaluate_fn(rows, lookup, config, exceptions, send_alert)
            log(f"[{window_label}] Checked successfully on attempt {attempt}.")
            activity_log.log_activity("INFO", f"[{window_label}] OK on attempt {attempt}: {len(rows)} order line(s)")
            return len(rows), rows
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


def _slot_label(window_minutes: int, align_hours: int = 1) -> str:
    """Exact window range as "09:00-13:00" (UTC)."""
    start, end = ca_orders_client.window_bounds(window_minutes, align_hours)
    return f"{start:%H:%M}-{end:%H:%M}"


def _range_text(window_minutes: int, align_hours: int = 1) -> str:
    """Range for alert subjects/headings, e.g. "13:00-14:00"; prefixed with the
    date ("19 Nov 23:00-00:00") when the window started before today."""
    start, end = ca_orders_client.window_bounds(window_minutes, align_hours)
    text = f"{start:%H:%M}-{end:%H:%M}"
    if start.date() != datetime.datetime.now(datetime.timezone.utc).date():
        text = f"{start:%d %b} {text}"
    return text


def make_sku_hit_logger(window_label: str, window_minutes: int, align_hours: int = 1):
    """Hook for evaluate_1h/4h: one activity-log line per SKU at/over the lowest
    SKU threshold (alerted or excluded), parsed back by daily_summary. INFO level,
    so it never shows up as a warning/error."""
    def log_hits(items: list[dict]) -> None:
        slot = _slot_label(window_minutes, align_hours)
        for item in items:
            payload = {"window": window_label, "slot": slot, **{
                k: item.get(k) for k in (
                    "sku", "status", "orders", "quantity", "sales_value", "title",
                    "channels", "online_sku", "threshold",
                )}}
            activity_log.log_activity("INFO", f"SKU threshold hit: {json.dumps(payload, ensure_ascii=False)}")
    return log_hits


def make_total_hit_logger(window_label: str, window_minutes: int, align_hours: int = 1):
    """Hook: one log line whenever the window's TOTAL order count is over a
    threshold (logged every run, even when the email is de-duped)."""
    def log_hit(info: dict) -> None:
        payload = {"window": window_label, "slot": _slot_label(window_minutes, align_hours), **info}
        activity_log.log_activity("INFO", f"Total threshold hit: {json.dumps(payload, ensure_ascii=False)}")
    return log_hit


def make_alert_state(window_label: str, window_minutes: int, align_hours: int = 1) -> alert_state.AlertState:
    """Once-per-slot de-dupe: 1h alerts per hourly slot (max 24/day), 4h per
    4-hour block (max 6/day). See alert_state.py."""
    _, end = ca_orders_client.window_bounds(window_minutes, align_hours)
    return alert_state.AlertState(alert_state.slot_key(window_label, end))


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
        eval_1h = functools.partial(
            evaluate_1h, on_sku_hits=make_sku_hit_logger("1h", 60), alert_state=make_alert_state("1h", 60),
            on_total_hit=make_total_hit_logger("1h", 60),
            window_range=_range_text(60))
        counters["orders_1h"], _ = _check_window("1h", 60, eval_1h, lookup, config, exceptions, send_alert)

        # 4h, check A: rolling window every run, to catch breaches BETWEEN the
        # block boundaries. Never emails -- it only feeds the daily summary.
        eval_4h_roll = functools.partial(
            evaluate_4h, on_sku_hits=make_sku_hit_logger("4h_roll", 240),
            on_total_hit=make_total_hit_logger("4h_roll", 240))
        counters["orders_4h"], _ = _check_window(
            "4h_roll", 240, eval_4h_roll, lookup, config, exceptions, lambda *a, **k: None)

        # 4h, check B: the EMAIL alert. Fixed blocks 00-04, 04-08, ... (UTC),
        # evaluated once, at the first run after a block ends (a missed run
        # self-heals on the next one); each alert emails once per block.
        state_4h = make_alert_state("4h", 240, align_hours=4)
        if state_4h.is_new("eval", "block", 0):
            eval_4h = functools.partial(
                evaluate_4h, on_sku_hits=make_sku_hit_logger("4h", 240, 4), alert_state=state_4h,
                on_total_hit=make_total_hit_logger("4h", 240, 4),
                window_range=_range_text(240, 4))
            _check_window("4h", 240, eval_4h, lookup, config, exceptions, send_alert, align_hours=4)
            state_4h.mark("eval", "block", 0)
        else:
            log("[4h] Latest finished 4-hour block already checked; email check waits for the next block.")
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
        f"{counters['orders_4h']} 4h order line(s), "
        f"{counters['alerts_sent']} alert(s) sent"
    )
    log(summary)
    activity_log.log_activity("INFO", summary)


if __name__ == "__main__":
    main()
