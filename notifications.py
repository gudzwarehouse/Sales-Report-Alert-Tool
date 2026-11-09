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


def render_product_breach_html(
    *,
    title: str,
    severity: str,
    subtitle: str,
    items: list[dict],
    totals: dict | None = None,
    footer: str = "This is an automated alert from the Sales Report Tool.",
) -> str:
    """Render a threshold-breach alert with a per-product breakdown (name,
    SKU, order count, quantity sold, sales value), mobile-responsive
    by construction (fluid stacked blocks, inline styles only).

    ``severity`` is "MEDIUM" or "HIGH" -- MEDIUM is the lower threshold
    bucket, HIGH is the bucket that also fires the Zapier SMS/phone-call
    triggers (see sales_pipeline.py's bucket severity mapping).
    ``items`` is [{"sku", "title", "orders", "quantity", "sales_value",
    "channels"}, ...] -- "channels" is an already-formatted display string
    (e.g. "Amazon UK (3), Temu UK (1)", see sales_pipeline._format_channels),
    optional/blank for rows with no channel data (e.g. the totals row).
    ``totals`` is an optional {"orders", "quantity", "sales_value"} summary
    row shown at the bottom (used for the total-threshold alert's full
    window breakdown).
    """
    severity = severity.upper()
    badge_color = "#b91c1c" if severity == "HIGH" else "#b45309"
    badge_bg = "#fef2f2" if severity == "HIGH" else "#fffbeb"

    def money(value: object) -> str:
        try:
            return f"£{float(value):,.2f}"
        except (TypeError, ValueError):
            return "£0.00"

    # Everything is inline-styled and fluid (no @media, no <style> dependency):
    # many mobile mail apps (e.g. Gmail app with non-Google accounts) strip
    # <style> blocks, so each product is a stacked block -- name/SKU/channels
    # on top, then a 3-column Orders/Qty/Sales strip -- that fits any width.
    def stat(label: str, value: str, bold: bool) -> str:
        weight = "700" if bold else "600"
        return (
            '<td width="33%" valign="top" style="padding:0 4px 0 0;">'
            f'<div style="font-size:11px;text-transform:uppercase;letter-spacing:.03em;color:#9ca3af;">{escape(label)}</div>'
            f'<div style="font-size:15px;font-weight:{weight};color:#111827;">{value}</div></td>'
        )

    def item_html(item: dict, *, totals_row: bool = False) -> str:
        product = escape(str(item.get("title") or item.get("sku") or ""))
        sku = escape(str(item.get("sku") or ""))
        online_sku = escape(str(item.get("online_sku") or ""))
        channels = escape(str(item.get("channels", "") or ""))
        sku_parts = [f"SKU: {sku}"] if sku else []
        if online_sku:
            sku_parts.append(f"Online SKU: {online_sku}")
        detail = ""
        if sku_parts and not totals_row:
            detail += f'<div style="font-size:12px;color:#9ca3af;margin-top:2px;word-break:break-all;">{" &middot; ".join(sku_parts)}</div>'
        if channels:
            detail += f'<div style="font-size:12px;color:#6b7280;margin-top:2px;">{channels}</div>'
        bg = "#f9fafb" if totals_row else "#ffffff"
        top = "2px solid #e5e7eb" if totals_row else "0"
        return (
            f'<div style="padding:14px 16px;border-bottom:1px solid #f3f4f6;border-top:{top};background:{bg};">'
            f'<div style="font-size:14px;font-weight:{"700" if totals_row else "600"};color:#111827;word-wrap:break-word;overflow-wrap:anywhere;">{product}</div>'
            f'{detail}'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="width:100%;margin-top:10px;border-collapse:collapse;"><tr>'
            f'{stat("Orders", escape(str(item.get("orders", ""))), totals_row)}'
            f'{stat("Qty", escape(str(item.get("quantity", ""))), totals_row)}'
            f'{stat("Sales", escape(money(item.get("sales_value", 0))), totals_row)}'
            '</tr></table></div>'
        )

    rows_html = "".join(item_html(item) for item in items)
    totals_html = item_html({**totals, "title": "Total"}, totals_row=True) if totals is not None else ""

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
</head>
<body style="margin:0;padding:0;background:#f3f4f6;">
<div style="max-width:640px;margin:0 auto;padding:12px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1f2937;">
  <div style="background:#ffffff;border-radius:10px;border:1px solid #e5e7eb;">
    <div style="padding:16px;border-bottom:1px solid #e5e7eb;">
      <span style="display:inline-block;padding:4px 12px;border-radius:999px;font-size:12px;font-weight:700;letter-spacing:.03em;background:{badge_bg};color:{badge_color};">{escape(severity)}</span>
      <h1 style="font-size:18px;margin:10px 0 4px;color:#111827;">{escape(title)}</h1>
      <p style="font-size:13px;color:#6b7280;margin:0;">{escape(subtitle)}</p>
    </div>
    {rows_html}
    {totals_html}
    <div style="padding:14px 16px;font-size:12px;color:#9ca3af;">{escape(footer)}</div>
  </div>
</div>
</body></html>"""


def render_error_html(*, source: str, message: str) -> str:
    """Render a pipeline-error alert, mirroring RunProcess's ErrorHandler email."""
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1f2937;line-height:1.5;margin:0;padding:12px;background:#ffffff;">
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
    """Send an alert email. Falls back to NOTIFY_EMAIL_TO/CC from .env only
    when to/cc are omitted entirely (None) -- an explicitly-passed empty
    list ([]) means "no recipients here", not "use the default", and is
    respected as-is. (Previously `cc=[]` still fell back to NOTIFY_EMAIL_CC
    because `list([] or [])` is indistinguishable from `list(None or [])`;
    fixed after that silently CC'd a real recipient during a test send.)

    Never raises -- a failed alert must not crash the monitoring loop that's
    trying to report a *different* problem. Logs a warning and returns instead.
    """
    load_dotenv(override=True)
    sender = os.getenv("NOTIFY_EMAIL_FROM", "").strip()
    password = os.getenv("NOTIFY_EMAIL_APP_PASSWORD", "").strip()

    if to is None:
        default_to = os.getenv("NOTIFY_EMAIL_TO", "").strip()
        to_list = [default_to] if default_to else []
    else:
        to_list = [to] if isinstance(to, str) else list(to)
    if cc is None:
        default_cc = os.getenv("NOTIFY_EMAIL_CC", "").strip()
        cc_list = [default_cc] if default_cc else []
    else:
        cc_list = [cc] if isinstance(cc, str) else list(cc)

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
