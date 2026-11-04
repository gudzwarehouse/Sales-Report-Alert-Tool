"""Sales-threshold alert emails -- Python port of Module1.bas's
``SendEmailNotification``/``SendEmailList``.

Same Gmail SMTP + app-password pattern as the other "Final Automations"
projects (``NOTIFY_EMAIL_FROM`` / ``NOTIFY_EMAIL_APP_PASSWORD`` in ``.env``),
with an added HTML alternative body instead of the VBA's plain text only.
"""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from html import escape
from typing import Callable

from dotenv import load_dotenv

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def render_sales_alert_html(
    *,
    title: str,
    threshold_label: str,
    rows: list[tuple[str, object]],
    footer: str = "This is an automated alert from the Sales Report Tool.",
) -> str:
    """Render a threshold-breach alert as a small HTML table.

    ``rows`` is a list of ``(sku_or_label, count)`` pairs, already sorted by
    the caller (highest first, matching the VBA's own sort order).
    """
    body_rows = "".join(
        '<tr><td style="padding:6px 10px;color:#1f2937;'
        'border-bottom:1px solid #e5e7eb;">'
        f'{escape(str(label))}</td><td style="padding:6px 10px;font-weight:600;'
        'text-align:right;border-bottom:1px solid #e5e7eb;">'
        f'{escape(str(count))}</td></tr>'
        for label, count in rows
    )
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1f2937;line-height:1.5;margin:0;padding:20px;background:#ffffff;">
<div style="max-width:600px;margin:0 auto;">
<div style="border-bottom:2px solid #e5e7eb;padding-bottom:12px;margin-bottom:16px;">
<h2 style="margin:0 0 6px;color:#111827;">{escape(title)}</h2>
<div style="font-size:13px;color:#6b7280;">{escape(threshold_label)}</div>
</div>
<table style="width:100%;border-collapse:collapse;font-size:14px;">
<tr><th style="text-align:left;padding:6px 10px;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:#6b7280;border-bottom:1px solid #e5e7eb;">SKU / Metric</th>
<th style="text-align:right;padding:6px 10px;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:#6b7280;border-bottom:1px solid #e5e7eb;">Orders</th></tr>
{body_rows}
</table>
<div style="font-size:12px;color:#9ca3af;border-top:1px solid #e5e7eb;margin-top:16px;padding-top:12px;">{escape(footer)}</div>
</div></body></html>"""


def render_error_html(*, source: str, message: str) -> str:
    """Render a pipeline-error alert, mirroring RunProcess's ErrorHandler email."""
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1f2937;line-height:1.5;margin:0;padding:20px;background:#ffffff;">
<div style="max-width:600px;margin:0 auto;">
<div style="border-bottom:2px solid #e5e7eb;padding-bottom:12px;margin-bottom:16px;">
<h2 style="margin:0 0 6px;color:#b91c1c;">Error in CA Sales Reports</h2>
<span style="background:#b91c1c;color:#ffffff;padding:3px 10px;border-radius:12px;font-size:12px;font-weight:600;">FAILED</span>
</div>
<div style="background:#fef2f2;border:1px solid #fca5a5;padding:12px;border-radius:6px;font-size:13px;color:#991b1b;">
<strong>Source:</strong> {escape(source)}<br>
<strong>Message:</strong><pre style="white-space:pre-wrap;overflow-wrap:anywhere;font-family:inherit;margin:4px 0 0;">{escape(message)}</pre>
</div>
</div></body></html>"""


def send_alert(
    subject: str,
    text_body: str,
    *,
    to: str | list[str] | None = None,
    cc: str | list[str] | None = None,
    html_body: str | None = None,
    log: Callable[[str], None] = print,
) -> None:
    """Send an alert email. Falls back to NOTIFY_EMAIL_TO/CC from .env if to/cc omitted.

    Never raises -- a failed alert must not crash the monitoring loop that's
    trying to report a *different* problem. Logs a warning and returns instead.
    """
    load_dotenv(override=True)
    sender = os.getenv("NOTIFY_EMAIL_FROM", "").strip()
    password = os.getenv("NOTIFY_EMAIL_APP_PASSWORD", "").strip()

    to_list = [to] if isinstance(to, str) else list(to or [])
    if not to_list:
        default_to = os.getenv("NOTIFY_EMAIL_TO", "").strip()
        to_list = [default_to] if default_to else []
    cc_list = [cc] if isinstance(cc, str) else list(cc or [])
    if not cc_list:
        default_cc = os.getenv("NOTIFY_EMAIL_CC", "").strip()
        cc_list = [default_cc] if default_cc else []

    if not sender or not password:
        log("[WARN] Alert not sent: NOTIFY_EMAIL_FROM / NOTIFY_EMAIL_APP_PASSWORD not set in .env.")
        return
    if not to_list:
        log("[WARN] Alert not sent: no recipients (pass to=, or set NOTIFY_EMAIL_TO in .env).")
        return

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(to_list)
    if cc_list:
        message["Cc"] = ", ".join(cc_list)
    message.set_content(text_body)
    if html_body is not None:
        message.add_alternative(html_body, subtype="html")

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(sender, password)
            smtp.send_message(message)
        log(f"Alert sent to {', '.join(to_list + cc_list)}: {subject}")
    except Exception as error:  # noqa: BLE001 - never let a notification failure crash the caller
        log(f"[WARN] Could not send alert email: {error}")
