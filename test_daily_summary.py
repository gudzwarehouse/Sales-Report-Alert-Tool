"""Unit tests for daily_summary.py's log-parsing and rendering."""

from __future__ import annotations

import daily_summary

SAMPLE_LINES = [
    "2026-11-04T09:00:00 [INFO] sales_pipeline: Run started\n",
    "2026-11-04T09:00:05 [INFO] sales_pipeline: Loaded 281925 SKU mappings from StockFeeds.xlsm\n",
    '2026-11-04T09:00:10 [INFO] sales_pipeline: Alert sent: "CA Sales - Orders last hour" to 11 recipient(s)\n',
    "2026-11-04T09:00:12 [INFO] sales_pipeline: Run completed in 12.3s: 1 1h file(s), 0 4h file(s), 1 alert(s) sent\n",
    "2026-11-04T09:40:00 [INFO] sales_pipeline: Run started\n",
    "2026-11-04T09:40:30 [ERROR] sales_pipeline: Run FAILED after 30.1s: network timeout\n",
]


def test_parse_log_lines_counts_everything():
    summary = daily_summary.parse_log_lines(SAMPLE_LINES)
    assert summary["runs_started"] == 2
    assert summary["runs_completed"] == 1
    assert summary["runs_failed"] == 1
    assert summary["alerts_sent"] == 1
    assert summary["alert_subjects"] == ["CA Sales - Orders last hour"]
    assert summary["failures"] == ["Run FAILED after 30.1s: network timeout"]


def test_parse_log_lines_ignores_malformed_lines():
    summary = daily_summary.parse_log_lines(["not a log line", "", "   "])
    assert summary["runs_started"] == 0
    assert summary["alerts_sent"] == 0


def test_parse_log_lines_empty_input():
    summary = daily_summary.parse_log_lines([])
    assert summary == {
        "runs_started": 0,
        "runs_completed": 0,
        "runs_failed": 0,
        "alerts_sent": 0,
        "alert_subjects": [],
        "failures": [],
        "alert_times": [],
        "failure_times": [],
        "window_peaks": {},
    }


def test_render_summary_text_includes_counts_and_subjects():
    summary = daily_summary.parse_log_lines(SAMPLE_LINES)
    text = daily_summary.render_summary_text("2026-11-04", summary)
    assert "Runs started:   2" in text
    assert "Alerts sent:    1" in text
    assert "CA Sales - Orders last hour" in text
    assert "network timeout" in text


def test_render_summary_html_escapes_and_includes_status():
    summary = daily_summary.parse_log_lines(SAMPLE_LINES)
    html = daily_summary.render_summary_html("2026-11-04", summary)
    assert "<html" in html
    assert "STATUS: WARNING" in html  # has a failure
    assert "CA Sales - Orders last hour" in html


def test_render_summary_html_status_ok_when_no_failures():
    summary = daily_summary.parse_log_lines(SAMPLE_LINES[:4])  # no failure line
    html = daily_summary.render_summary_html("2026-11-04", summary)
    assert "STATUS: SUCCESS" in html


def test_read_today_log_lines_returns_empty_for_missing_file(tmp_path, monkeypatch):
    import activity_log
    monkeypatch.setattr(activity_log, "SHARED_LOG_DIR", str(tmp_path))
    assert daily_summary.read_today_log_lines("1999-01-01") == []


def test_render_subject_matches_pipe_keyword_style():
    summary = daily_summary.parse_log_lines(SAMPLE_LINES)
    subject = daily_summary.render_subject("2026-11-04", summary)
    assert subject == "Sales Report Tool | WARNING | 11/04 | OP: Veer | Dev: Theekshana"


def test_render_subject_success_when_no_failures():
    summary = daily_summary.parse_log_lines(SAMPLE_LINES[:4])
    subject = daily_summary.render_subject("2026-11-04", summary)
    assert subject == "Sales Report Tool | SUCCESS | 11/04 | OP: Veer | Dev: Theekshana"


def test_window_peaks_and_threshold_lines():
    lines = [
        "2026-11-05T11:27:14 [INFO] sales_pipeline: [1h] OK on attempt 1: 5 order line(s)",
        "2026-11-05T11:30:59 [INFO] sales_pipeline: [1h] OK on attempt 1: 17 order line(s)",
        "2026-11-05T11:31:59 [INFO] sales_pipeline: [4h] OK on attempt 2: 144 order line(s)",
    ]
    summary = daily_summary.parse_log_lines(lines)
    assert summary["window_peaks"] == {"1h": 17, "4h": 144}
    summary["window_thresholds"] = {"1h": 40, "4h": 120}
    out = daily_summary.window_lines(summary)
    assert "1h: peak 17 order line(s) / lowest alert threshold 40 (below threshold)" in out
    assert "4h: peak 144 order line(s) / lowest alert threshold 120 (alert threshold reached)" in out
    assert "1h: peak 17" in daily_summary.render_summary_html("2026-11-05", summary)


def test_alerts_and_failures_show_uk_time_am_pm():
    lines = [
        '2026-11-05T09:05:00 [INFO] sales_pipeline: Alert sent: "A morning" to 3 recipient(s)',
        '2026-11-05T14:40:00 [INFO] sales_pipeline: Alert sent: "An afternoon" to 3 recipient(s)',
        "2026-11-05T00:10:00 [ERROR] sales_pipeline: Run FAILED after 1.0s: boom",
    ]
    text = daily_summary.render_summary_text("2026-11-05", daily_summary.parse_log_lines(lines))
    assert "9:05 AM - A morning" in text
    assert "2:40 PM - An afternoon" in text
    assert "12:10 AM - Run FAILED after 1.0s: boom" in text
