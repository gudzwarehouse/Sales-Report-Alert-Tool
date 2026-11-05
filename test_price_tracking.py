"""Unit tests for price_tracking.py's persistent per-SKU CSV store."""

from __future__ import annotations

import price_tracking


def test_load_tracking_missing_file_returns_empty_dict(tmp_path, monkeypatch):
    monkeypatch.setattr(price_tracking, "TRACKING_PATH", tmp_path / "price_tracking.csv")
    assert price_tracking.load_tracking() == {}


def test_save_then_load_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(price_tracking, "TRACKING_PATH", tmp_path / "price_tracking.csv")
    records = {
        "OUR-1": {"unit_price": 9.99, "last_updated_date": "2026-11-05"},
        "OUR-2": {"unit_price": 4.5, "last_updated_date": "2026-11-06"},
    }
    price_tracking.save_tracking(records)
    loaded = price_tracking.load_tracking()
    assert loaded == records


def test_save_overwrites_previous_contents(tmp_path, monkeypatch):
    monkeypatch.setattr(price_tracking, "TRACKING_PATH", tmp_path / "price_tracking.csv")
    price_tracking.save_tracking({"OUR-1": {"unit_price": 1.0, "last_updated_date": "2026-11-05"}})
    price_tracking.save_tracking({"OUR-2": {"unit_price": 2.0, "last_updated_date": "2026-11-06"}})
    assert price_tracking.load_tracking() == {"OUR-2": {"unit_price": 2.0, "last_updated_date": "2026-11-06"}}


def test_export_to_shared_xlsx_writes_a_real_workbook(tmp_path, monkeypatch):
    xlsx_path = tmp_path / "shared" / "Price Comparison Order.xlsx"
    monkeypatch.setattr(price_tracking, "SHARED_XLSX_PATH", str(xlsx_path))

    price_tracking.export_to_shared_xlsx({"OUR-1": {"unit_price": 9.99, "last_updated_date": "2026-11-05"}})

    assert xlsx_path.exists()
    import openpyxl
    sheet = openpyxl.load_workbook(xlsx_path).active
    assert sheet["A1"].value == "SKU"
    assert sheet["A2"].value == "OUR-1"
    assert sheet["B2"].value == 9.99


def test_export_to_shared_xlsx_never_raises_on_failure(monkeypatch):
    """A share/network hiccup must not break the real pipeline run."""
    monkeypatch.setattr(price_tracking, "SHARED_XLSX_PATH", "Z:\\does-not-exist\\unreachable\\file.xlsx")
    price_tracking.export_to_shared_xlsx({"OUR-1": {"unit_price": 1.0, "last_updated_date": "2026-11-05"}})  # no raise
