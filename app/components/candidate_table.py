"""Candidate table component with status actions."""
from __future__ import annotations

import streamlit as st
import pandas as pd
from trendy.db import Candidate
from app.components.filters import TAG_LABELS, STATUS_LABELS


def render_candidate_table(
    candidates: list[Candidate],
    key_prefix: str = "tbl",
    show_bulk_actions: bool = True,
) -> list[int]:
    """
    Render paginated candidate table.
    Returns list of selected candidate IDs.
    """
    if not candidates:
        st.info("Žiadni kandidáti pre aktuálne filtre.")
        return []

    rows = []
    for c in candidates:
        tag_label = TAG_LABELS.get(c.tag or "", c.tag or "—")
        cooldown_flag = "🔁" if c.is_returned_from_cooldown else ""
        rows.append({
            "ID": c.id,
            "Tag": tag_label,
            "🔁": cooldown_flag,
            "Kľúčovka": c.keyword,
            "Volume": c.volume or 0,
            "KD": c.kd if c.kd is not None else "—",
            "Trend M/M %": f"{c.trend_mom_pct:+.0f}%" if c.trend_mom_pct else "—",
            "GapScore": f"{c.gap_score:.0f}" if c.gap_score is not None else "—",
            "TrendScore": f"{c.trend_score:.0f}" if c.trend_score is not None else "—",
            "Klaster": c.cluster or "—",
            "Status": STATUS_LABELS.get(c.status, c.status),
        })

    df = pd.DataFrame(rows)

    # Render with st.dataframe + selection
    selection = st.dataframe(
        df.drop(columns=["ID"]),
        use_container_width=True,
        hide_index=True,
        column_config={
            "TrendScore": st.column_config.ProgressColumn(
                "TrendScore", min_value=0, max_value=100, format="%.0f"
            ),
            "Volume": st.column_config.NumberColumn("Volume", format="%d"),
        },
        on_select="rerun",
        selection_mode="multi-row",
        key=f"{key_prefix}_df",
    )

    selected_indices = selection.selection.rows if hasattr(selection, "selection") else []
    selected_ids = [rows[i]["ID"] for i in selected_indices]

    return selected_ids
