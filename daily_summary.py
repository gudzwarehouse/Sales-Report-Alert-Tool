"""Builds a daily summary of the pipeline's activity from the shared
activity log (activity_log.py), for a once-a-day status email to the
automation account.

Pure parsing/rendering functions here, no email-sending side effects, so
the summary logic is unit-testable against hand-built log lines.
"""

from __future__ import annotations

import datetime
import json
import re
from html import escape

import activity_log

_LINE_RE = re.compile(r"^(?P<ts>\S+) \[(?P<level>\w+)\] (?P<component>\S+): (?P<message>.*)$")


_HIT_PREFIX = "SKU threshold hit: "
_TOTAL_PREFIX = "Total threshold hit: "
_WINDOW_NAMES = {
    "1h": "1-hour",
    "4h": "4-hour block (email alert, 00-04 / 04-08 / ...)",
    "4h_roll": "4-hour rolling, between block checks (no email)",
}
_WINDOW_OK_RE =re.compile(r"^\[(?P<window>\w+)\] OK on attempt \d+: (?P<count>\d+) order line")


def window_lines(summary: dict) -> list[str]:
    """One line per window: "1h: peak 17 order line(s) / lowest alert threshold 40 (no alert)".

    Peaks come from the log; "window_thresholds" ({"1h": 40, ...}) is optional
    and set by the caller from config. Makes a quiet window read as
    "checked, below threshold" rather than missing.
    """
    peaks = summary.get("window_peaks", {})
    thresholds = summary.get("window_thresholds", {})
    lines = []
    for window in sorted(peaks):
        line = f"{window}: peak {peaks[window]} order line(s)"
        threshold = thresholds.get(window)
        if threshold:
            state = "alert threshold reached" if peaks[window] >= threshold else "below threshold"
            line += f" / lowest alert threshold {threshold} ({state})"
        lines.append(line)
    return lines


def _uk_time(ts: str) -> str:
    """Log timestamps are written in the server's local (UK) time. "2026-11-05T14:05:09" -> "2:05 PM"."""
    try:
        t = datetime.datetime.fromisoformat(ts)
    except ValueError:
        return ""
    return t.strftime("%I:%M %p").lstrip("0")


def timed(time_str: str, text: str) -> str:
    return f"{time_str} - {text}" if time_str else text


def parse_log_lines(lines: list[str]) -> dict:
    """Summarize one day's worth of activity_log lines.

    Returns {runs_started, runs_completed, runs_failed, alerts_sent,
    alert_subjects: list[str], failures: list[str]}.
    """
    summary = {
        "runs_started": 0,
        "runs_completed": 0,
        "runs_failed": 0,
        "alerts_sent": 0,
        "alert_subjects": [],
        "failures": [],
        "alert_times": [],    # parallel to alert_subjects, UK "2:05 PM"
        "failure_times": [],  # parallel to failures
        "window_peaks": {},  # {"1h": highest order-line count seen, "4h": ...}
        "sku_hits": [],      # aggregated, see _aggregate_sku_hits
        "total_hits": {},    # {"1h": [{slot, total, threshold, severity}, ...], "4h": [...]}
    }
    raw_hits: list[dict] = []
    for raw in lines:
        match = _LINE_RE.match(raw.strip())
        if not match:
            continue
        level, message = match["level"], match["message"]
        if message.startswith(_HIT_PREFIX):
            try:
                raw_hits.append(json.loads(message[len(_HIT_PREFIX):]))
            except ValueError:
                pass
            continue
        if message.startswith(_TOTAL_PREFIX):
            try:
                total_hit = json.loads(message[len(_TOTAL_PREFIX):])
            except ValueError:
                continue
            by_slot = summary["total_hits"].setdefault(total_hit.get("window"), {})
            prev = by_slot.get(total_hit.get("slot"))
            if prev is None or (total_hit.get("total") or 0) >= (prev.get("total") or 0):
                by_slot[total_hit.get("slot")] = total_hit
            continue
        window_match = _WINDOW_OK_RE.match(message)
        if window_match:
            window, count = window_match["window"], int(window_match["count"])
            peaks = summary["window_peaks"]
            peaks[window] = max(peaks.get(window, 0), count)
        if message == "Run started":
            summary["runs_started"] += 1
        elif message.startswith("Run completed"):
            summary["runs_completed"] += 1
        elif message.startswith("Run FAILED") or level == "ERROR":
            summary["runs_failed"] += 1
            summary["failures"].append(message)
            summary["failure_times"].append(_uk_time(match["ts"]))
        elif message.startswith("Alert sent:"):
            summary["alerts_sent"] += 1
            alert_match = re.search(r'Alert sent: "(.+?)"', message)
            if alert_match:
                summary["alert_subjects"].append(alert_match.group(1))
                summary["alert_times"].append(_uk_time(match["ts"]))
    summary["sku_hits"] = _aggregate_sku_hits(raw_hits)
    summary["total_hits"] = {w: list(slots.values()) for w, slots in summary["total_hits"].items()}
    return summary


def _aggregate_sku_hits(raw_hits: list[dict]) -> list[dict]:
    """Collapse the per-run "SKU threshold hit" log lines (the same hour-aligned
    slot is re-evaluated every ~40 min) into one entry per (window, SKU):
    number of distinct slots hit, the slot with the peak order count (its
    product detail is the one shown), and the SKU's latest status.
    Alerted first, then by slots hit, then peak orders."""
    per_slot: dict[tuple, dict] = {}
    for hit in raw_hits:
        key = (hit.get("window"), hit.get("sku"), hit.get("slot"))
        prev = per_slot.get(key)
        if prev is None or (hit.get("orders") or 0) >= (prev.get("orders") or 0):
            per_slot[key] = hit
    latest_status: dict[tuple, str] = {}
    for hit in raw_hits:  # file order == chronological
        latest_status[(hit.get("window"), hit.get("sku"))] = hit.get("status", "")
    grouped: dict[tuple, dict] = {}
    for (window, sku, slot), hit in per_slot.items():
        entry = grouped.get((window, sku))
        if entry is None:
            entry = grouped[(window, sku)] = {**hit, "slots": [], "slot_orders": {}, "status": latest_status[(window, sku)]}
        entry["slots"].append(slot)
        entry["slot_orders"][slot] = hit.get("orders")
        if (hit.get("orders") or 0) > (entry.get("orders") or 0):
            for k in ("orders", "quantity", "sales_value", "title", "channels", "online_sku"):
                entry[k] = hit.get(k)
    result = list(grouped.values())
    for entry in result:
        entry["slots"] = sorted(set(entry["slots"]))
    result.sort(key=lambda e: (e["status"] != "alerted", -len(e["slots"]), -(e.get("orders") or 0)))
    return result


def _hit_headline(hit: dict) -> str:
    n = len(hit["slots"])
    ranges = "1 time range" if n == 1 else f"{n} time ranges"
    return f"Reached the threshold of {hit.get('threshold')} in {ranges}, peak {hit.get('orders')} orders"


def _hit_ranges(hit: dict) -> str:
    """"09:00-13:00 (4 orders), 10:00-14:00 (5 orders)" -- exact UTC ranges that exceeded."""
    orders = hit.get("slot_orders", {})
    return ", ".join(f"{s} ({orders[s]} orders)" if orders.get(s) else s for s in hit["slots"])


def _total_line(hit: dict) -> str:
    tail = f", {hit['severity']}" if hit.get("severity") else ""
    return f"{hit['slot']} UTC: {hit['total']} orders (threshold {hit['threshold']}{tail})"


def _hit_status_label(hit: dict) -> str:
    if hit["status"] != "alerted":
        return "EXCLUDED - no alert sent"
    return "REACHED - in-between check, no email" if hit["window"] == "4h_roll" else "ALERT SENT"


def read_today_log_lines(date_str: str) -> list[str]:
    """Read today's shared log file (sales_pipeline_<date>.log). Returns [] if it doesn't exist yet."""
    import os
    path = os.path.join(activity_log.SHARED_LOG_DIR, f"sales_pipeline_{date_str}.log")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return f.readlines()


def read_last_hours_log_lines(now: datetime.datetime, hours: int = 24) -> list[str]:
    """Log lines from the trailing ``hours`` before ``now`` (server-local time),
    read from today's and the previous day's shared log files."""
    cutoff = now - datetime.timedelta(hours=hours)
    out: list[str] = []
    for day in (cutoff.date(), now.date()) if cutoff.date() != now.date() else (now.date(),):
        for line in read_today_log_lines(day.isoformat()):
            try:
                ts = datetime.datetime.fromisoformat(line.split(" ", 1)[0])
            except ValueError:
                continue
            if cutoff <= ts <= now:
                out.append(line)
    return out


def period_label(now: datetime.datetime, hours: int = 24) -> str:
    start = now - datetime.timedelta(hours=hours)
    return f"Last {hours}h: {start:%Y-%m-%d %H:%M} to {now:%Y-%m-%d %H:%M} (UK time)"


def render_summary_text(date_str: str, summary: dict) -> str:
    lines = [
        f"Sales Report Tool -- daily summary for {date_str}",
        *([summary["period"]] if summary.get("period") else []),
        "",
        f"Runs started:   {summary['runs_started']}",
        f"Runs completed: {summary['runs_completed']}",
        f"Runs failed:    {summary['runs_failed']}",
        f"Alerts sent:    {summary['alerts_sent']}",
    ]
    if window_lines(summary):
        lines.append("")
        lines.append("Order volume checked today:")
        lines.extend(f"  - {w}" for w in window_lines(summary))
    if summary["alert_subjects"]:
        lines.append("")
        lines.append("Alerts sent today (UK time):")
        for t, subject in zip(summary["alert_times"], summary["alert_subjects"]):
            lines.append(f"  - {timed(t, subject)}")
    for window in ("1h", "4h", "4h_roll"):
        name = _WINDOW_NAMES[window]
        totals = summary.get("total_hits", {}).get(window, [])
        if totals:
            lines.append("")
            lines.append(f"{name} -- TOTAL orders over threshold:")
            lines.extend(f"  - {_total_line(t)}" for t in totals)
        hits = [h for h in summary.get("sku_hits", []) if h["window"] == window]
        if hits:
            lines.append("")
            lines.append(f"{name} -- per-SKU threshold:")
            for hit in hits:
                lines.append(f"  - [{_hit_status_label(hit)}] {hit.get('title') or hit['sku']}")
                lines.append(f"      SKU: {hit['sku']}  Online SKU: {hit.get('online_sku') or '-'}")
                sales = float(hit.get("sales_value") or 0)
                lines.append(f"      {_hit_headline(hit)}; peak qty {hit.get('quantity')}, sales £{sales:,.2f}")
                if hit.get("channels"):
                    lines.append(f"      Channel(s): {hit['channels']}")
                lines.append(f"      Time ranges (UTC): {_hit_ranges(hit)}")
    if summary["failures"]:
        lines.append("")
        lines.append("Warnings:")
        for t, failure in zip(summary["failure_times"], summary["failures"]):
            lines.append(f"  - {timed(t, failure)}")
    return "\n".join(lines)


def summary_status(summary: dict) -> str:
    """SUCCESS or WARNING, for the subject line and the HTML status pill."""
    return "WARNING" if summary["runs_failed"] else "SUCCESS"


def render_subject(date_str: str, summary: dict, *, op: str = "Veer", dev: str = "Theekshana") -> str:
    """Pipe-separated subject keyword style: "<Tool> | <STATUS> | <MM/DD> | OP: <name> | Dev: <name>"."""
    status = summary_status(summary)
    mmdd = datetime.date.fromisoformat(date_str).strftime("%m/%d")
    return f"Sales Report Tool | {status} | {mmdd} | OP: {op} | Dev: {dev}"


def render_summary_html(date_str: str, summary: dict) -> str:
    status_text = summary_status(summary)
    status_color = "#b45309" if status_text == "WARNING" else "#15803d"

    def _rows(items: list[tuple[str, object]]) -> str:
        return "".join(
            '<tr><td style="padding:6px 10px;color:#6b7280;border-bottom:1px solid #e5e7eb;">'
            f'{escape(label)}</td><td style="padding:6px 10px;font-weight:600;text-align:right;'
            f'border-bottom:1px solid #e5e7eb;">{escape(str(value))}</td></tr>'
            for label, value in items
        )

    stats_rows = _rows([
        ("Runs started", summary["runs_started"]),
        ("Runs completed", summary["runs_completed"]),
        ("Runs failed", summary["runs_failed"]),
        ("Alerts sent", summary["alerts_sent"]),
    ])

    windows_section = ""
    if window_lines(summary):
        windows_section = (
            '<div style="margin-top:16px;"><div style="font-size:11px;font-weight:700;'
            'letter-spacing:.04em;text-transform:uppercase;color:#6b7280;margin-bottom:6px;">'
            "Order volume checked today</div><ul style=\"margin:0;padding-left:20px;color:#1f2937;word-wrap:break-word;overflow-wrap:anywhere;\">"
            + "".join(f"<li>{escape(w)}</li>" for w in window_lines(summary))
            + "</ul></div>"
        )

    alerts_section = ""
    if summary["alert_subjects"]:
        alerts_section = (
            '<div style="margin-top:16px;"><div style="font-size:11px;font-weight:700;'
            'letter-spacing:.04em;text-transform:uppercase;color:#6b7280;margin-bottom:6px;">'
            "Alerts sent today (UK time)</div><ul style=\"margin:0;padding-left:20px;color:#1f2937;word-wrap:break-word;overflow-wrap:anywhere;\">"
            + "".join(f"<li>{escape(timed(t, s))}</li>" for t, s in zip(summary["alert_times"], summary["alert_subjects"]))
            + "</ul></div>"
        )

    def hit_block(hit: dict) -> str:
        sent = hit["status"] == "alerted"
        pill_bg, pill_fg = ("#fffbeb", "#b45309") if sent else ("#f3f4f6", "#4b5563")
        sku_parts = f"SKU: {escape(hit['sku'])}"
        if hit.get("online_sku"):
            sku_parts += f" &middot; Online SKU: {escape(hit['online_sku'])}"
        channels = (
            f'<div style="font-size:12px;color:#6b7280;margin-top:2px;">{escape(hit["channels"])}</div>'
            if hit.get("channels") else ""
        )
        stat = lambda label, value: (  # noqa: E731
            '<td width="33%" valign="top" style="padding:0 4px 0 0;">'
            f'<div style="font-size:11px;text-transform:uppercase;color:#9ca3af;">{label}</div>'
            f'<div style="font-size:15px;font-weight:600;color:#111827;">{escape(str(value))}</div></td>'
        )
        return (
            '<div style="padding:12px 0;border-bottom:1px solid #f3f4f6;">'
            f'<span style="display:inline-block;padding:2px 10px;border-radius:999px;font-size:11px;font-weight:700;background:{pill_bg};color:{pill_fg};">{escape(_hit_status_label(hit))}</span>'
            f'<div style="font-size:14px;font-weight:600;color:#111827;margin-top:6px;word-wrap:break-word;overflow-wrap:anywhere;">{escape(str(hit.get("title") or hit["sku"]))}</div>'
            f'<div style="font-size:12px;color:#9ca3af;margin-top:2px;word-break:break-all;">{sku_parts}</div>'
            f'{channels}'
            f'<div style="font-size:13px;color:#374151;margin-top:6px;">{escape(_hit_headline(hit))}</div>'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="width:100%;margin-top:8px;border-collapse:collapse;"><tr>'
            f'{stat("Peak orders", hit.get("orders"))}{stat("Qty (peak)", hit.get("quantity"))}'
            f'{stat("Sales (peak)", "£%s" % format(float(hit.get("sales_value") or 0), ",.2f"))}'
            '</tr></table>'
            f'<div style="font-size:11px;color:#9ca3af;margin-top:6px;">Time ranges (UTC): {escape(_hit_ranges(hit))}</div>'
            '</div>'
        )

    label_style = ("font-size:11px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;"
                   "color:#6b7280;margin-bottom:2px;")
    hits_section = ""
    for window in ("1h", "4h", "4h_roll"):
        name = _WINDOW_NAMES[window]
        totals = summary.get("total_hits", {}).get(window, [])
        if totals:
            hits_section += (
                f'<div style="margin-top:16px;"><div style="{label_style}">{name} -- TOTAL orders over threshold</div>'
                '<ul style="margin:4px 0 0;padding-left:20px;color:#1f2937;">'
                + "".join(f"<li>{escape(_total_line(t))}</li>" for t in totals)
                + "</ul></div>"
            )
        hits = [h for h in summary.get("sku_hits", []) if h["window"] == window]
        if hits:
            hits_section += (
                f'<div style="margin-top:16px;"><div style="{label_style}">{name} -- per-SKU threshold</div>'
                '<div style="font-size:12px;color:#9ca3af;">Excluded SKUs are on the exclude list, so they reach the threshold without sending an alert.</div>'
                + "".join(hit_block(h) for h in hits)
                + "</div>"
            )

    failures_section = ""
    if summary["failures"]:
        failures_section = (
            '<div style="margin-top:16px;background:#fffbeb;border:1px solid #fcd34d;'
            'padding:12px;border-radius:6px;font-size:13px;color:#92400e;">'
            "<strong>Warnings</strong><ul style=\"margin:4px 0 0;padding-left:20px;\">"
            + "".join(f"<li>{escape(timed(t, f))}</li>" for t, f in zip(summary["failure_times"], summary["failures"]))
            + "</ul></div>"
        )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1f2937;line-height:1.5;margin:0;padding:12px;background:#ffffff;">
<div style="max-width:600px;margin:0 auto;">
<div style="border-bottom:2px solid #e5e7eb;padding-bottom:12px;margin-bottom:16px;">
<h2 style="margin:0 0 6px;color:#111827;">Sales Report Tool -- daily summary</h2>
<div style="font-size:13px;color:#6b7280;">{escape(date_str)}</div>
{('<div style="font-size:12px;color:#6b7280;">' + escape(summary["period"]) + '</div>') if summary.get("period") else ""}
<div style="margin-top:8px;"><span style="background:{status_color};color:#ffffff;padding:3px 10px;border-radius:12px;font-size:12px;font-weight:600;">STATUS: {status_text}</span></div>
</div>
<table role="presentation" width="100%" style="width:100%;border-collapse:collapse;font-size:14px;">{stats_rows}</table>
{windows_section}
{alerts_section}
{hits_section}
{failures_section}
<div style="font-size:12px;color:#9ca3af;border-top:1px solid #e5e7eb;margin-top:16px;padding-top:12px;">This is an automated daily summary from Sales Report Tool.</div>
</div></body></html>"""
