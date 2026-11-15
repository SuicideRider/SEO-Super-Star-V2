"""Look and feel for the dashboard: colours, cards and small display helpers."""

from __future__ import annotations

import html

import streamlit as st

CSS = """
<style>
:root { --violet:#6C4DF6; --pink:#F0489B; --teal:#12B5A6; --amber:#F5A524; --blue:#3B82F6; }
.block-container { padding-top: 2.2rem; }

.hero { background: linear-gradient(120deg, #6C4DF6 0%, #B14DF6 45%, #F0489B 100%);
        border-radius: 18px; padding: 26px 30px; color: #fff; margin-bottom: 18px;
        box-shadow: 0 10px 30px rgba(108,77,246,.25); }
.hero h1 { color:#fff; margin:0; padding:0; font-size:2.1rem; }
.hero p { margin:6px 0 12px; font-size:1.05rem; opacity:.95; }
.hero .chip { display:inline-block; background:rgba(255,255,255,.2); border-radius:999px;
              padding:4px 12px; margin:0 6px 4px 0; font-size:.85rem; }

.intro { border-left:5px solid var(--violet); background:rgba(108,77,246,.08);
         border-radius:10px; padding:12px 16px; margin:4px 0 16px; }
.intro b { color:var(--violet); }

/* Tabs as coloured pills */
.stTabs [data-baseweb="tab-list"] { gap:8px; flex-wrap:wrap; }
.stTabs [data-baseweb="tab"] { border-radius:999px; padding:8px 18px; height:auto;
        background:rgba(108,77,246,.08); font-weight:600; }
.stTabs [data-baseweb="tab"]:nth-child(1)[aria-selected="true"] { background:#6C4DF6; }
.stTabs [data-baseweb="tab"]:nth-child(2)[aria-selected="true"] { background:#3B82F6; }
.stTabs [data-baseweb="tab"]:nth-child(3)[aria-selected="true"] { background:#F0489B; }
.stTabs [data-baseweb="tab"]:nth-child(4)[aria-selected="true"] { background:#12B5A6; }
.stTabs [data-baseweb="tab"]:nth-child(5)[aria-selected="true"] { background:#E08A00; }
.stTabs [data-baseweb="tab"]:nth-child(6)[aria-selected="true"] { background:#7C3AED; }
.stTabs [data-baseweb="tab"][aria-selected="true"],
.stTabs [data-baseweb="tab"][aria-selected="true"] p { color:#fff; }
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] { display:none; }

/* Metric cards, each column its own colour */
[data-testid="stMetricValue"] { font-size:1.45rem; }
[data-testid="stMetric"] { background:#fff; border-radius:14px; padding:10px 10px;
        border:1px solid rgba(108,77,246,.15); border-top:5px solid var(--violet);
        box-shadow:0 4px 14px rgba(30,27,58,.06); }
[data-testid="stColumn"]:nth-child(2) [data-testid="stMetric"],
[data-testid="column"]:nth-child(2) [data-testid="stMetric"] { border-top-color:var(--blue); }
[data-testid="stColumn"]:nth-child(3) [data-testid="stMetric"],
[data-testid="column"]:nth-child(3) [data-testid="stMetric"] { border-top-color:var(--pink); }
[data-testid="stColumn"]:nth-child(4) [data-testid="stMetric"],
[data-testid="column"]:nth-child(4) [data-testid="stMetric"] { border-top-color:var(--teal); }
[data-testid="stColumn"]:nth-child(5) [data-testid="stMetric"],
[data-testid="column"]:nth-child(5) [data-testid="stMetric"] { border-top-color:var(--amber); }

/* Forms and buttons */
[data-testid="stForm"] { background:#fff; border-radius:16px;
        border:1px solid rgba(108,77,246,.18); box-shadow:0 4px 14px rgba(30,27,58,.05); }
button[kind="primary"], button[kind="primaryFormSubmit"] {
        background:linear-gradient(90deg,#6C4DF6,#F0489B); border:0; border-radius:999px;
        font-weight:700; padding:.5rem 1.4rem; }
button[kind="primary"]:hover, button[kind="primaryFormSubmit"]:hover {
        filter:brightness(1.08); box-shadow:0 6px 16px rgba(240,72,155,.35); }
[data-testid="stDownloadButton"] button { border-radius:999px; border-color:var(--teal);
        color:#0C8D82; font-weight:600; }
h3 { color:#3A2A9E; }

/* Sidebar status */
[data-testid="stSidebar"] { background:linear-gradient(180deg,#F0EDFF 0%,#FDEFF6 100%); }
.status { background:#fff; border-radius:10px; padding:8px 12px; margin-bottom:8px;
          border:1px solid rgba(108,77,246,.12); }
.status .name { font-weight:600; font-size:.9rem; }
.status .detail { font-size:.78rem; color:#5B5878; margin-top:2px; }
.pill { float:right; border-radius:999px; padding:1px 10px; font-size:.75rem;
        font-weight:700; color:#fff; }
.pill.good { background:#12B5A6; } .pill.warn { background:#E08A00; }
.pill.off { background:#9A98AD; }
</style>
"""

_GOOD = {"full", "cloud"}
_OFF = {"off"}
SEVERITY_ICONS = {"High": "🔴 High", "Medium": "🟠 Medium", "Low": "🟡 Low",
                  "Good": "🟢 Better", "Info": "🔵 Note"}


def apply() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def hero() -> None:
    st.markdown(
        '<div class="hero"><h1>⭐ SEO Super Star</h1>'
        "<p>Check a page, find keywords, study competitors and track what works — "
        "for Google and for AI answers.</p>"
        '<span class="chip">🔍 SEO</span><span class="chip">🤖 AI search (GEO)</span>'
        '<span class="chip">💬 Answer engines (AEO)</span>'
        '<span class="chip">💸 Free to run</span></div>', unsafe_allow_html=True)


def intro(what: str, how: str) -> None:
    """Short plain-language box at the top of a section."""
    st.markdown(f'<div class="intro"><b>{html.escape(what)}</b><br>{html.escape(how)}</div>',
                unsafe_allow_html=True)


def status_card(service: str, mode: str, detail: str) -> None:
    kind = "good" if mode.lower() in _GOOD else "off" if mode.lower() in _OFF else "warn"
    st.markdown(
        f'<div class="status"><span class="pill {kind}">{html.escape(mode)}</span>'
        f'<div class="name">{html.escape(service)}</div>'
        f'<div class="detail">{html.escape(detail)}</div></div>', unsafe_allow_html=True)


def score_badge(score: float) -> str:
    return "🟢" if score >= 70 else "🟠" if score >= 40 else "🔴"
