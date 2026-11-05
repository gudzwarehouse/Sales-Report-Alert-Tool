"""Entry point: sends one daily summary email of today's pipeline activity
to the automation account, meant to run once a day at 3pm via its own
Task Scheduler trigger (separate from run_scheduler.bat's 40-minute loop).

Run: python run_daily_summary.py
"""

from __future__ import annotations

import datetime
import os

from dotenv import load_dotenv

import daily_summary
import notifications


def log(message: str) -> None:
    print(message, flush=True)


DEFAULT_CC = ["theekshana@gudz.com", "aflal@gudz.com"]


def main() -> None:
    load_dotenv(override=True)
    recipient = os.getenv("DAILY_SUMMARY_EMAIL", "").strip() or os.getenv("NOTIFY_EMAIL_FROM", "").strip()
    if not recipient:
        log("[ERROR] DAILY_SUMMARY_EMAIL / NOTIFY_EMAIL_FROM not set in .env.")
        raise SystemExit(1)

    cc_raw = os.getenv("DAILY_SUMMARY_CC", "").strip()
    cc_list = [addr.strip() for addr in cc_raw.split(",") if addr.strip()] if cc_raw else DEFAULT_CC

    date_str = datetime.date.today().isoformat()
    lines = daily_summary.read_today_log_lines(date_str)
    summary = daily_summary.parse_log_lines(lines)
    try:
        import config_store
        summary["window_thresholds"] = {
            window: min(b["total_threshold"] for b in buckets)
            for window, buckets in config_store.load_thresholds().items() if buckets
        }
    except Exception as error:  # noqa: BLE001 - thresholds are optional decoration
        log(f"[WARN] Could not load thresholds for summary: {error}")

    text_body = daily_summary.render_summary_text(date_str, summary)
    html_body = daily_summary.render_summary_html(date_str, summary)
    subject = daily_summary.render_subject(date_str, summary)

    notifications.send_alert(
        subject,
        text_body,
        to=[recipient],
        cc=cc_list,
        html_body=html_body,
        log=log,
    )


if __name__ == "__main__":
    main()
