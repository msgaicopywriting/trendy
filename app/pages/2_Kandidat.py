"""Kandidát — detail témy: trend graf, SERP coverage, akcie, brief export."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))

import json
import streamlit as st
import plotly.graph_objects as go
from trendy.db import get_db, Candidate, Portal, CandidateStatusHistory, PublishedArticle
from trendy.config import PORTALS
from components.filters import TAG_LABELS, STATUS_LABELS
from components.status_actions import render_single_actions

st.set_page_config(page_title="Trendy — Kandidát", layout="wide")
st.title("🔎 Detail kandidáta")

# Candidate selection
candidate_id = st.query_params.get("id")

db = get_db()
try:
    if not candidate_id:
        # Show selector
        st.info("Vyber kandidáta z tabuľky Portál, alebo zadaj ID:")
        cid_input = st.number_input("Candidate ID", min_value=1, step=1)
        if st.button("Zobraziť"):
            st.query_params["id"] = str(int(cid_input))
            st.rerun()
        st.stop()

    c = db.query(Candidate).get(int(candidate_id))
    if not c:
        st.error(f"Kandidát ID {candidate_id} neexistuje.")
        st.stop()

    portal = db.query(Portal).get(c.portal_id)
    cfg = PORTALS.get(portal.key, None)

    # ─── Header ────────────────────────────────────────────────────────────
    tag_label = TAG_LABELS.get(c.tag or "", c.tag or "—")
    st.subheader(f"{tag_label}  {c.keyword}")
    if c.is_returned_from_cooldown:
        st.info("🔁 Táto téma sa vrátila po cooldown. Skontroluj predchádzajúce rozhodnutie nižšie.")

    # ─── Score breakdown ────────────────────────────────────────────────────
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("TrendScore", f"{c.trend_score or 0:.0f}/100")
    col2.metric("Volume", f"{c.volume or 0:,}")
    col3.metric("Growth M/M", f"{c.trend_mom_pct:+.0f}%" if c.trend_mom_pct else "—")
    col4.metric("Gap Score", f"{c.gap_score or 0:.0f}/100")
    col5.metric("KD", c.kd if c.kd is not None else "—")

    st.caption(f"Portál: **{portal.name}** | Klaster: **{c.cluster or '—'}** | Zdroj: `{c.source}` | Intent: {c.intent or '—'}")

    st.divider()

    # ─── Trend graph ────────────────────────────────────────────────────────
    left, right = st.columns([2, 1])

    with left:
        st.subheader("📈 Google Trends (12 mesiacov)")
        if c.trend_data_json:
            try:
                timeline = json.loads(c.trend_data_json)
                if timeline:
                    dates = list(timeline.keys())
                    values = list(timeline.values())
                    fig = go.Figure()
                    fig.add_trace(go.Scatter(
                        x=dates, y=values, mode="lines+markers",
                        line=dict(color=cfg.color if cfg else "#0057A8", width=2),
                        name=c.keyword,
                    ))
                    fig.update_layout(
                        margin=dict(l=0, r=0, t=10, b=0),
                        height=250,
                        xaxis_title=None,
                        yaxis_title="Záujem (relatívny)",
                        showlegend=False,
                    )
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.info("Trend dáta nie sú k dispozícii.")
            except Exception:
                st.info("Trend dáta nie sú k dispozícii.")
        else:
            st.info("Trend dáta nie sú k dispozícii — spusti pipeline pre aktualizáciu.")

    with right:
        st.subheader("🗺️ GSC pokrytie")
        if c.gsc_avg_position:
            pos = c.gsc_avg_position
            color = "green" if pos <= 10 else "orange" if pos <= 20 else "red"
            st.markdown(f"Avg. pozícia: :{color}[{pos:.1f}]")
            st.caption(f"Impressie: {c.gsc_impressions or 0:,}")
        else:
            st.info("Portál neranku je na túto query.")

        # Matching articles
        if c.matched_article_id:
            article = db.query(PublishedArticle).get(c.matched_article_id)
            if article:
                st.markdown(f"**Pokrývajúci článok:**")
                st.markdown(f"[{article.title or article.url}]({article.url})")
        else:
            # Try fuzzy match
            similar = (
                db.query(PublishedArticle)
                .filter_by(portal_id=c.portal_id)
                .filter(PublishedArticle.slug_normalized.contains(
                    c.keyword_normalized.split()[0] if c.keyword_normalized else ""
                ))
                .limit(3)
                .all()
            )
            if similar:
                st.markdown("**Podobné články (fuzzy):**")
                for a in similar:
                    st.markdown(f"- [{a.title or a.url}]({a.url})")

    st.divider()

    # ─── Status & actions ────────────────────────────────────────────────────
    st.subheader("📌 Status a akcie")
    current_status_label = STATUS_LABELS.get(c.status, c.status)
    st.markdown(f"**Aktuálny status:** {current_status_label}")

    if c.published_url:
        st.markdown(f"🔗 Publikovaný článok: [{c.published_url}]({c.published_url})")
    if c.brief_url:
        st.markdown(f"📝 Brief URL: [{c.brief_url}]({c.brief_url})")
    if c.snoozed_until:
        st.markdown(f"💤 Odložené do: **{c.snoozed_until}**")

    render_single_actions(c, key_prefix=f"cand_{c.id}")

    # ─── Status history ────────────────────────────────────────────────────
    history = (
        db.query(CandidateStatusHistory)
        .filter_by(candidate_id=c.id)
        .order_by(CandidateStatusHistory.changed_at.desc())
        .all()
    )
    if history:
        with st.expander("📋 História statusov"):
            import pandas as pd
            rows = [{
                "Kedy": h.changed_at.strftime("%d.%m.%Y %H:%M"),
                "Z": h.from_status or "—",
                "Na": h.to_status,
                "Dôvod": h.reason or "—",
            } for h in history]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.divider()

    # ─── Brief export ────────────────────────────────────────────────────────
    st.subheader("📄 Podklady pre brief")
    st.caption("Exportuje dáta vo formáte pre príslušný brief skill portálu.")
    if st.button("⬇️ Exportovať podklady (XLSX)"):
        try:
            from trendy.briefing import export_candidate_brief
            xlsx_bytes = export_candidate_brief(c.id)
            st.download_button(
                "📥 Stiahnuť XLSX",
                data=xlsx_bytes,
                file_name=f"brief_{c.keyword_normalized.replace(' ', '_')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        except ImportError:
            st.warning("Briefing modul nie je ešte implementovaný (Phase 4).")
        except Exception as e:
            st.error(f"Chyba pri exporte: {e}")

finally:
    db.close()
