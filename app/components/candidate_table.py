"""Candidate table component with status actions."""
from __future__ import annotations

import streamlit as st
import pandas as pd
from trendy.db import Candidate
from components.filters import TAG_LABELS, STATUS_LABELS


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

    # Render with st.dataframe + selection. Every metric column carries a
    # tooltip saying where the number comes from — hover the column header.
    selection = st.dataframe(
        df.drop(columns=["ID"]),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Tag": st.column_config.TextColumn(
                "Tag",
                help="Typ príležitosti: 🚀 Rising = rastúci záujem · ✨ Newly discovered = novoobjavená téma · 🎯 Gap = nepokrytá téma · ♻️ Refresh = aktualizovať existujúci článok",
            ),
            "🔁": st.column_config.TextColumn(
                "🔁", help="Téma sa vrátila z cooldownu (bola už raz zamietnutá/publikovaná a znovu rastie)",
            ),
            "Volume": st.column_config.NumberColumn(
                "Volume", format="%d",
                help="Mesačná hľadanosť na Slovensku — z Ahrefs exportu (jediný zdroj skutočného objemu)",
            ),
            "KD": st.column_config.TextColumn(
                "KD", help="Keyword Difficulty 0–100 z Ahrefs — čím nižšie, tým ľahšie sa dá rankovať",
            ),
            "Trend M/M %": st.column_config.TextColumn(
                "Trend M/M %",
                help="Medzimesačný rast záujmu z Google Trends (priemer posledných 4 týždňov vs. predchádzajúce 4 týždne)",
            ),
            "GapScore": st.column_config.TextColumn(
                "GapScore",
                help="0–100 z GSC + sitemapy: 100 = žiadny článok ani ranking (najväčšia príležitosť), 0 = už rankujeme v top 10",
            ),
            "TrendScore": st.column_config.ProgressColumn(
                "TrendScore", min_value=0, max_value=100, format="%.0f",
                help="Celkové skóre = vážený súčet: hľadanosť (Ahrefs) + rast (Google Trends) + medzera v pokrytí (GSC/sitemap) + šanca uspieť (KD + intent). Váhy: Nastavenia → Scoring",
            ),
            "Klaster": st.column_config.TextColumn(
                "Klaster", help="Tematický klaster z cluster mapy (data/clusters) alebo z Ahrefs parent topic",
            ),
        },
        on_select="rerun",
        selection_mode="multi-row",
        key=f"{key_prefix}_df",
    )

    selected_indices = selection.selection.rows if hasattr(selection, "selection") else []
    selected_ids = [rows[i]["ID"] for i in selected_indices]

    return selected_ids
