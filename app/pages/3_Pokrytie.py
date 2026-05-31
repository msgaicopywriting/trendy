"""Pokrytie & Gap analysis — bublinová mapa + topické biele miesta."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))

import streamlit as st
import plotly.express as px
import pandas as pd
from trendy.db import get_db, Portal, PublishedArticle, GscQuery, Candidate
from trendy.config import PORTALS
from components.filters import portal_selector

st.set_page_config(page_title="Trendy — Pokrytie", layout="wide")
from components.branding import apply_branding, render_header
apply_branding()
render_header("Pokrytie")
st.title("🗺️ Pokrytie & Gap analysis")

portal_key = portal_selector(key="pokrytie_sel")
db = get_db()

try:
    portal = db.query(Portal).filter_by(key=portal_key).first()
    if not portal:
        st.error(f"Portál '{portal_key}' nie je v DB.")
        st.stop()

    cfg = PORTALS[portal_key]

    tab1, tab2 = st.tabs(["📊 Pokrytie klastrov", "⬜ Biele miesta"])

    with tab1:
        # Cluster coverage — how many candidates per cluster and their avg TrendScore
        rows_q = (
            db.query(Candidate.cluster, Candidate.trend_score, Candidate.status,
                     Candidate.volume, Candidate.gap_score)
            .filter_by(portal_id=portal.id)
            .filter(Candidate.cluster.isnot(None))
            .all()
        )

        if not rows_q:
            st.info("Žiadni kandidáti s klastrom. Spusti pipeline a pridaj cluster master súbor.")
        else:
            df = pd.DataFrame(rows_q, columns=["cluster", "trend_score", "status", "volume", "gap_score"])
            cluster_summary = (
                df.groupby("cluster")
                .agg(
                    count=("cluster", "size"),
                    avg_score=("trend_score", "mean"),
                    total_volume=("volume", "sum"),
                    avg_gap=("gap_score", "mean"),
                )
                .reset_index()
                .sort_values("avg_score", ascending=False)
            )

            st.subheader("Prehľad klastrov")
            st.dataframe(
                cluster_summary.rename(columns={
                    "cluster": "Klaster", "count": "Kandidáti",
                    "avg_score": "Avg TrendScore", "total_volume": "Celkový volume",
                    "avg_gap": "Avg GapScore",
                }).round(1),
                use_container_width=True,
                hide_index=True,
            )

            # Bubble chart: cluster vs avg_score, size = total_volume
            if len(cluster_summary) > 1:
                fig = px.scatter(
                    cluster_summary,
                    x="avg_score",
                    y="avg_gap",
                    size="total_volume",
                    color="cluster",
                    hover_name="cluster",
                    text="cluster",
                    labels={"avg_score": "Avg TrendScore", "avg_gap": "Avg GapScore"},
                    title="Klastre: príležitosť vs pokrytie",
                )
                fig.update_traces(textposition="top center")
                st.plotly_chart(fig, use_container_width=True)

    with tab2:
        # White spots: clusters with gap_score=100 (no coverage)
        gap_rows = (
            db.query(Candidate.cluster, Candidate.keyword, Candidate.volume, Candidate.trend_score)
            .filter_by(portal_id=portal.id)
            .filter(Candidate.gap_score >= 100)
            .filter(Candidate.status.in_(["new", "seen"]))
            .filter(Candidate.cluster.isnot(None))
            .order_by(Candidate.trend_score.desc())
            .all()
        )

        if not gap_rows:
            st.info("Žiadne biele miesta s kompletnou gap príležitosťou.")
        else:
            df_gap = pd.DataFrame(gap_rows, columns=["Klaster", "Kľúčovka", "Volume", "TrendScore"])
            st.subheader(f"⬜ Témy bez pokrytia ({len(df_gap)})")
            st.dataframe(df_gap, use_container_width=True, hide_index=True)

        # Published articles summary
        st.divider()
        article_count = db.query(PublishedArticle).filter_by(portal_id=portal.id).count()
        st.metric("Publikovaných článkov v sitemap", article_count)

        if article_count > 0:
            articles = (
                db.query(PublishedArticle)
                .filter_by(portal_id=portal.id)
                .order_by(PublishedArticle.last_seen.desc())
                .limit(20)
                .all()
            )
            with st.expander("Posledné sitemapované články"):
                for a in articles:
                    st.markdown(f"- [{a.title or a.url}]({a.url})")

finally:
    db.close()
