"""Portál — tabuľka kandidátov s filtrami a bulk akciami."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))

import streamlit as st
from trendy.db import get_db, Candidate, Portal, SuppressedCandidate, PipelineRun
from trendy.config import PORTALS
from components.filters import portal_selector, candidate_filters, TAG_LABELS, STATUS_LABELS
from components.candidate_table import render_candidate_table
from components.status_actions import render_bulk_actions

st.set_page_config(page_title="Trendy — Portál", layout="wide")
st.title("📋 Portál — kandidáti")

# Sidebar
portal_key = portal_selector(key="portal_page_sel")
filters = candidate_filters(key_prefix="portal")

db = get_db()
try:
    portal = db.query(Portal).filter_by(key=portal_key).first()
    if not portal:
        st.error(f"Portál '{portal_key}' nie je v DB.")
        st.stop()

    cfg = PORTALS[portal_key]

    # ─── Query ─────────────────────────────────────────────────────────────
    # Main list = only topics with verified search volume. Unverified discovery
    # topics live in the "Témy na overenie hľadanosti" bucket below.
    q = db.query(Candidate).filter_by(portal_id=portal.id).filter(
        Candidate.needs_volume.isnot(True)
    )

    if filters["tags"]:
        q = q.filter(Candidate.tag.in_(filters["tags"]))

    if not filters["show_cooldown"]:
        q = q.filter(Candidate.status.in_(filters["statuses"]))
    else:
        # Show everything including cooldown items
        pass

    if filters["min_score"] > 0:
        q = q.filter(Candidate.trend_score >= filters["min_score"])

    candidates = q.order_by(Candidate.trend_score.desc()).all()

    # ─── Header metrics ────────────────────────────────────────────────────
    total = db.query(Candidate).filter_by(portal_id=portal.id).count()
    new_seen = db.query(Candidate).filter_by(portal_id=portal.id).filter(
        Candidate.status.in_(["new", "seen"])
    ).count()

    m1, m2, m3 = st.columns(3)
    m1.metric("Zobrazených", len(candidates))
    m2.metric("Aktívnych (new+seen)", new_seen)
    m3.metric("Spolu v DB", total)

    st.divider()

    # ─── Candidate table ────────────────────────────────────────────────────
    selected_ids = render_candidate_table(candidates, key_prefix="portal_tbl")

    # ─── Bulk actions ──────────────────────────────────────────────────────
    if selected_ids:
        st.divider()
        render_bulk_actions(selected_ids, key_prefix="portal_bulk")

    # ─── Témy na overenie hľadanosti (needs_volume) ──────────────────────────
    st.divider()
    st.subheader("🔍 Témy na overenie hľadanosti")
    st.caption(
        "Discovery témy bez overenej hľadanosti (volume). Pri ďalšom Ahrefs exporte "
        "sa spárujú s reálnym volume a presunú medzi plnohodnotných kandidátov."
    )
    needs_vol = (
        db.query(Candidate)
        .filter_by(portal_id=portal.id)
        .filter(Candidate.needs_volume.is_(True))
        .filter(Candidate.status.in_(["new", "seen"]))
        .order_by(Candidate.growth_score.desc())
        .all()
    )
    if needs_vol:
        import pandas as pd
        nv_rows = [{
            "Kľúčovka": c.keyword,
            "Zdroj": c.source,
            "Tag": TAG_LABELS.get(c.tag or "", c.tag or "—"),
            "Trend M/M %": f"{c.trend_mom_pct:+.0f}%" if c.trend_mom_pct else "—",
            "GapScore": f"{c.gap_score:.0f}" if c.gap_score is not None else "—",
            "Klaster": c.cluster or "—",
            "Status": STATUS_LABELS.get(c.status, c.status),
        } for c in needs_vol]
        st.dataframe(pd.DataFrame(nv_rows), use_container_width=True, hide_index=True)
        st.caption(f"{len(needs_vol)} tém čaká na overenie hľadanosti.")
    else:
        st.info("Žiadne témy na overenie — všetci aktívni kandidáti majú overenú hľadanosť.")

    # ─── Export ────────────────────────────────────────────────────────────
    st.divider()
    col_exp, col_refresh = st.columns([3, 1])

    with col_exp:
        if st.button("⬇️ Export vybraných do XLSX", disabled=not selected_ids):
            import pandas as pd
            import io
            sel = [c for c in candidates if c.id in selected_ids]
            rows = [{
                "keyword": c.keyword, "volume": c.volume, "kd": c.kd,
                "tag": c.tag, "trend_score": c.trend_score,
                "cluster": c.cluster, "status": c.status,
            } for c in sel]
            buf = io.BytesIO()
            pd.DataFrame(rows).to_excel(buf, index=False)
            st.download_button("📥 Stiahnuť XLSX", buf.getvalue(),
                               file_name=f"trendy_{portal_key}_export.xlsx")

    with col_refresh:
        if st.button("▶️ Spustiť pipeline", type="primary"):
            with st.spinner("Pipeline beží..."):
                try:
                    from trendy.pipeline import run_pipeline
                    summary = run_pipeline(portal_key)
                    st.success(f"Hotovo — {summary['candidates_found']} nových, {summary['candidates_suppressed']} suppressed")
                    st.rerun()
                except Exception as e:
                    st.error(f"Pipeline zlyhala: {e}")

    # ─── Suppressed panel ──────────────────────────────────────────────────
    if filters["show_cooldown"]:
        st.divider()
        st.subheader("🔕 Suppressed týmto behom")
        last_run = (
            db.query(PipelineRun)
            .filter_by(portal_id=portal.id, status="completed")
            .order_by(PipelineRun.started_at.desc())
            .first()
        )
        if last_run:
            from trendy.lifecycle import get_suppressed_for_run
            suppressed = get_suppressed_for_run(last_run.id, db)
            if suppressed:
                import pandas as pd
                st.dataframe(pd.DataFrame(suppressed), use_container_width=True, hide_index=True)
            else:
                st.info("Žiadne suppressed témy v poslednom behu.")
        else:
            st.info("Pipeline ešte nebehal — žiadne suppressed dáta.")

finally:
    db.close()
