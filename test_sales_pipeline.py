"""Unit tests for sales_pipeline.py's pure functions, against hand-built fixtures."""

from __future__ import annotations

import csv
from pathlib import Path

from sales_pipeline import (
    check_sku_thresholds,
    check_total_threshold,
    count_by_sku,
    count_distinct_orders,
    load_orders_csv,
    map_to_our_sku,
    run_1h_check,
    run_4h_check,
)


def write_csv(path: Path, rows: list[list[str]]) -> Path:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["OrderId", "B", "C", "SupplierSku", "E", "F", "G", "H", "I", "J"])
        writer.writerows(rows)
    return path


def test_load_orders_csv_reads_order_id_and_supplier_sku(tmp_path):
    path = write_csv(tmp_path / "orders.csv", [
        ["O1", "", "", "SUP-1", "", "", "", "", "", ""],
        ["O2", "", "", "SUP-2", "", "", "", "", "", ""],
    ])
    rows = load_orders_csv(path)
    assert rows == [
        {"order_id": "O1", "supplier_sku": "SUP-1"},
        {"order_id": "O2", "supplier_sku": "SUP-2"},
    ]


def test_map_to_our_sku_unmatched_rows_get_blank():
    rows = [{"order_id": "O1", "supplier_sku": "SUP-1"}, {"order_id": "O2", "supplier_sku": "UNKNOWN"}]
    mapped = map_to_our_sku(rows, {"SUP-1": "OUR-1"})
    assert mapped[0]["our_sku"] == "OUR-1"
    assert mapped[1]["our_sku"] == ""


def test_count_by_sku():
    rows = [{"our_sku": "A"}, {"our_sku": "A"}, {"our_sku": "B"}]
    assert count_by_sku(rows) == {"A": 2, "B": 1}


def test_count_distinct_orders_dedupes_by_order_id():
    rows = [{"order_id": "O1"}, {"order_id": "O1"}, {"order_id": "O2"}]
    assert count_distinct_orders(rows) == 2


# Buckets mirror the real sheet: the higher threshold's recipients include
# two extra Zapier SMS/phone-call trigger addresses the lower one doesn't.
EMAIL_ONLY = ["alice@example.com", "bob@example.com"]
EMAIL_PLUS_SMS = EMAIL_ONLY + ["sms@zapiermail.com", "call@zapiermail.com"]
SKU_BUCKETS = [
    {"sku_threshold": 3, "total_threshold": 40, "recipients": EMAIL_ONLY},
    {"sku_threshold": 6, "total_threshold": 45, "recipients": EMAIL_PLUS_SMS},
]


def test_check_sku_thresholds_removes_after_higher_match_and_uses_that_buckets_recipients():
    # SKU A (count 10) should only be flagged under the 6-threshold bucket,
    # not re-flagged again under the 3-threshold bucket -- and use the
    # 6-threshold bucket's own (SMS-including) recipients, not shared ones.
    counts = {"A": 10, "B": 4, "C": 2}
    results = check_sku_thresholds(counts, SKU_BUCKETS, exceptions=set())
    assert results == [
        (6, EMAIL_PLUS_SMS, [("A", 10)]),
        (3, EMAIL_ONLY, [("B", 4)]),
    ]


def test_check_sku_thresholds_skips_exceptions():
    counts = {"A": 10, "EXC": 10}
    buckets = [{"sku_threshold": 6, "total_threshold": 45, "recipients": EMAIL_PLUS_SMS}]
    results = check_sku_thresholds(counts, buckets, exceptions={"EXC"})
    assert results == [(6, EMAIL_PLUS_SMS, [("A", 10)])]


def test_check_total_threshold_highest_match_wins_with_its_own_recipients():
    buckets = [
        {"sku_threshold": 3, "total_threshold": 40, "recipients": EMAIL_ONLY},
        {"sku_threshold": 6, "total_threshold": 45, "recipients": EMAIL_PLUS_SMS},
    ]
    assert check_total_threshold(50, buckets) == (45, EMAIL_PLUS_SMS)
    assert check_total_threshold(42, buckets) == (40, EMAIL_ONLY)
    assert check_total_threshold(10, buckets) is None


def test_run_1h_check_sends_sku_and_total_alerts_and_deletes_file(tmp_path):
    path = write_csv(tmp_path / "Orders Last 1 hour.csv", [
        [f"O{i}", "", "", "SUP-1", "", "", "", "", "", ""] for i in range(10)
    ])
    sent = []
    config = {"1h": [{"sku_threshold": 3, "total_threshold": 5, "recipients": EMAIL_ONLY}]}

    def fake_send_alert(subject, body, *, to):
        sent.append((subject, body, to))

    run_1h_check(path, {"SUP-1": "OUR-1"}, config, exceptions=set(), send_alert=fake_send_alert)

    assert not path.exists()  # 1h file is deleted after processing
    subjects = [s for s, _, _ in sent]
    assert "CA Sales - Orders last hour" in subjects  # per-SKU alert
    assert "CA Sales - Orders last 1 hour" in subjects  # total alert
    assert all(to == EMAIL_ONLY for _, _, to in sent)


def test_run_1h_check_crossing_higher_threshold_includes_sms_recipients(tmp_path):
    path = write_csv(tmp_path / "Orders Last 1 hour.csv", [
        [f"O{i}", "", "", "SUP-1", "", "", "", "", "", ""] for i in range(7)
    ])
    sent = []
    config = {"1h": SKU_BUCKETS}

    run_1h_check(
        path, {"SUP-1": "OUR-1"}, config, exceptions=set(),
        send_alert=lambda subject, body, *, to: sent.append((subject, body, to)),
    )

    sku_alert = next(s for s in sent if s[0] == "CA Sales - Orders last hour")
    assert sku_alert[2] == EMAIL_PLUS_SMS  # 7 >= 6-threshold bucket -> SMS/call addresses included


def test_run_4h_check_does_not_delete_file(tmp_path):
    path = write_csv(tmp_path / "Orders Last 4 hours.csv", [
        ["O1", "", "", "SUP-1", "", "", "", "", "", ""],
    ])
    config = {"4h": []}

    run_4h_check(path, {"SUP-1": "OUR-1"}, config, exceptions=set(), send_alert=lambda *a, **k: None)

    assert path.exists()  # 4h file is NOT deleted, matching ReadLast4Hours
