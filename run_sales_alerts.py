"""Entry point: one pass over the Orders Last 1 hour / Last 4 hours CSV drops.

Port of RunProcess (Module1.bas), minus the Application.OnTime-equivalent
re-arm loop -- scheduling this on an interval is an OS-level job (Task
Scheduler), not something this script does itself.

Run: python run_sales_alerts.py
"""

from __future__ import annotations

import datetime
import glob
import os
import sys
import time

from dotenv import load_dotenv

import activity_log
import config_store
import notifications
import sku_lookup as sku_lookup_module
from sales_pipeline import run_1h_check, run_4h_check


def log(message: str, level: str = "INFO") -> None:
    timestamp = datetime.datetime.now().isoformat(timespec="seconds")
    print(f"{timestamp} [{level}] {message}", flush=True)


def load_config() -> tuple[dict, set[str]]:
    return config_store.load_thresholds(), set(config_store.load_exceptions())


def make_send_alert(counters: dict):
    def send_alert(subject: str, text_body: str, *, to: list[str]) -> None:
        counters["alerts_sent"] += 1
        log(f"Alert triggered: \"{subject}\" -> {len(to)} recipient(s)")
        activity_log.log_activity("INFO", f"Alert sent: \"{subject}\" to {len(to)} recipient(s)")
        notifications.send_alert(subject, text_body, to=to, log=log)
    return send_alert


def main() -> None:
    start_time = time.monotonic()
    counters = {"alerts_sent": 0, "files_1h": 0, "files_4h": 0}

    log("Pipeline run starting.")
    activity_log.log_activity("INFO", "Run started")

    load_dotenv(override=True)
    last_hour_folder = os.getenv("LAST_HOUR_FOLDER", "").strip()
    last_4_hours_folder = os.getenv("LAST_4_HOURS_FOLDER", "").strip()
    if not last_hour_folder or not last_4_hours_folder:
        log("LAST_HOUR_FOLDER / LAST_4_HOURS_FOLDER not set in .env.", level="ERROR")
        activity_log.log_activity("ERROR", "Run FAILED: LAST_HOUR_FOLDER / LAST_4_HOURS_FOLDER not set")
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
        for csv_path in glob.glob(os.path.join(last_hour_folder, "Orders Last 1 hour*.csv")):
            if os.path.getsize(csv_path) == 0:
                continue
            log(f"Processing 1h file: {csv_path}")
            run_1h_check(csv_path, lookup, config, exceptions, send_alert)
            counters["files_1h"] += 1

        for csv_path in glob.glob(os.path.join(last_4_hours_folder, "Orders Last 4 hours*.csv")):
            if os.path.getsize(csv_path) == 0:
                continue
            log(f"Processing 4h file: {csv_path}")
            run_4h_check(csv_path, lookup, config, exceptions, send_alert)
            counters["files_4h"] += 1
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
        f"Run completed in {duration:.1f}s: {counters['files_1h']} 1h file(s), "
        f"{counters['files_4h']} 4h file(s), {counters['alerts_sent']} alert(s) sent"
    )
    log(summary)
    activity_log.log_activity("INFO", summary)


if __name__ == "__main__":
    main()
