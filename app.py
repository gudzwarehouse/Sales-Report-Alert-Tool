"""Streamlit config editor for the Sales Report Tool pipeline.

Edits config/thresholds.json (SKU/total thresholds + their recipients) and
config/exceptions.json (excluded SKUs) via config_store.py -- the same
module run_sales_alerts.py reads, so the UI and the pipeline never disagree.

Deliberately does NOT expose .env (SMTP credentials, StockFeeds path/
password, CSV folder paths) -- those stay hand-edited, never in a browser.

Run via run_ui.bat, or directly: streamlit run app.py
"""

from __future__ import annotations

import hmac
import html
import os

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

import config_store
import sku_titles
from sales_pipeline import IGNORED_ONLINE_SKU_PREFIXES

st.set_page_config(page_title="Sales Report Tool", page_icon="📈", layout="wide", initial_sidebar_state="collapsed")

# ---------------------------------------------------------------- styling --
st.markdown(
    """
<style>
    :root {
        --ink: #0f172a; --muted: #64748b; --line: #e2e8f0; --bg: #f8fafc;
        --card: #ffffff; --accent: #0f172a; --danger: #dc2626;
    }
    #MainMenu, header, footer, [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"] { display: none; }
    html, body, .stApp, [class*="css"] {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, "Helvetica Neue", Arial, sans-serif;
    }
    .stApp { background: var(--bg); color: var(--ink); }
    .block-container { padding: 1.6rem 2rem 3rem; max-width: 1240px; }

    /* header */
    .app-head { display:flex; align-items:center; justify-content:space-between; margin-bottom: 1.1rem; }
    .app-title { font-size: 1.35rem; font-weight: 700; letter-spacing: -.01em; color: var(--ink); margin: 0; }
    .app-sub { color: var(--muted); font-size: .85rem; margin-top: 2px; }
    .chips { display:flex; gap:8px; }
    .chip { background: var(--card); border:1px solid var(--line); border-radius: 999px; padding: 4px 12px;
            font-size: .75rem; color: var(--muted); }
    .chip b { color: var(--ink); font-weight: 650; }

    /* tabs */
    .stTabs [data-baseweb="tab-list"] { gap: 4px; border-bottom: 1px solid var(--line); }
    .stTabs [data-baseweb="tab"] { height: 40px; padding: 0 14px; font-size: .88rem; color: var(--muted); font-weight: 500; }
    .stTabs [aria-selected="true"] { color: var(--ink); font-weight: 650; }
    .stTabs [data-baseweb="tab-highlight"] { background: var(--ink); height: 2px; }
    .stTabs [data-baseweb="tab-panel"] { padding-top: 1.1rem; }

    /* cards */
    div[data-testid="stVerticalBlockBorderWrapper"]:has(> div > div > div > .card-marker),
    div[data-testid="stForm"], [data-testid="stExpander"] {
        background: var(--card); border: 1px solid var(--line) !important; border-radius: 12px;
        box-shadow: 0 1px 2px rgba(15,23,42,.04);
    }
    div[data-testid="stForm"] { padding: 18px 20px; }
    .card-title { font-size: .95rem; font-weight: 650; color: var(--ink); margin: 0; }
    .card-sub { font-size: .78rem; color: var(--muted); margin: 2px 0 10px; }
    .tag { display:inline-block; font-size: .68rem; font-weight: 650; letter-spacing:.03em; text-transform: uppercase;
           padding: 2px 8px; border-radius: 6px; background:#f1f5f9; color:#475569; }
    .tag.hot { background:#fef2f2; color:#b91c1c; }
    .bucket-head { display:flex; align-items:center; gap:8px; margin: 14px 0 6px; font-size:.85rem; font-weight:600; }
    .bucket-head:first-child { margin-top: 0; }
    hr { margin: 14px 0; border-color: var(--line); }

    /* inputs & buttons */
    label, [data-testid="stWidgetLabel"] p { font-size: .78rem !important; color: var(--muted) !important; font-weight: 550 !important; }
    .stTextInput input, .stNumberInput input, .stTextArea textarea {
        font-size: .88rem; border-radius: 8px; background: #fff;
    }
    .stButton > button, .stDownloadButton > button, [data-testid="stFormSubmitButton"] > button {
        border-radius: 8px; font-size: .82rem; font-weight: 550; min-height: 2.15rem; padding: 0 .9rem;
        border: 1px solid var(--line); background: #fff; color: var(--ink);
    }
    .stButton > button:hover, .stDownloadButton > button:hover { border-color: #94a3b8; color: var(--ink); }
    button[kind="primary"], button[data-testid="stBaseButton-primary"], button[data-testid="stBaseButton-primaryFormSubmit"] {
        background: var(--accent) !important; border-color: var(--accent) !important; color: #fff !important;
    }
    button[kind="primary"]:hover, button[data-testid="stBaseButton-primary"]:hover { background: #1e293b !important; }

    /* excluded-SKU table */
    .tbl-head { font-size:.7rem; font-weight:650; letter-spacing:.05em; text-transform:uppercase; color: var(--muted);
                padding: 0 0 6px; border-bottom: 1px solid var(--line); }
    [class*='st-key-skurow_'] { border-bottom: 1px solid #f1f5f9; padding: 0; }
    [class*='st-key-skurow_']:hover { background: #f8fafc; }
    [class*='st-key-skurow_'] [data-testid='stVerticalBlock'],
    [class*='st-key-skurow_'] [data-testid='stHorizontalBlock'] { gap: 0 !important; }
    [class*='st-key-skurow_'] p { margin: 0 !important; }
    .sku-code { font-size: .84rem; font-weight: 650; color: var(--ink); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
    .sku-name { font-size: .84rem; color: var(--muted); }
    [class*='st-key-del_'] button {
        background: transparent !important; border: none !important; box-shadow: none !important;
        color: #94a3b8 !important; min-height: 1.5rem !important; height: 1.5rem !important; padding: 0 !important;
    }
    [class*='st-key-del_'] button:hover { color: var(--danger) !important; background: #fef2f2 !important; }

    /* confirmation popup */
    div[role='dialog'] { max-width: 340px !important; border-radius: 14px; }
    div[role='dialog'] h2 { font-size: 1rem !important; }
    div[role='dialog'] p { font-size: .85rem !important; }
    .st-key-confirm_delete_yes button { background: var(--danger) !important; border-color: var(--danger) !important; color:#fff !important; }
    .st-key-confirm_delete_yes button:hover { background: #b91c1c !important; }

    .note { background:#f1f5f9; border-radius:10px; padding:10px 14px; font-size:.8rem; color:#334155; }
    .note.warn { background:#fef2f2; color:#991b1b; }
    code { font-size: .8rem; }
</style>
""",
    unsafe_allow_html=True,
)

load_dotenv(".env", override=True)


def _require_passcode() -> None:
    """Lock the whole UI behind UI_PASSCODE from .env. Fails closed: no passcode
    configured means nobody gets in (the editor can change who gets alerts)."""
    expected = os.getenv("UI_PASSCODE", "").strip()
    if not expected:
        st.error("UI is locked: UI_PASSCODE is not set in .env.")
        st.stop()
    if st.session_state.get("unlocked"):
        return
    _, mid, _ = st.columns([1, 1, 1])
    with mid:
        st.markdown('<div class="app-title" style="margin-top:12vh">Sales Report Tool</div>'
                    '<div class="app-sub" style="margin-bottom:14px">Enter the passcode to continue.</div>',
                    unsafe_allow_html=True)
        entered = st.text_input("Passcode", type="password", key="passcode_input")
        if entered:
            if hmac.compare_digest(entered.encode(), expected.encode()):
                st.session_state["unlocked"] = True
                st.rerun()
            st.error("Wrong passcode.")
    st.stop()


_require_passcode()

# ------------------------------------------------------------------ data --
WINDOW_LABELS = {"1h": "1-hour window", "4h": "4-hour window"}
WINDOW_HINTS = {
    "1h": "Orders arriving in ChannelAdvisor in each clock hour. Alerts once per hour.",
    "4h": "Fixed 4-hour blocks (00-04, 04-08, ...). Alerts once per block.",
}

try:
    thresholds = config_store.load_thresholds()
except Exception as error:  # noqa: BLE001
    st.error(f"Could not load config/thresholds.json: {error}")
    thresholds = None
try:
    exceptions = config_store.load_exceptions()
except Exception as error:  # noqa: BLE001
    st.error(f"Could not load config/exceptions.json: {error}")
    exceptions = None
titles = sku_titles.load_titles()


def _recipients_text(recipients: list[str]) -> str:
    return "\n".join(recipients)


def _parse_recipients(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


# ---------------------------------------------------------------- header --
n_buckets = sum(len(thresholds[w]) for w in WINDOW_LABELS) if thresholds else 0
st.markdown(
    f"""<div class="app-head">
      <div><div class="app-title">Sales Report Tool</div>
      <div class="app-sub">Thresholds, alert recipients and excluded SKUs for the ChannelAdvisor sales alerts.</div></div>
      <div class="chips"><span class="chip"><b>{n_buckets}</b> threshold buckets</span>
      <span class="chip"><b>{len(exceptions) if exceptions is not None else 0}</b> excluded SKUs</span></div>
    </div>""",
    unsafe_allow_html=True,
)

tab_thresholds, tab_exceptions, tab_overview = st.tabs(["Thresholds & recipients", "Excluded SKUs", "Overview"])


# ------------------------------------------------------------ thresholds --
with tab_thresholds:
    if thresholds is not None:
        cols = st.columns(2, gap="large")
        for col, window in zip(cols, ("1h", "4h")):
            with col:
                st.markdown(f'<div class="card-title">{WINDOW_LABELS[window]}</div>'
                            f'<div class="card-sub">{WINDOW_HINTS[window]}</div>', unsafe_allow_html=True)
                buckets = thresholds[window]
                with st.form(key=f"form_{window}"):
                    new_buckets = []
                    for i, bucket in enumerate(buckets):
                        is_highest = i == len(buckets) - 1
                        tag = '<span class="tag hot">SMS + phone call</span>' if is_highest else '<span class="tag">Email</span>'
                        st.markdown(f'<div class="bucket-head">Bucket {i + 1} {tag}</div>', unsafe_allow_html=True)
                        c1, c2 = st.columns(2)
                        sku_threshold = c1.number_input(
                            "Per-SKU orders ≥", min_value=1, value=int(bucket["sku_threshold"]), key=f"{window}_{i}_sku")
                        total_threshold = c2.number_input(
                            "Total orders >", min_value=1, value=int(bucket["total_threshold"]), key=f"{window}_{i}_total")
                        recipients_text = st.text_area(
                            f"Recipients ({len(bucket['recipients'])}) — one per line",
                            value=_recipients_text(bucket["recipients"]), key=f"{window}_{i}_recipients", height=130)
                        new_buckets.append({
                            "sku_threshold": int(sku_threshold),
                            "total_threshold": int(total_threshold),
                            "recipients": _parse_recipients(recipients_text),
                        })
                        if not is_highest:
                            st.markdown("<hr>", unsafe_allow_html=True)
                    if st.form_submit_button("Save changes", type="primary"):
                        try:
                            config_store.save_thresholds({**thresholds, window: new_buckets})
                            st.success(f"Saved {WINDOW_LABELS[window]}.")
                            st.rerun()
                        except ValueError as error:
                            st.error(str(error))


# --------------------------------------------------------- excluded SKUs --
@st.dialog("Delete excluded SKU?")
def _confirm_delete_sku(sku: str) -> None:
    st.write(f"Remove **{sku}**? It can alert again.")
    col_yes, col_no = st.columns(2)
    if col_yes.button("Yes, delete", type="primary", key="confirm_delete_yes"):
        config_store.save_exceptions([s for s in config_store.load_exceptions() if s != sku])
        st.rerun()
    if col_no.button("Cancel", key="confirm_delete_no"):
        st.rerun()


with tab_exceptions:
    if exceptions is not None:
        left, right = st.columns([7, 4], gap="large")

        # ---- left: searchable list
        with left:
            st.markdown('<div class="card-title">Excluded SKUs</div>'
                        '<div class="card-sub">These products never trigger a per-SKU alert, however many they sell.</div>',
                        unsafe_allow_html=True)
            f1, f2 = st.columns([3, 2])
            query = f1.text_input("Search", key="search_sku", placeholder="SKU or product name",
                                  label_visibility="collapsed").strip().lower()
            no_name = [s for s in exceptions if s not in titles]
            if f2.button(f"Fetch names ({len(no_name)} missing)", key="fetch_names_btn",
                         disabled=not no_name, use_container_width=True):
                with st.spinner("Looking up product names..."):
                    try:
                        got, still = sku_titles.fetch_missing_names(exceptions)
                        st.session_state["fetch_names_msg"] = (
                            f"Found {got} name(s)." + (f" Not found: {', '.join(still)}" if still else ""))
                    except Exception as error:  # noqa: BLE001
                        st.session_state["fetch_names_msg"] = f"Could not fetch names: {error}"
                st.rerun()
            if st.session_state.get("fetch_names_msg"):
                st.info(st.session_state.pop("fetch_names_msg"))

            shown = [s for s in exceptions
                     if query in s.lower() or query in titles.get(s, "").lower()] if query else list(exceptions)
            st.caption(f"Showing {len(shown)} of {len(exceptions)}")
            h1, h2, h3 = st.columns([2, 7, 1])
            h1.markdown('<div class="tbl-head">SKU</div>', unsafe_allow_html=True)
            h2.markdown('<div class="tbl-head">Product name</div>', unsafe_allow_html=True)
            h3.markdown('<div class="tbl-head">&nbsp;</div>', unsafe_allow_html=True)
            with st.container(height=520, border=False):
                for sku in shown:
                    with st.container(key=f"skurow_{sku}"):
                        c1, c2, c3 = st.columns([2, 7, 1], vertical_alignment="center")
                        c1.markdown(f'<span class="sku-code">{html.escape(sku)}</span>', unsafe_allow_html=True)
                        c2.markdown(f'<span class="sku-name">{html.escape(titles.get(sku, ""))}</span>',
                                    unsafe_allow_html=True)
                        if c3.button("🗑️", key=f"del_{sku}", help=f"Delete {sku}"):
                            _confirm_delete_sku(sku)

        # ---- right: add + bulk + rules
        with right:
            st.markdown('<div class="card-title">Add a SKU</div>'
                        '<div class="card-sub">Use OUR SKU (e.g. RIOPAU04), not the marketplace SKU.</div>',
                        unsafe_allow_html=True)
            a1, a2 = st.columns([3, 1])
            new_one = a1.text_input("SKU", key="add_one_sku", placeholder="e.g. RIOPAU04", label_visibility="collapsed")
            if a2.button("Add", type="primary", key="add_one_btn", use_container_width=True):
                sku = new_one.strip()
                if not sku:
                    st.warning("Enter a SKU first.")
                elif sku in exceptions:
                    st.info(f"{sku} is already excluded.")
                else:
                    config_store.save_exceptions(exceptions + [sku])
                    st.session_state.pop("add_one_sku", None)
                    st.rerun()

            st.markdown("<hr>", unsafe_allow_html=True)
            st.markdown('<div class="card-title">Bulk add from Excel</div>'
                        '<div class="card-sub">One SKU per row (a header containing "SKU", else column A). '
                        'Only new SKUs are added &mdash; nothing is removed.</div>', unsafe_allow_html=True)
            st.download_button(
                "Download sample", data=config_store.exceptions_template_xlsx(),
                file_name="excluded_skus_template.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="download_exceptions_template", use_container_width=True)
            upload = st.file_uploader("Excel file", type=["xlsx"], key="exceptions_upload", label_visibility="collapsed")
            if upload is not None:
                try:
                    uploaded = config_store.parse_skus_from_xlsx(upload)
                except Exception as error:  # noqa: BLE001
                    st.error(f"Could not read that file: {error}")
                    uploaded = None
                if uploaded is not None:
                    existing = set(exceptions)
                    new_skus = [s for s in uploaded if s not in existing]
                    st.write(f"**{len(uploaded)}** in file · **{len(new_skus)}** new · "
                             f"{len(uploaded) - len(new_skus)} already excluded")
                    if new_skus:
                        st.dataframe(pd.DataFrame({"New SKU": new_skus}), use_container_width=True, height=180,
                                     hide_index=True)
                        if st.button(f"Add {len(new_skus)} SKU(s)", type="primary", key="bulk_add_exceptions",
                                     use_container_width=True):
                            saved = config_store.save_exceptions(exceptions + new_skus)
                            st.success(f"Added {len(new_skus)}. Now {len(saved)} excluded SKU(s).")
                            st.rerun()

            st.markdown("<hr>", unsafe_allow_html=True)
            prefixes = ", ".join(f"<code>{p}*</code>" for p in IGNORED_ONLINE_SKU_PREFIXES)
            st.markdown(
                '<div class="note warn"><b>Our SKU only.</b> Online/marketplace SKUs (e.g. 21700, FBARIOPAU02(AB)) '
                "never match, so that product would still alert.</div>"
                f'<div class="note" style="margin-top:8px">Ignored automatically: online SKUs starting with {prefixes} '
                "(Amazon-fulfilled). They are left out of per-product alerts and totals.</div>",
                unsafe_allow_html=True,
            )


# -------------------------------------------------------------- overview --
SOURCE_WORKBOOKS = [
    (
        "StockFeeds.xlsm",
        r"\\mbc-nt01\Management\Excel Automatic\StockFeeds.xlsm",
        "<b>When:</b> fetched fresh at the start of every pipeline run &mdash; never a cached copy.<br>"
        "<b>How it works:</b> decrypts the file, reads its &ldquo;Inventory&rdquo; sheet to map each online SKU "
        "to our own SKU, then deletes the temporary copy.<br>"
        "<b>Configured via .env</b> (not editable here): <code>STOCKFEEDS_XLSM_PATH</code> and "
        "<code>STOCKFEEDS_XLSM_PASSWORD</code>.",
    ),
]

with tab_overview:
    o1, o2 = st.columns(2, gap="large")
    with o1:
        st.markdown('<div class="card-title">How alerts work</div>'
                    '<div class="card-sub">Runs hourly via Windows Task Scheduler.</div>', unsafe_allow_html=True)
        st.markdown(
            """
- Orders are counted by **arrival time in ChannelAdvisor**, per our SKU.
- **1-hour alerts:** once per clock hour.
- **4-hour alerts:** once per fixed block (00-04, 04-08, ...).
- A daily summary email lists every threshold hit, including excluded SKUs.
- The higher bucket also notifies the SMS / phone-call addresses.
"""
        )
    with o2:
        st.markdown('<div class="card-title">Source file</div>'
                    '<div class="card-sub">The one workbook the pipeline reads at runtime.</div>', unsafe_allow_html=True)
        for name, path, description in SOURCE_WORKBOOKS:
            st.markdown(
                f'<div class="note"><b>{name}</b><br><code>{path}</code>'
                f'<div style="margin-top:8px">{description}</div></div>',
                unsafe_allow_html=True,
            )
