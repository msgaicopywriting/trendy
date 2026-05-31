"""Pipeline — Kanban view stavov tém naprieč portálmi."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))

import streamlit as st
from trendy.db import get_db, Candidate, Portal
from trendy.config import PORTALS
from components.filters import STATUS_LABELS, TAG_LABELS, portal_selector
from components.status_actions import _single_action

st.set_page_config(page_title="Trendy — Pipeline", layout="wide")
st.title("📌 Pipeline — Kanban")

portal_key = portal_selector(key="kanban_portal")
db = get_db()

try:
    portal = db.query(Portal).filter_by(key=portal_key).first()
    if not portal:
        st.error(f"Portál '{portal_key}' nie je v DB.")
        st.stop()

    # Kanban columns config
    COLUMNS = [
        ("new",         "🆕 New",        "#E8F4FD"),
        ("seen",        "👁️ Seen",        "#FFF8E1"),
        ("accepted",    "✅ Accepted",    "#E8F5E9"),
        ("in_progress", "🚧 In progress", "#FFF3E0"),
        ("published",   "🟢 Published",   "#E0F2F1"),
    ]

    SIDE_COLUMNS = [
        ("rejected", "❌ Rejected", "#FFEBEE"),
        ("snoozed",  "💤 Snoozed",  "#F3E5F5"),
    ]

    def _fetch_status(status: str) -> list[Candidate]:
        return (
            db.query(Candidate)
            .filter_by(portal_id=portal.id, status=status)
            .order_by(Candidate.trend_score.desc())
            .limit(20)
            .all()
        )

    def _render_card(c: Candidate, compact: bool = False):
        tag = TAG_LABELS.get(c.tag or "", "")
        cooldown = "🔁 " if c.is_returned_from_cooldown else ""
        score = f"{c.trend_score:.0f}" if c.trend_score else "—"
        with st.container(border=True):
            st.markdown(f"**{cooldown}{c.keyword}**")
            st.caption(f"{tag} · Score: {score} · Vol: {c.volume or 0:,}")
            if not compact:
                st.caption(f"Klaster: {c.cluster or '—'}")
            if st.button("Otvoriť", key=f"open_{c.id}", use_container_width=True):
                st.query_params["id"] = str(c.id)
                st.switch_page("pages/2_Kandidat.py")

    # Main kanban board
    st.subheader("Aktívne stavy")
    cols = st.columns(len(COLUMNS))
    for col, (status, label, bg) in zip(cols, COLUMNS):
        candidates = _fetch_status(status)
        with col:
            count = db.query(Candidate).filter_by(portal_id=portal.id, status=status).count()
            st.markdown(f"**{label}** ({count})")
            if not candidates:
                st.caption("—")
            for c in candidates:
                _render_card(c, compact=True)

    st.divider()

    # Side columns: rejected + snoozed
    st.subheader("Archív")
    side_cols = st.columns(2)
    for col, (status, label, bg) in zip(side_cols, SIDE_COLUMNS):
        candidates = _fetch_status(status)
        with col:
            count = db.query(Candidate).filter_by(portal_id=portal.id, status=status).count()
            st.markdown(f"**{label}** ({count})")
            with st.expander("Zobraziť"):
                if not candidates:
                    st.caption("Žiadne.")
                for c in candidates:
                    tag = TAG_LABELS.get(c.tag or "", "")
                    cooldown = "🔁 " if c.is_returned_from_cooldown else ""
                    extra = ""
                    if c.snoozed_until:
                        extra = f" · do {c.snoozed_until}"
                    st.markdown(f"- **{cooldown}{c.keyword}** {tag}{extra}")
                    c2a, c2b = st.columns(2)
                    with c2a:
                        if st.button("↩️ Reset", key=f"reset_{c.id}"):
                            _single_action(c, "seen", reason="reset z kanbanu")
                    with c2b:
                        if st.button("🔎", key=f"view_{c.id}"):
                            st.query_params["id"] = str(c.id)
                            st.switch_page("pages/2_Kandidat.py")

finally:
    db.close()
