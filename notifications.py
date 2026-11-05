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


def _card_css(badge_bg: str, badge_color: str) -> str:
    """Shared mobile-responsive card CSS for both render_product_breach_html
    and render_price_drop_html -- identical skeleton, just the badge colors
    differ per call. Deliberately no `overflow:hidden` on .card -- that
    combined with the mobile @media block's layout silently dropped
    rendered values in a real screenshot test (Phase 3), confirmed fixed
    by removing it; don't reintroduce it here."""
    return f"""
  body {{ margin:0; padding:0; background:#f3f4f6; }}
  .wrap {{ max-width:640px; margin:0 auto; padding:20px; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif; color:#1f2937; }}
  .card {{ background:#ffffff; border-radius:10px; border:1px solid #e5e7eb; }}
  .header {{ padding:20px 20px 16px; border-bottom:1px solid #e5e7eb; }}
  .badge {{ display:inline-block; padding:4px 12px; border-radius:999px; font-size:12px; font-weight:700; letter-spacing:.03em; background:{badge_bg}; color:{badge_color}; }}
  h1 {{ font-size:18px; margin:10px 0 4px; color:#111827; }}
  .subtitle {{ font-size:13px; color:#6b7280; margin:0; }}
  table {{ width:100%; border-collapse:collapse; }}
  thead th {{ text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:.04em; color:#6b7280; padding:10px 20px; border-bottom:1px solid #e5e7eb; }}
  thead th.num, td.num {{ text-align:right; }}
  td.cell {{ padding:12px 20px; border-bottom:1px solid #f3f4f6; font-size:14px; }}
  .mobile-label {{ display:none; }}
  .sku {{ font-size:11px; color:#9ca3af; margin-top:2px; }}
  .totals td {{ background:#f9fafb; border-top:2px solid #e5e7eb; border-bottom:none; }}
  .footer {{ padding:14px 20px; font-size:12px; color:#9ca3af; }}
  @media (max-width:480px) {{
    thead {{ display:none; }}
    table, tbody, tr {{ display:block; width:100%; }}
    tr.row {{ padding:8px 20px; border-bottom:1px solid #f3f4f6; }}
    td.cell {{ display:block; border:none; padding:3px 0; }}
    .row-line {{ display:flex; justify-content:space-between; align-items:baseline; }}
    .mobile-label {{ display:inline-block; font-size:11px; text-transform:uppercase; letter-spacing:.03em; color:#9ca3af; }}
  }}
"""


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
    SKU, order count, quantity sold, sales value), mobile-responsive --
    the table collapses into stacked label/value rows below 480px via the
    @media block, since most mobile mail clients (Gmail/Apple Mail apps)
    honor <style> media queries even though Outlook desktop doesn't (it
    just falls back to the plain table, which still reads fine).

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

    def cell_html(label: str, value: str, *, bold: bool = False) -> str:
        # Real inline elements, not ::before/float -- those render unreliably
        # once an ancestor clips overflow (confirmed: Chromium's full-page
        # screenshot silently dropped floated pseudo-content here, even
        # though the element was genuinely laid out correctly in isolation).
        open_tag, close_tag = ("<strong>", "</strong>") if bold else ("", "")
        return (
            f'<td class="cell num"><div class="row-line">'
            f'<span class="mobile-label">{escape(label)}</span>'
            f'<span class="value">{open_tag}{value}{close_tag}</span>'
            f'</div></td>'
        )

    def channel_cell_html(channels: str, *, bold: bool = False) -> str:
        open_tag, close_tag = ("<strong>", "</strong>") if bold else ("", "")
        return (
            f'<td class="cell"><div class="row-line">'
            f'<span class="mobile-label">Channel(s)</span>'
            f'<span class="value">{open_tag}{escape(channels)}{close_tag}</span>'
            f'</div></td>'
        )

    def row_html(item: dict, *, row_class: str = "row", bold: bool = False) -> str:
        product = escape(str(item.get("title") or item.get("sku") or ""))
        sku = escape(str(item.get("sku") or ""))
        open_tag, close_tag = ("<strong>", "</strong>") if bold else ("", "")
        sku_line = f'<div class="sku">{sku}</div>' if sku and not bold else ""
        product_cell = (
            f'<td class="cell"><div class="row-line">'
            f'<span class="mobile-label">Product</span>'
            f'<span class="value">{open_tag}{product}{close_tag}</span>'
            f'</div>{sku_line}</td>'
        )
        return (
            f'<tr class="{row_class}">{product_cell}'
            f'{channel_cell_html(str(item.get("channels", "") or ""), bold=bold)}'
            f'{cell_html("Orders", escape(str(item.get("orders", ""))), bold=bold)}'
            f'{cell_html("Qty", escape(str(item.get("quantity", ""))), bold=bold)}'
            f'{cell_html("Sales", escape(money(item.get("sales_value", 0))), bold=bold)}'
            f'</tr>'
        )

    rows_html = "".join(row_html(item) for item in items)
    totals_html = ""
    if totals is not None:
        totals_html = row_html({**totals, "title": "Total"}, row_class="row totals", bold=True)

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{_card_css(badge_bg, badge_color)}</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <div class="header">
      <span class="badge">{escape(severity)}</span>
      <h1>{escape(title)}</h1>
      <p class="subtitle">{escape(subtitle)}</p>
    </div>
    <table role="presentation">
      <thead><tr><th>Product</th><th>Channel(s)</th><th class="num">Orders</th><th class="num">Qty</th><th class="num">Sales</th></tr></thead>
      <tbody>
        {rows_html}
        {totals_html}
      </tbody>
    </table>
    <div class="footer">{escape(footer)}</div>
  </div>
</div>
</body></html>"""


def render_price_drop_html(
    *,
    severity: str,
    items: list[dict],
    footer: str = "This is an automated alert from the Sales Report Tool.",
) -> str:
    """Render a price-drop alert -- a sibling to render_product_breach_html
    (same mobile-responsive card skeleton, see _card_css), with columns for
    comparing today's price against a baseline instead of order volume.

    ``severity`` is "MEDIUM" or "HIGH" (HIGH when the drop is at least
    double the configured threshold, see price_check.py's caller).
    ``items`` is [{"sku", "title", "channels", "baseline_price",
    "today_price", "pct_drop", "baseline_source"}, ...] -- "baseline_source"
    is currently always "Yesterday's sale" (the Source column is kept
    generic/always shown in case another baseline is added later, see
    run_sales_alerts.py's _run_price_check).
    """
    severity = severity.upper()
    badge_color = "#b91c1c" if severity == "HIGH" else "#b45309"
    badge_bg = "#fef2f2" if severity == "HIGH" else "#fffbeb"

    def money(value: object) -> str:
        try:
            return f"£{float(value):,.2f}"
        except (TypeError, ValueError):
            return "£0.00"

    def text_cell_html(label: str, value: str) -> str:
        return (
            f'<td class="cell"><div class="row-line">'
            f'<span class="mobile-label">{escape(label)}</span>'
            f'<span class="value">{escape(value)}</span>'
            f'</div></td>'
        )

    def num_cell_html(label: str, value: str) -> str:
        return (
            f'<td class="cell num"><div class="row-line">'
            f'<span class="mobile-label">{escape(label)}</span>'
            f'<span class="value">{value}</span>'
            f'</div></td>'
        )

    def row_html(item: dict) -> str:
        product = escape(str(item.get("title") or item.get("sku") or ""))
        sku = escape(str(item.get("sku") or ""))
        product_cell = (
            f'<td class="cell"><div class="row-line">'
            f'<span class="mobile-label">Product</span>'
            f'<span class="value">{product}</span>'
            f'</div><div class="sku">{sku}</div></td>'
        )
        pct_drop_text = f"{item.get('pct_drop', 0):.0f}%"
        return (
            f'<tr class="row">{product_cell}'
            f'{text_cell_html("Channel(s)", str(item.get("channels", "") or ""))}'
            f'{num_cell_html("Baseline", money(item.get("baseline_price", 0)))}'
            f'{num_cell_html("Today", money(item.get("today_price", 0)))}'
            f'{num_cell_html("Drop %", pct_drop_text)}'
            f'{text_cell_html("Source", str(item.get("baseline_source", "") or ""))}'
            f'</tr>'
        )

    rows_html = "".join(row_html(item) for item in items)

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{_card_css(badge_bg, badge_color)}</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <div class="header">
      <span class="badge">{escape(severity)}</span>
      <h1>Price drop detected</h1>
      <p class="subtitle">Price dropped more than the configured threshold vs. its baseline</p>
    </div>
    <table role="presentation">
      <thead><tr><th>Product</th><th>Channel(s)</th><th class="num">Baseline</th><th class="num">Today</th><th class="num">Drop %</th><th>Source</th></tr></thead>
      <tbody>
        {rows_html}
      </tbody>
    </table>
    <div class="footer">{escape(footer)}</div>
  </div>
</div>
</body></html>"""


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
