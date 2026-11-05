"""Unit tests for price_check.py's pure functions."""

from __future__ import annotations

from price_check import build_today_price_snapshot, check_and_update_price_tracking


def test_build_today_price_snapshot_picks_minimum_price_per_sku():
    rows = [
        {"our_sku": "OUR-1", "unit_price": 10.0, "title": "Widget", "channel": "Amazon UK"},
        {"our_sku": "OUR-1", "unit_price": 7.0, "title": "Widget", "channel": "Temu UK"},
        {"our_sku": "OUR-1", "unit_price": 9.0, "title": "Widget", "channel": "Amazon UK"},
    ]
    prices, details = build_today_price_snapshot(rows)
    assert prices == {"OUR-1": 7.0}
    assert details["OUR-1"] == {"title": "Widget", "channel": "Temu UK"}


def test_build_today_price_snapshot_skips_blank_sku_and_missing_price():
    rows = [
        {"our_sku": "", "unit_price": 1.0},
        {"our_sku": "OUR-1", "unit_price": None},
        {"our_sku": "OUR-2", "unit_price": 5.0},
    ]
    prices, details = build_today_price_snapshot(rows)
    assert prices == {"OUR-2": 5.0}


def test_no_prior_record_just_logs_new_record_no_comparison():
    tracking: dict = {}
    drops = check_and_update_price_tracking({"OUR-1": 5.0}, tracking, "2026-11-06", threshold_percent=15)
    assert drops == []
    assert tracking == {"OUR-1": {"unit_price": 5.0, "last_updated_date": "2026-11-06"}}


def test_prior_record_from_earlier_day_compares_and_flags_over_threshold():
    tracking = {"OUR-1": {"unit_price": 10.0, "last_updated_date": "2026-11-01"}}
    drops = check_and_update_price_tracking({"OUR-1": 8.5}, tracking, "2026-11-06", threshold_percent=15)
    assert drops == [{
        "sku": "OUR-1", "baseline_price": 10.0, "today_price": 8.5,
        "pct_drop": 15.0, "baseline_source": "Last recorded 2026-11-01",
    }]
    # record updated to today regardless
    assert tracking["OUR-1"] == {"unit_price": 8.5, "last_updated_date": "2026-11-06"}


def test_prior_record_from_earlier_day_below_threshold_updates_but_no_flag():
    tracking = {"OUR-1": {"unit_price": 10.0, "last_updated_date": "2026-11-01"}}
    drops = check_and_update_price_tracking({"OUR-1": 9.5}, tracking, "2026-11-06", threshold_percent=15)
    assert drops == []
    assert tracking["OUR-1"] == {"unit_price": 9.5, "last_updated_date": "2026-11-06"}


def test_record_already_today_skips_comparison_but_keeps_lower_price():
    tracking = {"OUR-1": {"unit_price": 9.0, "last_updated_date": "2026-11-06"}}
    # Price far below 9.0 would normally be a huge drop, but since the
    # record is already dated today, no comparison runs -- just update.
    drops = check_and_update_price_tracking({"OUR-1": 1.0}, tracking, "2026-11-06", threshold_percent=15)
    assert drops == []
    assert tracking["OUR-1"] == {"unit_price": 1.0, "last_updated_date": "2026-11-06"}


def test_record_already_today_does_not_raise_price_back_up():
    tracking = {"OUR-1": {"unit_price": 5.0, "last_updated_date": "2026-11-06"}}
    drops = check_and_update_price_tracking({"OUR-1": 9.0}, tracking, "2026-11-06", threshold_percent=15)
    assert drops == []
    assert tracking["OUR-1"]["unit_price"] == 5.0  # the lower price already seen today is kept


def test_zero_baseline_price_is_skipped_not_flagged():
    tracking = {"OUR-1": {"unit_price": 0.0, "last_updated_date": "2026-11-01"}}
    drops = check_and_update_price_tracking({"OUR-1": 1.0}, tracking, "2026-11-06", threshold_percent=15)
    assert drops == []
    assert tracking["OUR-1"] == {"unit_price": 1.0, "last_updated_date": "2026-11-06"}


def test_sorted_worst_drop_first():
    tracking = {
        "OUR-1": {"unit_price": 10.0, "last_updated_date": "2026-11-01"},
        "OUR-2": {"unit_price": 10.0, "last_updated_date": "2026-11-01"},
    }
    drops = check_and_update_price_tracking(
        {"OUR-1": 9.0, "OUR-2": 2.0}, tracking, "2026-11-06", threshold_percent=5,
    )
    assert [d["sku"] for d in drops] == ["OUR-2", "OUR-1"]
