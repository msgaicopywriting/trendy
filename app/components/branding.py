"""Shared msg life Slovakia visual identity, applied on every Trendy page.

Mirrors the internal schema-validator tool: white shell, burgundy accent,
Open Sans, neutral grays, and the official "msg life" wordmark in the top bar.
"""
from __future__ import annotations

import streamlit as st

# ── msg life brand tokens (from the internal tool design system) ─────────────
MSG_RED = "#A01B42"         # primary burgundy accent (interactive + logo dot)
MSG_RED_DARK = "#7E1534"    # hover / active
MSG_TEXT = "#1A1A1A"        # body / heading text
MSG_MUTED = "#6F6F6F"       # captions / breadcrumb / wordmark
MSG_BORDER = "#E5E5E5"      # hairline borders
MSG_BG_SOFT = "#FAFAFA"     # cards / secondary background

_CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Open+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

html, body, .stApp, [data-testid="stMarkdownContainer"],
[data-testid="stSidebar"], input, textarea, button, select {{
    font-family: 'Open Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}}

code, pre, [data-testid="stCodeBlock"], .stCode, kbd {{
    font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
}}

h1, h2, h3 {{ color: {MSG_TEXT}; font-weight: 700; letter-spacing: -0.01em; }}

/* Primary buttons → burgundy */
.stButton > button[kind="primary"] {{
    background: {MSG_RED}; border: 0; border-radius: 6px; font-weight: 600;
}}
.stButton > button[kind="primary"]:hover {{ background: {MSG_RED_DARK}; }}

/* Secondary buttons → outlined burgundy */
.stButton > button[kind="secondary"] {{
    border: 1px solid {MSG_BORDER}; color: {MSG_TEXT}; border-radius: 6px; font-weight: 600;
}}
.stButton > button[kind="secondary"]:hover {{
    border-color: {MSG_RED}; color: {MSG_RED};
}}

/* Inputs: burgundy focus ring (like the validator) */
input:focus, textarea:focus, [data-baseweb="input"]:focus-within,
[data-baseweb="select"]:focus-within {{
    border-color: {MSG_RED} !important;
    box-shadow: 0 0 0 2px rgba(160, 27, 66, 0.10) !important;
}}

/* Metric cards → clean white panels */
[data-testid="stMetric"] {{
    background: #FFFFFF; border: 1px solid {MSG_BORDER};
    border-radius: 6px; padding: 14px 18px;
}}
[data-testid="stMetricValue"] {{ color: {MSG_RED}; font-weight: 700; }}

/* Sidebar */
[data-testid="stSidebar"] {{ background: #FFFFFF; border-right: 1px solid {MSG_BORDER}; }}
[data-testid="stSidebar"] h1 {{ color: {MSG_RED}; }}

/* Top brand bar (logo + breadcrumb), mirrors the validator header */
.msg-bar {{
    display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
    border-bottom: 1px solid {MSG_BORDER};
    padding: 2px 0 12px; margin: 0 0 16px;
}}
.msg-bar svg {{ display: block; flex-shrink: 0; }}
.msg-bar .sep {{ color: {MSG_BORDER}; font-size: 1.25rem; font-weight: 300; }}
.msg-bar .crumb {{
    color: {MSG_MUTED}; font-size: 0.82rem; font-weight: 600;
    letter-spacing: 0.04em; text-transform: uppercase;
}}
.msg-tagline {{ color: {MSG_MUTED}; font-size: 0.9rem; margin: -8px 0 16px; }}
</style>
"""

# Official "msg life" wordmark — burgundy dot rgb(160,27,66) + gray wordmark rgb(111,111,111).
_LOGO_SVG = (
    '<svg viewBox="0 0 150 48" height="26" xmlns="http://www.w3.org/2000/svg" '
    'fill-rule="evenodd" clip-rule="evenodd" aria-label="msg life">'
    '<g transform="matrix(0.306122,0,0,0.306122,0,0)">'
    '<path d="M0,93.229C0,79.139 11.421,67.717 25.511,67.717C39.602,67.717 51.023,79.139 '
    '51.023,93.229C51.023,107.318 39.602,118.74 25.511,118.74C11.421,118.74 0,107.318 0,93.229Z" '
    'fill="rgb(160,27,66)"></path>'
    '<path d="M67.464,0.751L67.464,117.992L88.859,117.992L88.859,18.59L137.864,18.59L137.864,117.992L159.327,117.992'
    'L159.327,18.59L191.92,18.59C202.927,18.59 210.698,25.195 210.698,39.52L210.698,117.992L232.111,117.992'
    'L232.111,33.591C232.111,17.624 223.875,0.751 202.698,0.751L67.464,0.751Z" fill="rgb(111,111,111)"></path>'
    '<path d="M274.967,0.751C251.857,0.751 244.855,16.1 244.855,33.288C244.855,51.648 252.484,67.113 273.484,67.113'
    'L324.412,67.113C340.016,67.113 340.016,79.108 340.016,84.565C340.016,89.651 340.016,100.118 324.803,100.118'
    'L246.855,100.118L246.855,117.992L333.76,117.992C355.434,117.992 361.641,105.26 361.641,84.723'
    'C361.641,60.254 353.65,49.287 335.129,49.287L280.141,49.287C266.266,49.287 266.266,39.912 266.266,33.663'
    'C266.266,28.788 267.641,18.59 282.516,18.59L357.641,18.59L357.641,0.751L274.967,0.751Z" fill="rgb(111,111,111)"></path>'
    '<path d="M489.971,0.751L489.971,128.432C489.971,145.15 483.531,154 463.643,154L389.881,154L389.881,135.232'
    'L456.807,135.232C466.945,135.232 468.643,131.373 468.652,122.525C468.668,121.574 468.643,117.992 468.643,117.992'
    'L417.299,117.992C397.053,117.992 390.461,114.754 386.479,111.912C380.566,106.943 374.783,101.117 374.783,80.291'
    'L374.783,37.965C374.893,18.308 380.123,12.526 385.834,7.972C391.066,3.799 398.166,0.751 417.25,0.751L489.971,0.751Z'
    'M468.643,18.59L468.643,100.118L423.748,100.118C410.479,100.196 397.023,101.117 396.674,83.934'
    'C396.66,83.221 396.604,76.893 396.674,58.513C396.734,42.924 396.592,39.199 396.674,37.586'
    'C397.416,23.188 402.729,18.064 424.695,18.59C425.982,18.622 468.643,18.59 468.643,18.59Z" '
    'fill="rgb(111,111,111)" fill-rule="evenodd"></path>'
    '</g></svg>'
)


def apply_branding() -> None:
    """Inject the msg life theme CSS. Call once near the top of every page."""
    st.markdown(_CSS, unsafe_allow_html=True)


def render_header(breadcrumb: str | None = None, tagline: str | None = None) -> None:
    """Render the msg life top bar: wordmark logo + optional breadcrumb / tagline."""
    crumb = (
        f'<span class="sep">/</span><span class="crumb">{breadcrumb}</span>'
        if breadcrumb else ""
    )
    st.markdown(
        f'<div class="msg-bar">{_LOGO_SVG}{crumb}</div>',
        unsafe_allow_html=True,
    )
    if tagline:
        st.markdown(f"<div class='msg-tagline'>{tagline}</div>", unsafe_allow_html=True)
