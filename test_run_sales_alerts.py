"""Unit tests for run_sales_alerts.py's self-healing window-check retry
and the price-drop check's same-day update/dedup."""

from __future__ import annotations

import pytest

import run_sales_alerts


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    monkeypatch.setattr(run_sales_alerts.time, "sleep", lambda seconds: None)


@pytest.fixture(autouse=True)
def no_real_activity_log(monkeypatch):
    monkeypatch.setattr(run_sales_alerts.activity_log, "log_activity", lambda *a, **k: None)


def test_check_window_succeeds_first_try(monkeypatch):
    monkeypatch.setattr(
        run_sales_alerts.ca_orders_client, "fetch_recent_order_rows",
        lambda minutes: [{"order_id": "O1", "supplier_sku": "SKU-A"}],
    )
    evaluated = []

    count, rows = run_sales_alerts._check_window(
        "1h", 60, lambda rows, *a: evaluated.append(rows), lookup={}, config={}, exceptions=set(), send_alert=None,
    )

    assert count == 1
    assert rows == [{"order_id": "O1", "supplier_sku": "SKU-A"}]
    assert evaluated == [[{"order_id": "O1", "supplier_sku": "SKU-A"}]]


def test_check_window_self_heals_after_transient_failure(monkeypatch):
    attempts = {"n": 0}

    def flaky_fetch(minutes):
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise RuntimeError("transient blip")
        return [{"order_id": "O1", "supplier_sku": "SKU-A"}]

    monkeypatch.setattr(run_sales_alerts.ca_orders_client, "fetch_recent_order_rows", flaky_fetch)

    count, rows = run_sales_alerts._check_window(
        "4h", 240, lambda rows, *a: None, lookup={}, config={}, exceptions=set(), send_alert=None,
    )

    assert attempts["n"] == 2  # failed once, succeeded on retry -- self-healed
    assert count == 1


def test_check_window_raises_after_exhausting_retries(monkeypatch):
    def always_fails(minutes):
        raise RuntimeError("ChannelAdvisor is down")

    monkeypatch.setattr(run_sales_alerts.ca_orders_client, "fetch_recent_order_rows", always_fails)

    with pytest.raises(RuntimeError, match="ChannelAdvisor is down"):
        run_sales_alerts._check_window(
            "1h", 60, lambda rows, *a: None, lookup={}, config={}, exceptions=set(), send_alert=None,
        )


@pytest.fixture(autouse=True)
def isolated_price_tracking(tmp_path, monkeypatch):
    monkeypatch.setattr(run_sales_alerts.price_tracking, "TRACKING_PATH", tmp_path / "price_tracking.csv")


@pytest.fixture(autouse=True)
def fake_price_check_config(monkeypatch):
    monkeypatch.setattr(
        run_sales_alerts.config_store, "load_price_check_config",
        lambda: {"price_drop_threshold_percent": 15, "recipients": ["a@example.com"]},
    )


def test_run_price_check_flags_a_real_drop_against_prior_record():
    run_sales_alerts.price_tracking.save_tracking(
        {"OUR-1": {"unit_price": 10.0, "last_updated_date": "2020-01-01"}}
    )

    sent = []
    rows = [{"our_sku": "OUR-1", "unit_price": 5.0, "title": "Widget", "channel": "Amazon UK"}]
    count = run_sales_alerts._run_price_check(
        rows, lambda subject, body, *, to, html_body=None: sent.append((subject, body, html_body)),
    )

    assert count == 1
    assert len(sent) == 1
    assert "[PRICE]" in sent[0][0]
    assert "OUR-1" in sent[0][1]


def test_run_price_check_no_prior_record_sends_nothing_first_time():
    rows = [{"our_sku": "OUR-1", "unit_price": 5.0, "title": "Widget", "channel": "Amazon UK"}]
    sent = []
    count = run_sales_alerts._run_price_check(rows, lambda *a, **k: sent.append(a))
    assert count == 0
    assert sent == []
    # but it's recorded now, so the NEXT day's run would have something to compare against
    tracking = run_sales_alerts.price_tracking.load_tracking()
    assert tracking["OUR-1"]["unit_price"] == 5.0


def test_run_price_check_does_not_repeat_alert_same_day():
    run_sales_alerts.price_tracking.save_tracking(
        {"OUR-1": {"unit_price": 10.0, "last_updated_date": "2020-01-01"}}
    )

    sent = []
    send_alert = lambda subject, body, *, to, html_body=None: sent.append(subject)
    rows = [{"our_sku": "OUR-1", "unit_price": 5.0, "title": "Widget", "channel": "Amazon UK"}]

    first_count = run_sales_alerts._run_price_check(rows, send_alert)
    second_count = run_sales_alerts._run_price_check(rows, send_alert)

    assert first_count == 1
    assert second_count == 0  # already updated today -- not re-compared/re-sent
    assert len(sent) == 1


def test_run_price_check_keeps_lowest_price_seen_today_across_calls():
    rows_high = [{"our_sku": "OUR-1", "unit_price": 9.0, "title": "Widget", "channel": "Amazon UK"}]
    rows_low = [{"our_sku": "OUR-1", "unit_price": 3.0, "title": "Widget", "channel": "Amazon UK"}]

    run_sales_alerts._run_price_check(rows_high, lambda *a, **k: None)
    run_sales_alerts._run_price_check(rows_low, lambda *a, **k: None)

    assert run_sales_alerts.price_tracking.load_tracking()["OUR-1"]["unit_price"] == 3.0


def test_run_price_check_no_drop_sends_nothing():
    run_sales_alerts.price_tracking.save_tracking(
        {"OUR-1": {"unit_price": 10.0, "last_updated_date": "2020-01-01"}}
    )
    rows = [{"our_sku": "OUR-1", "unit_price": 10.0, "title": "Widget", "channel": "Amazon UK"}]
    sent = []
    count = run_sales_alerts._run_price_check(rows, lambda *a, **k: sent.append(a))
    assert count == 0
    assert sent == []
