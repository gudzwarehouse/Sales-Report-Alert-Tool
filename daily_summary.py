"""Builds a daily summary of the pipeline's activity from the shared
activity log (activity_log.py), for a once-a-day status email to the
automation account.

Pure parsing/rendering functions here, no email-sending side effects, so
the summary logic is unit-testable against hand-built log lines.
"""

from __future__ import annotations

import datetime
import re
from html import escape

import activity_log

_LINE_RE = re.compile(r"^(?P<ts>\S+) \[(?P<level>\w+)\] (?P<component>\S+): (?P<message>.*)$")


_WINDOW_OK_RE = re.compile(r"^\[(?P<window>\w+)\] OK on attempt \d+: (?P<count>\d+) order line")


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
    }
    for raw in lines:
        match = _LINE_RE.match(raw.strip())
        if not match:
            continue
        level, message = match["level"], match["message"]
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
    return summary


def read_today_log_lines(date_str: str) -> list[str]:
    """Read today's shared log file (sales_pipeline_<date>.log). Returns [] if it doesn't exist yet."""
    import os
    path = os.path.join(activity_log.SHARED_LOG_DIR, f"sales_pipeline_{date_str}.log")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return f.readlines()


def render_summary_text(date_str: str, summary: dict) -> str:
    lines = [
        f"Sales Report Tool -- daily summary for {date_str}",
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
<div style="margin-top:8px;"><span style="background:{status_color};color:#ffffff;padding:3px 10px;border-radius:12px;font-size:12px;font-weight:600;">STATUS: {status_text}</span></div>
</div>
<table role="presentation" width="100%" style="width:100%;border-collapse:collapse;font-size:14px;">{stats_rows}</table>
{windows_section}
{alerts_section}
{failures_section}
<div style="font-size:12px;color:#9ca3af;border-top:1px solid #e5e7eb;margin-top:16px;padding-top:12px;">This is an automated daily summary from Sales Report Tool.</div>
</div></body></html>"""
