"""Unit test for notifications.render_price_drop_html."""

from __future__ import annotations

import notifications


def test_render_price_drop_html_includes_all_columns():
    items = [
        {
            "sku": "OUR-1", "title": "Widget", "channels": "Temu UK (3)",
            "baseline_price": 15.0, "today_price": 5.0, "pct_drop": 66.7,
            "baseline_source": "Yesterday's sale",
        },
    ]
    html = notifications.render_price_drop_html(severity="HIGH", items=items)
    assert "<html" in html
    assert "HIGH" in html
    assert "Widget" in html
    assert "OUR-1" in html
    assert "Temu UK (3)" in html
    assert "£15.00" in html
    assert "£5.00" in html
    assert "67%" in html
    assert "Yesterday&#x27;s sale" in html or "Yesterday's sale" in html


def test_render_price_drop_html_medium_severity_uses_different_badge_color():
    html = notifications.render_price_drop_html(severity="medium", items=[])
    assert "MEDIUM" in html
    assert "#b45309" in html  # medium badge color, not the HIGH red
