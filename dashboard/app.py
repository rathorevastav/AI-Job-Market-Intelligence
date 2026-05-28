"""
dashboard/app.py

AI Job Market Intelligence Platform — Streamlit Dashboard

ROOT CAUSES OF BLANK PAGES (fixed in this version):
───────────────────────────────────────────────────
BUG 1 — double main() call:
    Old code had:
        if __name__ == "__main__": main()
        else: main()
    Streamlit runs the file AS __main__, so BOTH branches executed.
    Every sidebar widget (radio, selectbox, button) was registered twice,
    raising a DuplicateWidgetID exception. Streamlit catches this silently
    and renders a blank page instead of showing an error.
    FIX: Call main() once, unconditionally, at module level.

BUG 2 — @st.cache_data with **kwargs:
    Old _load_jobs(**kwargs) was decorated with @st.cache_data.
    Streamlit must hash all function arguments to build a cache key.
    A **kwargs dict is not hashable → TypeError → Streamlit suppresses the
    exception and returns None → every page that needed job data got None
    instead of a result dict → all job-dependent rendering was skipped.
    FIX: Use explicit named parameters on the cached loader.

HOW TO RUN:
    streamlit run dashboard/app.py
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import plotly.express as px
import streamlit as st

import api_client as api
import charts as charts
import utils as utils

# ============================================================================
# PAGE CONFIG — must be the very first Streamlit call
# ============================================================================

st.set_page_config(
    page_title="Job Market Intelligence",
    page_icon="💼",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Global CSS — green/white SaaS theme ──────────────────────────────────
st.markdown("""
<style>
/* ── Google Font ── */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

/* ── Base ── */
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

/* ── App background: off-white with faint dot grid for depth ── */
.stApp {
    background-color: #f7faf9;
    background-image: radial-gradient(circle, #c8e0d4 1px, transparent 1px);
    background-size: 28px 28px;
}
.main .block-container {
    padding-top: 2rem;
    padding-bottom: 3rem;
    padding-left: 2.5rem;
    padding-right: 2.5rem;
    max-width: 1400px;
}

/* ── Sidebar: deep forest green ── */
[data-testid="stSidebar"] {
    background: #0d1f17 !important;
    border-right: 1px solid #1a3327;
}
[data-testid="stSidebar"] * { color: #c8d8d0 !important; }
[data-testid="stSidebar"] .stRadio label { color: #a0bfb0 !important; font-size: 14px !important; }
[data-testid="stSidebarNav"] { display: none !important; }

/* ── Hide Streamlit chrome ── */
#MainMenu { visibility: hidden; }
footer    { visibility: hidden; }
header    { visibility: hidden; }

/* ── Metric cards: white surface, green top accent, lifts off dot-grid ── */
[data-testid="stMetric"] {
    background: #ffffff;
    border: 1px solid #e2ece8;
    border-top: 3px solid #10b981;
    border-radius: 14px;
    padding: 18px 22px 14px;
    box-shadow: 0 2px 8px rgba(16,185,129,0.08), 0 1px 3px rgba(0,0,0,0.04);
    transition: box-shadow 0.2s, transform 0.2s;
}
[data-testid="stMetric"]:hover {
    box-shadow: 0 6px 20px rgba(16,185,129,0.14), 0 2px 6px rgba(0,0,0,0.06);
    transform: translateY(-2px);
}
[data-testid="stMetric"] label {
    color: #6b7f78 !important;
    font-size: 11px !important;
    font-weight: 600 !important;
    letter-spacing: 0.06em !important;
    text-transform: uppercase !important;
}
[data-testid="stMetricValue"] {
    color: #0d1f17 !important;
    font-size: 28px !important;
    font-weight: 700 !important;
}
[data-testid="stMetricDelta"] { font-size: 12px !important; }

/* ── Tabs: minimal underline style ── */
[data-baseweb="tab-list"] {
    background: transparent !important;
    border-bottom: 2px solid #e2ece8 !important;
    gap: 8px;
}
button[data-baseweb="tab"] {
    background: transparent !important;
    color: #6b7f78 !important;
    font-size: 13px !important;
    font-weight: 500 !important;
    border: none !important;
    padding: 8px 16px !important;
    border-radius: 6px 6px 0 0 !important;
}
button[data-baseweb="tab"][aria-selected="true"] {
    color: #059669 !important;
    font-weight: 600 !important;
    border-bottom: 2px solid #059669 !important;
    background: rgba(16,185,129,0.06) !important;
}

/* ── Dataframe: full white SaaS table ── */
[data-testid="stDataFrameContainer"] {
    border-radius: 12px;
    border: 1px solid #e2ece8;
    overflow: hidden;
    box-shadow: 0 1px 4px rgba(0,0,0,0.04);
    background: #ffffff;
}
/* Canvas-based Glide Data Grid — override the dark surface */
[data-testid="stDataFrameContainer"] canvas {
    border-radius: 0 0 12px 12px;
}
/* The wrapper div Streamlit injects around the canvas */
[data-testid="stDataFrameContainer"] > div,
[data-testid="stDataFrameContainer"] > div > div {
    background: #ffffff !important;
    border-radius: 0 !important;
}
/* Column header row */
[data-testid="stDataFrameContainer"] [role="columnheader"],
[data-testid="stDataFrameContainer"] [data-testid="glideDataGridHeader"],
[data-testid="stDataFrameContainer"] .dvn-stack {
    background: #f8fbf9 !important;
    border-bottom: 2px solid #d1e8de !important;
    color: #374151 !important;
    font-size: 11px !important;
    font-weight: 700 !important;
    letter-spacing: 0.06em !important;
    text-transform: uppercase !important;
}
/* Data cells */
[data-testid="stDataFrameContainer"] [role="gridcell"],
[data-testid="stDataFrameContainer"] [data-testid="glideDataGridCell"] {
    background: #ffffff !important;
    color: #1a2e23 !important;
    font-size: 13px !important;
    border-bottom: 1px solid #edf3f0 !important;
}
/* Row hover */
[data-testid="stDataFrameContainer"] [role="row"]:hover [role="gridcell"],
[data-testid="stDataFrameContainer"] tr:hover td {
    background: #f0fdf9 !important;
}
/* Scrollbar inside table */
[data-testid="stDataFrameContainer"] ::-webkit-scrollbar { height: 6px; width: 6px; }
[data-testid="stDataFrameContainer"] ::-webkit-scrollbar-track { background: #f1f5f3; }
[data-testid="stDataFrameContainer"] ::-webkit-scrollbar-thumb {
    background: #a7d4c0;
    border-radius: 3px;
}
/* Streamlit's internal toolbar above the table (fullscreen / download icons) */
[data-testid="stDataFrameContainer"] [data-testid="stElementToolbar"] {
    background: #f8fbf9 !important;
    border-bottom: 1px solid #e2ece8 !important;
    border-radius: 12px 12px 0 0 !important;
}
[data-testid="stDataFrameContainer"] [data-testid="stElementToolbar"] button,
[data-testid="stDataFrameContainer"] [data-testid="stElementToolbar"] svg {
    color: #6b9e86 !important;
    fill: #6b9e86 !important;
}

/* ── Plotly charts: white card surface ── */
[data-testid="stPlotlyChart"] {
    background: #ffffff;
    border: 1px solid #e8f0ec;
    border-radius: 14px;
    padding: 8px;
    box-shadow: 0 2px 8px rgba(16,185,129,0.06), 0 1px 3px rgba(0,0,0,0.04);
}

/* ── Buttons: emerald green ── */
.stButton > button {
    background: #059669 !important;
    color: #ffffff !important;
    border: none !important;
    border-radius: 8px !important;
    font-size: 13px !important;
    font-weight: 600 !important;
    padding: 8px 18px !important;
    box-shadow: 0 1px 4px rgba(5,150,105,0.25);
    transition: all 0.15s;
}
.stButton > button:hover {
    background: #047857 !important;
    box-shadow: 0 3px 12px rgba(5,150,105,0.35);
    transform: translateY(-1px);
}

/* ── Text inputs ── */
[data-testid="stTextInput"] input {
    background: #ffffff !important;
    border: 1px solid #d1e0d8 !important;
    border-radius: 8px !important;
    color: #0d1f17 !important;
    font-size: 13px !important;
}
[data-testid="stTextInput"] input:focus {
    border-color: #10b981 !important;
    box-shadow: 0 0 0 3px rgba(16,185,129,0.12) !important;
}

/* ── Selectbox ── */
[data-baseweb="select"] > div {
    background: #ffffff !important;
    border: 1px solid #d1e0d8 !important;
    border-radius: 8px !important;
    color: #0d1f17 !important;
    font-size: 13px !important;
}

/* ── Alerts: kill all default Streamlit blue, use theme colours ── */
/* st.info → emerald tint */
[data-testid="stNotification"],
div[data-testid="stAlert"],
div[role="alert"] {
    border-radius: 10px !important;
    font-size: 13px !important;
}
[data-testid="stInfo"],
[data-testid="stAlert"][kind="info"] {
    background: #f0fdf9 !important;
    border: 1px solid #6ee7b7 !important;
    border-left: 4px solid #10b981 !important;
    color: #065f46 !important;
    border-radius: 10px !important;
}
/* Override the blue icon Streamlit injects */
[data-testid="stInfo"] svg,
[data-testid="stInfo"] [data-testid="stMarkdownContainer"] ~ div svg {
    fill: #10b981 !important;
    color: #10b981 !important;
}
[data-testid="stSuccess"] {
    background: #f0fdf4 !important;
    border: 1px solid #86efac !important;
    border-left: 4px solid #059669 !important;
    color: #064e3b !important;
    border-radius: 10px !important;
}
[data-testid="stSuccess"] svg { fill: #059669 !important; }
[data-testid="stWarning"] {
    background: #fffbeb !important;
    border: 1px solid #fde68a !important;
    border-left: 4px solid #f59e0b !important;
    border-radius: 10px !important;
}
[data-testid="stError"] {
    background: #fff1f2 !important;
    border: 1px solid #fecdd3 !important;
    border-left: 4px solid #ef4444 !important;
    border-radius: 10px !important;
}
/* Caption under st.caption() */
[data-testid="stCaptionContainer"] { color: #6b7f78 !important; }

/* ── Filter inputs: stronger text contrast ── */
[data-testid="stTextInput"] input {
    background: #ffffff !important;
    border: 1px solid #b8d4c8 !important;
    border-radius: 8px !important;
    color: #0d1f17 !important;
    font-size: 13px !important;
    font-weight: 500 !important;
}
[data-testid="stTextInput"] input::placeholder {
    color: #7a9e8e !important;
    font-weight: 400 !important;
    opacity: 1 !important;
}
[data-testid="stTextInput"] input:focus {
    border-color: #10b981 !important;
    box-shadow: 0 0 0 3px rgba(16,185,129,0.12) !important;
}
/* Selectbox: full override for BaseUI component internals */
[data-baseweb="select"] > div {
    background: #ffffff !important;
    border: 1px solid #b8d4c8 !important;
    border-radius: 8px !important;
    font-size: 13px !important;
    font-weight: 500 !important;
    min-height: 38px !important;
}
[data-baseweb="select"] > div:focus-within {
    border-color: #10b981 !important;
    box-shadow: 0 0 0 3px rgba(16,185,129,0.12) !important;
}
/* The selected value text */
[data-baseweb="select"] [data-testid="stSelectboxDiv"] span,
[data-baseweb="select"] [class*="ValueContainer"] span,
[data-baseweb="select"] [class*="singleValue"],
[data-baseweb="select"] [class*="placeholder"] {
    color: #0d1f17 !important;
    font-size: 13px !important;
    font-weight: 500 !important;
    opacity: 1 !important;
}
/* Placeholder specifically — muted but readable */
[data-baseweb="select"] [class*="placeholder"] {
    color: #7a9e8e !important;
    font-weight: 400 !important;
}
/* Chevron icon */
[data-baseweb="select"] svg[data-testid="stMarkdownContainer"],
[data-baseweb="select"] [class*="indicatorContainer"] svg {
    fill: #6b9e86 !important;
    color: #6b9e86 !important;
}
/* Open dropdown panel */
[data-baseweb="popover"],
[data-baseweb="menu"] {
    background: #ffffff !important;
    border: 1px solid #d1e8de !important;
    border-radius: 10px !important;
    box-shadow: 0 8px 24px rgba(16,185,129,0.1), 0 2px 8px rgba(0,0,0,0.06) !important;
}
/* Menu list items */
[data-baseweb="menu"] li,
[data-baseweb="menu"] [role="option"] {
    background: #ffffff !important;
    color: #0d1f17 !important;
    font-size: 13px !important;
    font-weight: 500 !important;
    padding: 8px 14px !important;
}
[data-baseweb="menu"] li:hover,
[data-baseweb="menu"] [role="option"]:hover,
[data-baseweb="menu"] [aria-selected="true"] {
    background: #f0fdf9 !important;
    color: #065f46 !important;
}
/* Focused/highlighted item */
[data-baseweb="menu"] [data-highlighted="true"],
[data-baseweb="menu"] [class*="highlighted"] {
    background: #ecfdf5 !important;
    color: #059669 !important;
}

/* ── Expander ── */
details {
    background: #ffffff !important;
    border: 1px solid #e2ece8 !important;
    border-radius: 12px !important;
}
details summary {
    font-weight: 600 !important;
    color: #0d1f17 !important;
    font-size: 13px !important;
}

/* ── Spinner ── */
[data-testid="stSpinner"] { color: #10b981; }
[data-testid="stSpinner"] > div { border-top-color: #10b981 !important; }

/* ── Remaining dark defaults: normalize to white/green system ── */

/* Number input */
[data-testid="stNumberInput"] input {
    background: #ffffff !important;
    border: 1px solid #b8d4c8 !important;
    border-radius: 8px !important;
    color: #0d1f17 !important;
    font-size: 13px !important;
    font-weight: 500 !important;
}
[data-testid="stNumberInput"] button {
    background: #f0fdf9 !important;
    border-color: #b8d4c8 !important;
    color: #059669 !important;
}

/* Checkbox */
[data-testid="stCheckbox"] label span,
[data-testid="stCheckbox"] p {
    color: #1a2e23 !important;
    font-size: 13px !important;
    font-weight: 500 !important;
}
[data-testid="stCheckbox"] [data-baseweb="checkbox"] div {
    border-color: #b8d4c8 !important;
    background: #ffffff !important;
}
[data-testid="stCheckbox"] input:checked ~ div {
    background: #10b981 !important;
    border-color: #10b981 !important;
}

/* Slider */
[data-testid="stSlider"] [data-baseweb="slider"] [role="slider"] {
    background: #10b981 !important;
    border-color: #10b981 !important;
    box-shadow: 0 0 0 4px rgba(16,185,129,0.15) !important;
}
[data-testid="stSlider"] [data-baseweb="slider"] div[class*="Track"] {
    background: #d1e8de !important;
}
[data-testid="stSlider"] [data-baseweb="slider"] div[class*="Track"]:first-of-type {
    background: #10b981 !important;
}

/* Non-sidebar radio (e.g. inside pages) */
[data-testid="stRadio"]:not([data-testid="stSidebar"] *) label p {
    color: #1a2e23 !important;
    font-size: 13px !important;
}
[data-testid="stRadio"]:not([data-testid="stSidebar"] *) [data-baseweb="radio"] div {
    border-color: #10b981 !important;
}

/* st.caption */
[data-testid="stCaptionContainer"] p {
    color: #6b7f78 !important;
    font-size: 12px !important;
}

/* st.code / inline code blocks (used in info banners) */
.stMarkdown code {
    background: #ecfdf5 !important;
    color: #059669 !important;
    border-radius: 4px !important;
    padding: 1px 6px !important;
    font-size: 12px !important;
}

/* Sidebar divider hr */
[data-testid="stSidebar"] hr {
    border-color: #1e3a2e !important;
    margin: 10px 0 !important;
}

/* Sidebar select / filter labels */
[data-testid="stSidebar"] [data-baseweb="select"] > div {
    background: #0d2318 !important;
    border-color: #1e3a2e !important;
    color: #d1fae5 !important;
}
[data-testid="stSidebar"] [data-baseweb="select"] [class*="singleValue"],
[data-testid="stSidebar"] [data-baseweb="select"] [class*="placeholder"] {
    color: #a7f3d0 !important;
}
[data-testid="stSidebar"] [data-baseweb="select"] [class*="indicatorContainer"] svg {
    fill: #6ee7b7 !important;
}

/* ── Download button ── */
[data-testid="stDownloadButton"] > button {
    background: #ffffff !important;
    color: #059669 !important;
    border: 1px solid #10b981 !important;
    font-weight: 600 !important;
    font-size: 12px !important;
}
[data-testid="stDownloadButton"] > button:hover {
    background: #f0fdf9 !important;
}

/* ── Radio buttons in sidebar ── */
[data-testid="stSidebar"] [data-testid="stRadio"] div[role="radiogroup"] label {
    padding: 9px 12px !important;
    border-radius: 8px !important;
    margin: 2px 0 !important;
    transition: background 0.15s, color 0.15s;
    cursor: pointer;
    border-left: 3px solid transparent;
}
[data-testid="stSidebar"] [data-testid="stRadio"] div[role="radiogroup"] label:hover {
    background: rgba(16,185,129,0.12) !important;
    border-left-color: rgba(16,185,129,0.4) !important;
}
/* Selected nav item: solid green left bar + tinted background */
[data-testid="stSidebar"] [data-testid="stRadio"] div[role="radiogroup"] label:has(input:checked) {
    background: rgba(16,185,129,0.18) !important;
    border-left: 3px solid #10b981 !important;
}
[data-testid="stSidebar"] [data-testid="stRadio"] div[role="radiogroup"] label:has(input:checked) p {
    color: #ecfdf5 !important;
    font-weight: 600 !important;
}
/* Hide the radio dot — we use the left bar as the selection indicator */
[data-testid="stSidebar"] [data-testid="stRadio"] [data-testid="stMarkdownContainer"] + div,
[data-testid="stSidebar"] input[type="radio"] { display: none !important; }
</style>
""", unsafe_allow_html=True)


# ============================================================================
# CACHED DATA LOADERS
# FIX: All loaders use explicit named parameters, never **kwargs.
#      @st.cache_data requires hashable arguments. Dict (**kwargs) is not
#      hashable → TypeError → silent None return → blank pages.
# ============================================================================

@st.cache_data(ttl=300, show_spinner=False)
def _load_health() -> dict:
    return api.get_health()


@st.cache_data(ttl=300, show_spinner=False)
def _load_top_skills(limit: int, country: Optional[str], posted_after: Optional[str]) -> list:
    return api.get_top_skills(limit=limit, country=country, posted_after=posted_after)


@st.cache_data(ttl=300, show_spinner=False)
def _load_scrape_runs(limit: int) -> list:
    return api.get_scrape_runs(limit=limit)


@st.cache_data(ttl=60, show_spinner=False)
def _load_jobs(
    skill:            Optional[str],
    country:          Optional[str],
    city:             Optional[str],
    company_name:     Optional[str],
    experience_level: Optional[str],
    job_type:         Optional[str],
    is_remote:        Optional[bool],
    source_platform:  Optional[str],
    search_query:     Optional[str],
    posted_after:     Optional[str],
    posted_before:    Optional[str],
    page:             int,
    page_size:        int,
    order_by:         str,
    descending:       bool,
) -> dict:
    """
    FIX: Explicit named parameters instead of **kwargs.
    Every argument is hashable (str, int, bool, None) so @st.cache_data
    can build a deterministic cache key without raising TypeError.
    """
    return api.get_jobs(
        skill=skill,
        country=country,
        city=city,
        company_name=company_name,
        experience_level=experience_level,
        job_type=job_type,
        is_remote=is_remote,
        source_platform=source_platform,
        search_query=search_query,
        posted_after=posted_after,
        posted_before=posted_before,
        page=page,
        page_size=page_size,
        order_by=order_by,
        descending=descending,
    )


# ============================================================================
# SIDEBAR
# ============================================================================

def _render_sidebar() -> dict:
    with st.sidebar:
        # ── Brand header ─────────────────────────────────────────────
        st.markdown("""
        <div style="padding:20px 16px 10px">
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:4px">
                <span style="font-size:22px">💼</span>
                <span style="color:#ecfdf5;font-size:16px;font-weight:700;letter-spacing:-0.3px">
                    Job Market Intel
                </span>
            </div>
            <p style="color:#4ade80;font-size:11px;margin:0;letter-spacing:0.04em;
                      font-weight:500;text-transform:uppercase">
                AI-Powered Analytics
            </p>
        </div>
        """, unsafe_allow_html=True)

        st.markdown('<hr style="border:none;border-top:1px solid #1a3327;margin:6px 0 14px">', unsafe_allow_html=True)

        # ── API health indicator ──────────────────────────────────────
        health = _load_health()
        api_ok = health.get("api_status") == "ok"
        db_ok  = health.get("database_connected", False)

        if api_ok and db_ok:
            st.markdown("""
            <div style="background:#052e16;border:1px solid #166534;border-radius:8px;
                        padding:8px 12px;margin:0 0 12px;display:flex;align-items:center;gap:8px">
                <span style="font-size:8px;color:#4ade80">●</span>
                <span style="color:#86efac;font-size:12px;font-weight:500">API & Database connected</span>
            </div>""", unsafe_allow_html=True)
        elif api_ok:
            st.markdown("""
            <div style="background:#422006;border:1px solid #92400e;border-radius:8px;
                        padding:8px 12px;margin:0 0 12px">
                <span style="color:#fcd34d;font-size:12px">⚠ API up — Database issue</span>
            </div>""", unsafe_allow_html=True)
        else:
            st.markdown("""
            <div style="background:#450a0a;border:1px solid #991b1b;border-radius:8px;
                        padding:8px 12px;margin:0 0 12px">
                <span style="color:#fca5a5;font-size:12px">✕ API offline — start FastAPI</span>
            </div>""", unsafe_allow_html=True)

        # ── Navigation ────────────────────────────────────────────────
        st.markdown('<p style="color:#4b7a63;font-size:10px;font-weight:700;letter-spacing:0.1em;'
                    'text-transform:uppercase;margin:0 0 6px;padding:0 4px">Navigation</p>',
                    unsafe_allow_html=True)

        page = st.radio(
            "nav",
            ["📊 Overview", "🔧 Skills", "💰 Salary", "🔍 Jobs"],
            label_visibility="collapsed",
        )

        st.markdown('<hr style="border:none;border-top:1px solid #1a3327;margin:14px 0">', unsafe_allow_html=True)

        # ── Filters ───────────────────────────────────────────────────
        st.markdown('<p style="color:#4b7a63;font-size:10px;font-weight:700;letter-spacing:0.1em;'
                    'text-transform:uppercase;margin:0 0 8px;padding:0 4px">Filters</p>',
                    unsafe_allow_html=True)

        country_opts = ["🌍 All Countries", "US", "IN", "GB", "DE", "CA", "AU", "BR", "FR", "NL", "SG"]
        c_sel = st.selectbox("Country", country_opts, label_visibility="collapsed")
        country: Optional[str] = None if c_sel == "🌍 All Countries" else c_sel

        time_opts = {"All time": None, "Last 7 days": 7, "Last 30 days": 30, "Last 90 days": 90}
        t_sel = st.selectbox("Time range", list(time_opts.keys()), label_visibility="collapsed")
        days = time_opts[t_sel]
        posted_after: Optional[str] = None
        if days:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            posted_after = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

        st.markdown('<hr style="border:none;border-top:1px solid #1a3327;margin:14px 0">', unsafe_allow_html=True)

        if st.button("↺  Refresh Data", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

        # ── Footer stats ──────────────────────────────────────────────
        total = health.get("total_jobs", 0)
        latest = health.get("latest_scrape_run") or {}
        st.markdown(f"""
        <div style="margin-top:20px;padding:12px;background:#051a0e;border-radius:8px;
                    border:1px solid #1a3327">
            <p style="color:#4b7a63;font-size:10px;font-weight:700;letter-spacing:0.06em;
                      text-transform:uppercase;margin:0 0 8px">Platform Stats</p>
            <p style="color:#86efac;font-size:20px;font-weight:700;margin:0">{total:,}</p>
            <p style="color:#4b7a63;font-size:11px;margin:0 0 8px">total jobs indexed</p>
            <p style="color:#4b7a63;font-size:11px;margin:0">
                Last scrape: <span style="color:#a7f3d0">
                {utils.fmt_relative_time(latest.get("started_at")) if latest else "Never"}
                </span>
            </p>
        </div>
        <p style="color:#2d5040;font-size:10px;margin:10px 4px 0;text-align:center">
            Cache refreshes every 5 min
        </p>
        """, unsafe_allow_html=True)

    return {"page": page, "country": country, "posted_after": posted_after}


# ============================================================================
# PAGE 1 — OVERVIEW
# ============================================================================

def _page_overview(ctx: dict) -> None:
    # ── Page header ───────────────────────────────────────────────────
    st.markdown("""
    <div style="margin-bottom:24px">
        <h1 style="color:#0d1f17;font-size:26px;font-weight:700;margin:0;letter-spacing:-0.5px">
            Market Overview
        </h1>
        <p style="color:#6b7f78;font-size:14px;margin:4px 0 0">
            Platform health, live metrics, and key demand signals
        </p>
    </div>
    """, unsafe_allow_html=True)

    health      = _load_health()
    skills      = _load_top_skills(limit=10, country=ctx["country"], posted_after=ctx["posted_after"])
    scrape_runs = _load_scrape_runs(limit=8)
    latest_run  = health.get("latest_scrape_run") or {}

    # Load a page of recent jobs for derived metrics (top companies, titles, platform split)
    recent = _load_jobs(
        skill=None, country=ctx["country"], city=None, company_name=None,
        experience_level=None, job_type=None, is_remote=None, source_platform=None,
        search_query=None, posted_after=ctx["posted_after"], posted_before=None,
        page=1, page_size=100, order_by="posted_at", descending=True,
    )
    recent_jobs = recent.get("items", []) if recent else []

    # Derive counts from recent_jobs for fallback metrics
    remote_count  = sum(1 for j in recent_jobs if j.get("is_remote"))
    total_recent  = len(recent_jobs)
    remote_pct    = f"{round(remote_count / total_recent * 100)}%" if total_recent else "—"

    # ── KPI row ───────────────────────────────────────────────────────
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        utils.metric_card("Total Jobs Indexed", utils.fmt_number(health.get("total_jobs", 0)))
    with m2:
        # Fall back to job count from recent page when ML skills aren't ready
        skills_val = str(len(skills)) if skills else (str(total_recent) + " jobs")
        skills_label = "Skills Tracked" if skills else "Recent Jobs"
        utils.metric_card(skills_label, skills_val)
    with m3:
        utils.metric_card("Remote Jobs", remote_pct)
    with m4:
        last_s = utils.fmt_relative_time(latest_run.get("started_at")) if latest_run else "Never"
        utils.metric_card("Last Scrape", last_s)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Main content ──────────────────────────────────────────────────
    left, right = st.columns([3, 2], gap="large")

    with left:
        _section_label("Top In-Demand Skills")
        if skills:
            st.plotly_chart(charts.bar_top_skills(skills, title="", max_skills=10),
                            use_container_width=True)
        elif recent_jobs:
            # ML not run yet — show top job titles as a useful fallback
            from collections import Counter
            title_counts = Counter(
                j.get("title", "").split("(")[0].strip()
                for j in recent_jobs if j.get("title")
            ).most_common(10)
            if title_counts:
                import pandas as pd
                df_titles = pd.DataFrame(title_counts, columns=["title", "count"])
                fig_t = px.bar(
                    df_titles.sort_values("count"),
                    x="count", y="title", orientation="h",
                    color="count",
                    color_continuous_scale=["#d1fae5", "#10b981", "#065f46"],
                    labels={"count": "Postings", "title": ""},
                )
                fig_t.update_coloraxes(showscale=False)
                fig_t.update_traces(marker_line_width=0, hovertemplate="<b>%{y}</b>  ·  %{x:,}<extra></extra>")
                fig_t.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                                    margin=dict(l=10,r=10,t=10,b=10), font=dict(color="#374151", family="Inter, sans-serif"))
                fig_t.update_xaxes(showgrid=False, zeroline=False, color="#9ca3af")
                fig_t.update_yaxes(showgrid=False, zeroline=False, color="#374151")
                st.plotly_chart(fig_t, use_container_width=True)
                st.caption("Showing top job titles — run `python -m ml.scheduler --only-skills` for skill analytics")
        else:
            _empty_card("No skill data yet", "python -m ml.scheduler --skip-scraper")

    with right:
        _section_label("Top Hiring Companies")
        if recent_jobs:
            from collections import Counter
            co_counts = Counter(
                j.get("company_name") for j in recent_jobs
                if j.get("company_name")
            ).most_common(8)
            if co_counts:
                import pandas as pd
                df_co = pd.DataFrame(co_counts, columns=["company", "jobs"])
                fig_co = px.bar(
                    df_co.sort_values("jobs"),
                    x="jobs", y="company", orientation="h",
                    color="jobs",
                    color_continuous_scale=["#d1fae5", "#059669"],
                    labels={"jobs": "Job Postings", "company": ""},
                )
                fig_co.update_coloraxes(showscale=False)
                fig_co.update_traces(marker_line_width=0, marker=dict(cornerradius=3),
                                     hovertemplate="<b>%{y}</b>  ·  %{x}<extra></extra>")
                fig_co.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                                     margin=dict(l=10,r=10,t=10,b=10), font=dict(color="#374151", family="Inter, sans-serif"))
                fig_co.update_xaxes(showgrid=False, zeroline=False, color="#9ca3af")
                fig_co.update_yaxes(showgrid=False, zeroline=False, color="#374151")
                st.plotly_chart(fig_co, use_container_width=True)
        elif scrape_runs:
            st.plotly_chart(charts.bar_scrape_history(scrape_runs, title=""), use_container_width=True)
        else:
            _empty_card("No data yet", "Run the scraper first")

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Audit table ───────────────────────────────────────────────────
    _section_label("Scraper Audit Log")
    if scrape_runs:
        df = utils.scrape_runs_to_dataframe(scrape_runs)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        _empty_card("No scrape runs recorded", "python -m scraper.playwright_scraper")

    if latest_run:
        with st.expander("Latest Scrape Run Detail"):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Status",     utils.fmt_status_badge(latest_run.get("status", "")))
            c2.metric("Inserted",   utils.fmt_number(latest_run.get("jobs_inserted", 0)))
            c3.metric("Found",      utils.fmt_number(latest_run.get("jobs_found", 0)))
            c4.metric("Duplicates", utils.fmt_number(latest_run.get("jobs_skipped_duplicate", 0)))
            if err := latest_run.get("error_message"):
                st.error(f"Error: {err[:300]}")


# ============================================================================
# PAGE 2 — SKILLS
# ============================================================================

def _page_skills(ctx: dict) -> None:
    st.markdown("""
    <div style="margin-bottom:24px">
        <h1 style="color:#0d1f17;font-size:26px;font-weight:700;margin:0;letter-spacing:-0.5px">
            Skills Analytics
        </h1>
        <p style="color:#6b7f78;font-size:14px;margin:4px 0 0">
            Demand signals across all scraped job postings
        </p>
    </div>
    """, unsafe_allow_html=True)

    # Controls
    c_search, c_limit = st.columns([3, 1])
    with c_search:
        term = st.text_input("Search skills", placeholder="python, docker, react…", label_visibility="collapsed")
    with c_limit:
        limit = st.selectbox("Show", [10, 20, 30, 50], index=1, label_visibility="collapsed")

    skills = _load_top_skills(
        limit=limit,
        country=ctx["country"],
        posted_after=ctx["posted_after"],
    )

    if term and skills:
        skills = [s for s in skills if term.lower() in s["skill"].lower()]

    if not skills:
        _empty_card(
            "No skill data available",
            "python -m ml.scheduler --only-skills",
        )
        return

    # Main chart
    st.plotly_chart(
        charts.bar_top_skills(skills, title=f"Top {len(skills)} Skills by Job Count", max_skills=limit),
        use_container_width=True,
    )

    st.markdown("<br>", unsafe_allow_html=True)

    tab_table, tab_pie = st.tabs(["  📋  Table  ", "  🥧  Distribution  "])

    with tab_table:
        df = utils.skills_to_dataframe(skills)
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.download_button(
            "⬇️ Download CSV",
            df.to_csv(index=False),
            file_name="top_skills.csv",
            mime="text/csv",
        )

    with tab_pie:
        if len(skills) >= 4:
            mid = len(skills) // 2
            fig = charts.pie_remote_vs_onsite(
                remote_count=sum(s["job_count"] for s in skills[:mid]),
                onsite_count=sum(s["job_count"] for s in skills[mid:]),
                title="Top half vs bottom half by frequency",
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.markdown('<p style="color:#6b7f78;font-size:13px;padding:8px 0">Need at least 4 skills for the distribution chart.</p>', unsafe_allow_html=True)

    if skills:
        top = skills[0]
        st.info(f"💡 **{top['skill'].title()}** leads with **{utils.fmt_number(top['job_count'])}** job postings.")


# ============================================================================
# PAGE 3 — SALARY
# ============================================================================

def _page_salary(ctx: dict) -> None:
    st.markdown("""
    <div style="margin-bottom:24px">
        <h1 style="color:#0d1f17;font-size:26px;font-weight:700;margin:0;letter-spacing:-0.5px">
            Salary Intelligence
        </h1>
        <p style="color:#6b7f78;font-size:14px;margin:4px 0 0">
            Compensation benchmarks by skill, location, and seniority
        </p>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div style="background:#f0fdf9;border:1px solid #6ee7b7;border-left:4px solid #10b981;
                border-radius:10px;padding:12px 16px;margin-bottom:16px;font-size:13px;color:#065f46">
        ℹ️ Salary data comes from postings that disclose compensation (~15–25% on RemoteOK).
        Run <code style="background:#d1fae5;padding:1px 5px;border-radius:4px">
        python -m ml.scheduler --only-salary</code> to refresh aggregations.
    </div>
    """, unsafe_allow_html=True)

    # Fetch a page of jobs with salary — explicit params for cache safety
    result = _load_jobs(
        skill=None, country=ctx["country"], city=None,
        company_name=None, experience_level=None, job_type=None,
        is_remote=None, source_platform=None, search_query=None,
        posted_after=ctx["posted_after"], posted_before=None,
        page=1, page_size=100, order_by="posted_at", descending=True,
    )

    all_jobs = result.get("items", []) if result else []
    jobs_with_salary = [j for j in all_jobs if j.get("salary_min") or j.get("salary_max")]

    if not jobs_with_salary:
        _empty_card(
            "No salary data in current view",
            "Most listings don't disclose salary. Try removing date/country filters.",
        )
        return

    st.markdown(
        f'<div style="display:inline-block;background:#ecfdf5;border:1px solid #6ee7b7;'
        f'border-radius:20px;padding:4px 14px;font-size:13px;color:#065f46;font-weight:600;margin-bottom:16px">'
        f'✓ {len(jobs_with_salary)} jobs with disclosed salary in current view</div>',
        unsafe_allow_html=True,
    )
    st.markdown("<br>", unsafe_allow_html=True)

    salary_by_skill = utils.build_salary_by_skill(jobs_with_salary)

    chart_col, table_col = st.columns([3, 2], gap="large")

    with chart_col:
        _section_label("Salary by Skill — Median (USD)")
        st.plotly_chart(charts.bar_salary_by_skill(salary_by_skill[:15]), use_container_width=True)

    with table_col:
        _section_label("Top 10 Compensation Table")
        if salary_by_skill:
            import pandas as pd
            df_sal = pd.DataFrame(salary_by_skill[:10])[["skill", "median_usd", "sample_size"]]
            df_sal["median_usd"] = df_sal["median_usd"].apply(lambda x: utils.fmt_salary(int(x)))
            df_sal.columns = ["Skill", "Median (USD)", "# Jobs"]
            st.dataframe(df_sal, use_container_width=True, hide_index=True)
        else:
            st.markdown('<p style="color:#6b7f78;font-size:13px;padding:8px 0">No salary data for skills in current view.</p>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    _section_label("Remote vs On-site Compensation")

    def _mid(j):
        lo, hi = j.get("salary_min") or 0, j.get("salary_max") or 0
        return (lo + hi) / 2 if lo and hi else lo or hi

    remote_mids = [_mid(j) for j in jobs_with_salary if j.get("is_remote") and _mid(j) > 0]
    onsite_mids = [_mid(j) for j in jobs_with_salary if not j.get("is_remote") and _mid(j) > 0]

    r1, r2, r3 = st.columns(3)
    with r1:
        rmed = sorted(remote_mids)[len(remote_mids)//2] if remote_mids else None
        utils.metric_card("Remote Median", utils.fmt_salary(int(rmed)) if rmed else "—")
        st.caption(f"n={len(remote_mids)} jobs")
    with r2:
        omed = sorted(onsite_mids)[len(onsite_mids)//2] if onsite_mids else None
        utils.metric_card("On-site Median", utils.fmt_salary(int(omed)) if omed else "—")
        st.caption(f"n={len(onsite_mids)} jobs")
    with r3:
        if rmed and omed and omed > 0:
            prem = round((rmed - omed) / omed * 100, 1)
            utils.metric_card("Remote Premium", f"{prem:+.1f}%")
        else:
            utils.metric_card("Remote Premium", "—")
        st.caption("vs on-site median")

    st.markdown("<br>", unsafe_allow_html=True)
    all_mids = [_mid(j) for j in jobs_with_salary if _mid(j) > 0]
    if all_mids:
        import pandas as pd
        _section_label("Salary Distribution")
        fig_hist = px.histogram(
            x=all_mids, nbins=20,
            labels={"x": "Annual Salary (USD)"},
            color_discrete_sequence=["#10b981"],
        )
        fig_hist.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            showlegend=False, margin=dict(l=10, r=10, t=20, b=10),
            font=dict(color="#6b7f78", family="Inter, sans-serif"),
        )
        fig_hist.update_xaxes(tickprefix="$", tickformat=",", color="#6b7f78", gridcolor="#e2ece8")
        fig_hist.update_yaxes(color="#6b7f78", gridcolor="#e2ece8")
        st.plotly_chart(fig_hist, use_container_width=True)


# ============================================================================
# PAGE 4 — JOB EXPLORER
# ============================================================================

def _page_jobs(ctx: dict) -> None:
    st.markdown("""
    <div style="margin-bottom:24px">
        <h1 style="color:#0d1f17;font-size:26px;font-weight:700;margin:0;letter-spacing:-0.5px">
            Job Explorer
        </h1>
        <p style="color:#6b7f78;font-size:14px;margin:4px 0 0">
            Search, filter and browse all indexed job postings
        </p>
    </div>
    """, unsafe_allow_html=True)

    # ── Filter row ────────────────────────────────────────────────────
    f1, f2, f3, f4 = st.columns(4)
    with f1:
        search  = st.text_input("Search title/keyword", placeholder="machine learning", label_visibility="collapsed")
    with f2:
        skill_f = st.text_input("Skill filter", placeholder="python", label_visibility="collapsed")
    with f3:
        company_f = st.text_input("Company", placeholder="stripe", label_visibility="collapsed")
    with f4:
        remote_map = {"All": None, "Remote": True, "On-site": False}
        r_sel = st.selectbox("Work type", list(remote_map.keys()), label_visibility="collapsed")
        is_remote = remote_map[r_sel]

    f5, f6, f7 = st.columns([2, 2, 1])
    with f5:
        exp_opts = ["All", "internship", "entry", "mid", "senior", "lead", "principal", "executive"]
        exp_sel = st.selectbox("Experience", exp_opts, label_visibility="collapsed")
        exp_level = None if exp_sel == "All" else exp_sel
    with f6:
        sort_map = {
            "Newest":   ("posted_at",    True),
            "Oldest":   ("posted_at",    False),
            "Salary ↑": ("salary_max",   True),
            "Company":  ("company_name", False),
        }
        sort_sel = st.selectbox("Sort", list(sort_map.keys()), label_visibility="collapsed")
        order_by, descending = sort_map[sort_sel]
    with f7:
        page_size = st.selectbox("Per page", [10, 20, 50], index=1, label_visibility="collapsed")

    # ── Session state pagination ──────────────────────────────────────
    if "jobs_page" not in st.session_state:
        st.session_state.jobs_page = 1

    # Reset to page 1 when any filter changes
    fkey = f"{search}|{skill_f}|{company_f}|{is_remote}|{exp_level}|{ctx['country']}|{ctx['posted_after']}"
    if st.session_state.get("_jobs_fkey") != fkey:
        st.session_state.jobs_page = 1
        st.session_state["_jobs_fkey"] = fkey

    # ── Fetch ─────────────────────────────────────────────────────────
    with st.spinner("Loading jobs…"):
        result = _load_jobs(
            skill=skill_f or None,
            country=ctx["country"],
            city=None,
            company_name=company_f or None,
            experience_level=exp_level,
            job_type=None,
            is_remote=is_remote,
            source_platform=None,
            search_query=search or None,
            posted_after=ctx["posted_after"],
            posted_before=None,
            page=st.session_state.jobs_page,
            page_size=page_size,
            order_by=order_by,
            descending=descending,
        )

    jobs  = result.get("items", []) if result else []
    total = result.get("total", 0) if result else 0

    st.caption(f"**{total:,}** jobs match your filters")

    if not jobs:
        # Determine which filters are active to give targeted advice
        active_filters = [f for f, v in [
            ("skill", skill_f), ("company", company_f), ("search", search),
            ("experience", exp_level), ("country", ctx["country"]),
            ("date range", ctx["posted_after"]),
        ] if v]

        hint = "No jobs found with current filters."
        if active_filters:
            hint = f"No jobs found. Active filters: **{', '.join(active_filters)}**."

        st.markdown(f"""
        <div style="background:#ffffff;border:1px solid #e2ece8;border-left:4px solid #10b981;
                    border-radius:12px;padding:24px 28px;margin:8px 0 16px;
                    box-shadow:0 1px 4px rgba(16,185,129,0.06)">
            <p style="font-size:20px;margin:0 0 6px">🔍</p>
            <p style="color:#0d1f17;font-weight:600;font-size:15px;margin:0 0 4px">{hint}</p>
            <p style="color:#6b7f78;font-size:13px;margin:0 0 12px">Try one of these:</p>
            <ul style="color:#6b7f78;font-size:13px;margin:0;padding-left:18px;line-height:1.8">
                <li>Clear the skill or keyword filter</li>
                <li>Switch <b>Work type</b> to <b>All</b></li>
                <li>Change <b>Experience</b> to <b>All</b></li>
                <li>Set the sidebar time range to <b>All time</b></li>
                <li>Change the sidebar country to <b>All Countries</b></li>
            </ul>
        </div>
        """, unsafe_allow_html=True)
        return

    df = utils.jobs_to_dataframe(jobs)
    display_cols = [c for c in df.columns if c not in ("ID", "URL")]
    st.dataframe(df[display_cols], use_container_width=True, hide_index=True)

    st.session_state.jobs_page = utils.render_pagination(
        total=total, page=st.session_state.jobs_page,
        page_size=page_size, key_prefix="jobs",
    )

    st.markdown("<br>", unsafe_allow_html=True)
    _section_label("Job Detail")

    job_options = ["— select a job —"] + [
        f"[{j['id']}] {j.get('title','?')} — {j.get('company_name') or 'Unknown'}"
        for j in jobs
    ]
    selected = st.selectbox("Job", job_options, label_visibility="collapsed")

    if selected != "— select a job —":
        job_id = int(selected.split("]")[0].replace("[", ""))
        job = next((j for j in jobs if j.get("id") == job_id), None)

        if job:
            _render_job_card(job)


def _render_job_card(job: dict) -> None:
    """Renders a styled detail card for one job."""
    utils.card_html(
        f'<h3 style="color:#0d1f17;margin:0 0 4px;font-size:18px;font-weight:700">'
        f'{job.get("title","")}</h3>'
        f'<p style="color:#059669;margin:0;font-size:14px;font-weight:500">'
        f'{job.get("company_name") or "Unknown company"}</p>'
    )

    c1, c2, c3 = st.columns(3)
    c1.metric("Location",   utils.fmt_location(job))
    c2.metric("Level",      (job.get("experience_level") or "—").title())
    c3.metric("Salary",     utils.fmt_salary_range(
        job.get("salary_min"), job.get("salary_max"), job.get("salary_currency") or "USD"
    ))

    r1, r2, r3 = st.columns(3)
    r1.metric("Type",     (job.get("job_type") or "—").replace("_", " ").title())
    r2.metric("Remote",   "✓ Yes" if job.get("is_remote") else "✗ No")
    r3.metric("Posted",   utils.fmt_relative_time(job.get("posted_at")))

    st.markdown("**Skills**")
    utils.skill_tags(job.get("skills") or [])

    src_url = job.get("source_url", "")
    platform = (job.get("source_platform") or "").title()
    if src_url:
        st.markdown(f"**Source:** [{platform}]({src_url})")

    if st.button("📄 Load full description", key=f"desc_{job['id']}"):
        with st.spinner("Fetching…"):
            full = api.get_job_detail(job["id"])
        if full and full.get("description"):
            desc = full["description"][:2500].replace("\n", "  \n")
            with st.expander("Description", expanded=True):
                st.markdown(desc)
        else:
            st.markdown('<p style="color:#6b7f78;font-size:13px;padding:6px 0">No description available for this job.</p>', unsafe_allow_html=True)


# ============================================================================
# SHARED HELPERS
# ============================================================================

def _empty_card(title: str, subtitle: str = "") -> None:
    """Compact empty-state card — never crashes, never takes up excessive space."""
    sub_html = (
        f'<code style="background:#ecfdf5;color:#059669;padding:2px 7px;'
        f'border-radius:5px;font-size:11px">{subtitle}</code>'
        if subtitle else ""
    )
    st.markdown(
        f'<div style="background:#ffffff;border:1px solid #e2ece8;border-radius:12px;'
        f'padding:16px 20px;margin:4px 0 8px;'
        f'box-shadow:0 1px 3px rgba(16,185,129,0.05)">'
        f'<span style="color:#9ca3af;font-size:13px">📭 {title}</span>'
        f'{"  " + sub_html if sub_html else ""}'
        f'</div>',
        unsafe_allow_html=True,
    )


def _section_label(text: str) -> None:
    """Renders a consistent section label in the new green/white theme."""
    st.markdown(
        f'<p style="color:#059669;font-size:11px;font-weight:700;letter-spacing:0.08em;'
        f'text-transform:uppercase;margin:0 0 10px;border-left:3px solid #10b981;'
        f'padding-left:8px">{text}</p>',
        unsafe_allow_html=True,
    )


# ============================================================================
# MAIN ROUTER
# FIX: Called ONCE unconditionally at module level.
#      The old code had:
#          if __name__ == "__main__": main()
#          else: main()
#      Streamlit runs the file AS __main__, so BOTH branches ran, causing
#      DuplicateWidgetID for every sidebar widget → silent blank pages.
# ============================================================================

def main() -> None:
    ctx = _render_sidebar()
    page = ctx["page"]

    if   "Overview" in page: _page_overview(ctx)
    elif "Skills"   in page: _page_skills(ctx)
    elif "Salary"   in page: _page_salary(ctx)
    elif "Jobs"     in page: _page_jobs(ctx)


main()
