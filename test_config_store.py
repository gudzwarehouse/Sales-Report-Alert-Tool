"""Unit tests for config_store.py -- isolated from the real config/ files via monkeypatched paths."""

from __future__ import annotations

import json

import pytest

import config_store


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    thresholds_path = tmp_path / "thresholds.json"
    exceptions_path = tmp_path / "exceptions.json"
    monkeypatch.setattr(config_store, "THRESHOLDS_PATH", thresholds_path)
    monkeypatch.setattr(config_store, "EXCEPTIONS_PATH", exceptions_path)
    return thresholds_path, exceptions_path


VALID_THRESHOLDS = {
    "1h": [
        {"sku_threshold": 3, "total_threshold": 40, "recipients": ["a@example.com"]},
        {"sku_threshold": 6, "total_threshold": 45, "recipients": ["a@example.com", "sms@zapiermail.com"]},
    ],
    "4h": [
        {"sku_threshold": 10, "total_threshold": 120, "recipients": ["a@example.com"]},
        {"sku_threshold": 20, "total_threshold": 145, "recipients": ["a@example.com", "sms@zapiermail.com"]},
    ],
}


def test_save_then_load_thresholds_round_trips(isolated_config):
    config_store.save_thresholds(VALID_THRESHOLDS)
    assert config_store.load_thresholds() == VALID_THRESHOLDS


def test_save_thresholds_rejects_missing_window(isolated_config):
    bad = {"1h": VALID_THRESHOLDS["1h"]}
    with pytest.raises(ValueError, match="windows"):
        config_store.save_thresholds(bad)


def test_save_thresholds_rejects_non_positive_threshold(isolated_config):
    bad = json.loads(json.dumps(VALID_THRESHOLDS))
    bad["1h"][0]["sku_threshold"] = 0
    with pytest.raises(ValueError, match="positive"):
        config_store.save_thresholds(bad)


def test_save_thresholds_rejects_empty_recipients(isolated_config):
    bad = json.loads(json.dumps(VALID_THRESHOLDS))
    bad["1h"][0]["recipients"] = []
    with pytest.raises(ValueError, match="recipient"):
        config_store.save_thresholds(bad)


def test_save_thresholds_rejects_malformed_email(isolated_config):
    bad = json.loads(json.dumps(VALID_THRESHOLDS))
    bad["1h"][0]["recipients"] = ["not-an-email"]
    with pytest.raises(ValueError, match="invalid recipient"):
        config_store.save_thresholds(bad)


def test_save_exceptions_dedupes_case_sensitively_and_strips_blanks(isolated_config):
    saved = config_store.save_exceptions(["SKU1", "SKU1", "sku1", "  SKU2  ", "", "  "])
    assert saved == ["SKU1", "sku1", "SKU2"]
    assert config_store.load_exceptions() == ["SKU1", "sku1", "SKU2"]
