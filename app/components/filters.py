"""Reusable sidebar filter components."""
from __future__ import annotations

import streamlit as st
from trendy.config import PORTALS


TAG_LABELS = {
    "rising": "🚀 Rising",
    "newly_discovered": "✨ Newly discovered",
    "gap": "🎯 Gap",
    "refresh": "♻️ Refresh",
}

STATUS_LABELS = {
    "new": "🆕 New",
    "seen": "👁️ Seen",
    "accepted": "✅ Accepted",
    "in_progress": "🚧 In progress",
    "published": "🟢 Published",
    "rejected": "❌ Rejected",
    "snoozed": "💤 Snoozed",
}


def portal_selector(label: str = "Portál", key: str = "portal_sel") -> str:
    """Portal selectbox with a key shared across all pages, so the chosen
    portal follows the user when navigating (Home → Portál → Kanban...)."""
    options = list(PORTALS.keys())
    # Re-pin the widget value as app state — Streamlit drops widget keys for
    # widgets not rendered on the current page, which would reset the selection
    # on every page switch.
    if key in st.session_state:
        st.session_state[key] = st.session_state[key]
    return st.sidebar.selectbox(
        label,
        options=options,
        format_func=lambda k: PORTALS[k].name,
        key=key,
    )


def candidate_filters(key_prefix: str = "") -> dict:
    """Render sidebar candidate filters, return dict of filter values."""
    st.sidebar.subheader("Filtre")

    tags = st.sidebar.multiselect(
        "Tag",
        options=list(TAG_LABELS.keys()),
        format_func=lambda t: TAG_LABELS.get(t, t),
        default=["rising", "newly_discovered", "gap", "refresh"],
        key=f"{key_prefix}_tags",
    )

    statuses = st.sidebar.multiselect(
        "Status",
        options=list(STATUS_LABELS.keys()),
        format_func=lambda s: STATUS_LABELS.get(s, s),
        default=["new", "seen"],
        key=f"{key_prefix}_statuses",
    )

    min_score = st.sidebar.slider(
        "Min. TrendScore", 0, 100, 0,
        key=f"{key_prefix}_min_score",
    )

    show_cooldown = st.sidebar.checkbox(
        "Ukázať suppressed (cooldown)", value=False,
        key=f"{key_prefix}_show_cooldown",
    )

    return {
        "tags": tags,
        "statuses": statuses,
        "min_score": min_score,
        "show_cooldown": show_cooldown,
    }
