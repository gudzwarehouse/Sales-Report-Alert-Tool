"""Streamlit config editor for the Sales Report Tool pipeline.

Edits config/thresholds.json (SKU/total thresholds + their recipients) and
config/exceptions.json (excluded SKUs) via config_store.py -- the same
module run_sales_alerts.py reads, so the UI and the pipeline never disagree.

Deliberately does NOT expose .env (SMTP credentials, StockFeeds path/
password, CSV folder paths) -- those stay hand-edited, never in a browser.

Run via run_ui.bat, or directly: streamlit run app.py
"""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

import config_store

st.set_page_config(page_title="Sales Report Tool", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
<style>
    #MainMenu, header, footer, [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"] {
        display: none;
    }
    .block-container { padding-top: 2rem; max-width: 1100px; }
    body, .stApp { background: #f9fafb; }
    h1, h2, h3 { color: #111827; font-weight: 600; }
    .subtitle { color: #6b7280; font-size: 14px; margin-top: -8px; margin-bottom: 24px; }
    .bucket-card {
        background: #fafafa;
        border: 1px solid #e5e7eb;
        border-radius: 8px;
        padding: 16px 20px;
        margin-bottom: 14px;
    }
    .bucket-label {
        font-size: 11px;
        font-weight: 700;
        letter-spacing: .04em;
        text-transform: uppercase;
        color: #6b7280;
        margin-bottom: 10px;
    }
    .pill-sms {
        display: inline-block;
        background: #eff6ff;
        color: #1d4ed8;
        border: 1px solid #bfdbfe;
        border-radius: 999px;
        padding: 2px 10px;
        font-size: 12px;
        margin-top: 6px;
    }
    div[data-testid="stForm"] {
        border: 1px solid #e5e7eb;
        border-radius: 8px;
        padding: 20px;
        background: #ffffff;
    }
</style>
""", unsafe_allow_html=True)

st.title("Sales Report Tool")
st.markdown('<div class="subtitle">Thresholds, alert recipients, and excluded SKUs for the CA sales-alert pipeline.</div>', unsafe_allow_html=True)

WINDOW_LABELS = {"1h": "1-hour window", "4h": "4-hour window"}


def _recipients_text(recipients: list[str]) -> str:
    return "\n".join(recipients)


def _parse_recipients(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


tab_thresholds, tab_exceptions, tab_raw = st.tabs(["Thresholds & recipients", "Excluded SKUs", "Current config"])

with tab_thresholds:
    try:
        thresholds = config_store.load_thresholds()
    except Exception as error:  # noqa: BLE001
        st.error(f"Could not load config/thresholds.json: {error}")
        thresholds = None

    if thresholds is not None:
        for window in ("1h", "4h"):
            st.subheader(WINDOW_LABELS[window])
            buckets = thresholds[window]
            with st.form(key=f"form_{window}"):
                new_buckets = []
                for i, bucket in enumerate(buckets):
                    is_highest = i == len(buckets) - 1
                    st.markdown(
                        f'<div class="bucket-label">Bucket {i + 1}'
                        + (' &mdash; also triggers SMS + phone call' if is_highest else '')
                        + '</div>',
                        unsafe_allow_html=True,
                    )
                    col1, col2 = st.columns(2)
                    sku_threshold = col1.number_input(
                        "SKU threshold (orders)", min_value=1, value=int(bucket["sku_threshold"]),
                        key=f"{window}_{i}_sku",
                    )
                    total_threshold = col2.number_input(
                        "Total threshold (orders)", min_value=1, value=int(bucket["total_threshold"]),
                        key=f"{window}_{i}_total",
                    )
                    recipients_text = st.text_area(
                        "Recipients (one address per line)",
                        value=_recipients_text(bucket["recipients"]),
                        key=f"{window}_{i}_recipients",
                        height=120,
                    )
                    new_buckets.append({
                        "sku_threshold": int(sku_threshold),
                        "total_threshold": int(total_threshold),
                        "recipients": _parse_recipients(recipients_text),
                    })
                    st.markdown("</div>", unsafe_allow_html=True)

                saved = st.form_submit_button("Save changes", type="primary")
                if saved:
                    updated = {**thresholds, window: new_buckets}
                    try:
                        config_store.save_thresholds(updated)
                        st.success(f"Saved {WINDOW_LABELS[window]}.")
                        st.rerun()
                    except ValueError as error:
                        st.error(str(error))

with tab_exceptions:
    st.subheader("Excluded SKUs")
    st.markdown(
        '<div class="subtitle">SKUs listed here never trigger an alert, no matter how high their order count is.</div>',
        unsafe_allow_html=True,
    )
    try:
        exceptions = config_store.load_exceptions()
    except Exception as error:  # noqa: BLE001
        st.error(f"Could not load config/exceptions.json: {error}")
        exceptions = None

    if exceptions is not None:
        df = pd.DataFrame({"SKU": exceptions})
        edited = st.data_editor(df, num_rows="dynamic", use_container_width=True, key="exceptions_editor")
        col_save, col_cancel = st.columns([1, 6])
        if col_save.button("Save changes", type="primary", key="save_exceptions"):
            skus = [str(v) for v in edited["SKU"].tolist()]
            saved = config_store.save_exceptions(skus)
            st.success(f"Saved {len(saved)} excluded SKU(s).")
            st.rerun()
        if col_cancel.button("Cancel", key="cancel_exceptions"):
            st.rerun()

with tab_raw:
    st.subheader("Current config (read-only)")
    try:
        st.markdown("**config/thresholds.json**")
        st.json(config_store.load_thresholds())
        st.markdown("**config/exceptions.json**")
        st.json({"skus": config_store.load_exceptions()})
    except Exception as error:  # noqa: BLE001
        st.error(f"Could not load config: {error}")
