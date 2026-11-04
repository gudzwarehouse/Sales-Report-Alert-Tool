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
    assert "STATUS: FAILURE" in html  # has a failure
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
    assert subject == "Sales Report Tool | FAILURE | 11/04 | OP: Veer | Dev: Theekshana"


def test_render_subject_success_when_no_failures():
    summary = daily_summary.parse_log_lines(SAMPLE_LINES[:4])
    subject = daily_summary.render_subject("2026-11-04", summary)
    assert subject == "Sales Report Tool | SUCCESS | 11/04 | OP: Veer | Dev: Theekshana"
