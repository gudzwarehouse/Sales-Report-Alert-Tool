"""Unit tests for run_sales_alerts.py's self-healing window-check retry."""

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

    count = run_sales_alerts._check_window(
        "1h", 60, lambda rows, *a: evaluated.append(rows), lookup={}, config={}, exceptions=set(), send_alert=None,
    )

    assert count == 1
    assert evaluated == [[{"order_id": "O1", "supplier_sku": "SKU-A"}]]


def test_check_window_self_heals_after_transient_failure(monkeypatch):
    attempts = {"n": 0}

    def flaky_fetch(minutes):
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise RuntimeError("transient blip")
        return [{"order_id": "O1", "supplier_sku": "SKU-A"}]

    monkeypatch.setattr(run_sales_alerts.ca_orders_client, "fetch_recent_order_rows", flaky_fetch)

    count = run_sales_alerts._check_window(
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
