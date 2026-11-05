"""Unit tests for ca_orders_client.py against a fake httpx transport --
no real network calls, no real credentials needed.
"""

from __future__ import annotations

import httpx
import pytest

import ca_orders_client


@pytest.fixture(autouse=True)
def fake_env(monkeypatch):
    monkeypatch.setenv("CA_UK_APPLICATION_ID", "app-id")
    monkeypatch.setenv("CA_UK_SHARED_SECRET", "secret")
    monkeypatch.setenv("CA_UK_REFRESH_TOKEN", "refresh-token")
    monkeypatch.setenv("CA_UK_PROFILE_ID", "12345")
    monkeypatch.setenv("CA_TOKEN_URL", "https://fake/oauth2/token")
    monkeypatch.setenv("CA_ORDERS_API_URL", "https://fake/v1/Orders")
    monkeypatch.setenv("CA_PRODUCTS_API_URL", "https://fake/v1/Products")
    # Make load_dotenv() inside _config() a no-op so the fixture's env vars aren't overwritten.
    monkeypatch.setattr(ca_orders_client, "load_dotenv", lambda *a, **k: None)


def _token_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"access_token": "fake-token"})


def _make_order(site_order_id: str, skus: list[str], *, channel: str = "Amazon UK") -> dict:
    return {"SiteOrderID": site_order_id, "SiteName": channel, "Items": [
        {"Sku": sku, "Title": f"Product {sku}", "Quantity": 2, "UnitPrice": 9.99} for sku in skus
    ]}


def _expected_row(order_id: str, sku: str, *, channel: str = "Amazon UK") -> dict:
    return {
        "order_id": order_id, "supplier_sku": sku,
        "title": f"Product {sku}", "quantity": 2, "unit_price": 9.99,
        "channel": channel,
    }


_RealClient = httpx.Client


def _patch_client(monkeypatch, handler) -> None:
    monkeypatch.setattr(
        ca_orders_client.httpx, "Client",
        lambda **kwargs: _RealClient(transport=httpx.MockTransport(handler)),
    )


def test_fetch_recent_order_rows_flattens_items_across_one_page(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return _token_response(request)
        return httpx.Response(200, json={"value": [
            _make_order("SO-1", ["SKU-A", "SKU-B"]),
            _make_order("SO-2", ["SKU-A"]),
        ]})

    _patch_client(monkeypatch, handler)

    rows = ca_orders_client.fetch_recent_order_rows(60)
    assert rows == [
        _expected_row("SO-1", "SKU-A"),
        _expected_row("SO-1", "SKU-B"),
        _expected_row("SO-2", "SKU-A"),
    ]


def test_fetch_recent_order_rows_carries_channel_per_order(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return _token_response(request)
        return httpx.Response(200, json={"value": [
            _make_order("SO-1", ["SKU-A"], channel="Temu UK"),
            _make_order("SO-2", ["SKU-B"], channel="eBay Fixed Price UK"),
        ]})

    _patch_client(monkeypatch, handler)

    rows = ca_orders_client.fetch_recent_order_rows(60)
    assert rows == [
        _expected_row("SO-1", "SKU-A", channel="Temu UK"),
        _expected_row("SO-2", "SKU-B", channel="eBay Fixed Price UK"),
    ]


def test_fetch_recent_order_rows_paginates_until_short_page(monkeypatch):
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return _token_response(request)
        call_count["n"] += 1
        if call_count["n"] == 1:
            full_page = [_make_order(f"SO-{i}", ["SKU-A"]) for i in range(ca_orders_client.PAGE_SIZE)]
            return httpx.Response(200, json={"value": full_page})
        return httpx.Response(200, json={"value": [_make_order("SO-last", ["SKU-Z"])]})

    _patch_client(monkeypatch, handler)

    rows = ca_orders_client.fetch_recent_order_rows(60)
    assert call_count["n"] == 2
    assert rows[-1] == _expected_row("SO-last", "SKU-Z")
    assert len(rows) == ca_orders_client.PAGE_SIZE + 1


def test_fetch_recent_order_rows_retries_429_then_succeeds(monkeypatch):
    monkeypatch.setattr(ca_orders_client.time, "sleep", lambda seconds: None)
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return _token_response(request)
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, json={"value": [_make_order("SO-1", ["SKU-A"])]})

    _patch_client(monkeypatch, handler)

    rows = ca_orders_client.fetch_recent_order_rows(60)
    assert attempts["n"] == 2
    assert rows == [_expected_row("SO-1", "SKU-A")]


def test_fetch_recent_order_rows_raises_on_exhausted_429_retries(monkeypatch):
    monkeypatch.setattr(ca_orders_client.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(ca_orders_client, "MAX_RATE_LIMIT_RETRIES", 2)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return _token_response(request)
        return httpx.Response(429, headers={"Retry-After": "1"})

    _patch_client(monkeypatch, handler)

    with pytest.raises(httpx.HTTPStatusError):
        ca_orders_client.fetch_recent_order_rows(60)


def test_fetch_recent_order_rows_retries_transient_connect_error_then_succeeds(monkeypatch):
    monkeypatch.setattr(ca_orders_client.time, "sleep", lambda seconds: None)
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return _token_response(request)
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json={"value": []})

    _patch_client(monkeypatch, handler)

    rows = ca_orders_client.fetch_recent_order_rows(60)
    assert attempts["n"] == 2
    assert rows == []


def test_fetch_recent_order_rows_raises_without_silent_partial_data(monkeypatch):
    monkeypatch.setattr(ca_orders_client.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(ca_orders_client, "MAX_TRANSIENT_RETRIES", 1)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return _token_response(request)
        raise httpx.ConnectError("always down", request=request)

    _patch_client(monkeypatch, handler)

    with pytest.raises(httpx.ConnectError):
        ca_orders_client.fetch_recent_order_rows(60)
