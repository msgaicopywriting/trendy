"""„Ďalší krok" banner — z reálneho stavu DB odvodí, v ktorej fáze workflowu
sa user nachádza, a vždy ukáže jednu jasnú akciu s preklikom. Rieši problém
„pipeline dobehol a neviem, čo mám robiť ďalej"."""
from __future__ import annotations

import streamlit as st
from sqlalchemy.orm import Session

from trendy.config import PORTALS
from trendy.db import Candidate, PipelineRun, Portal


def _stats(db: Session, portal: Portal) -> dict:
    active = (
        db.query(Candidate)
        .filter(Candidate.portal_id == portal.id, Candidate.status.in_(["new", "seen"]))
        .count()
    )
    accepted = db.query(Candidate).filter_by(portal_id=portal.id, status="accepted").count()
    needs_vol = (
        db.query(Candidate)
        .filter(
            Candidate.portal_id == portal.id,
            Candidate.needs_volume.is_(True),
            Candidate.status.in_(["new", "seen"]),
        )
        .count()
    )
    has_run = (
        db.query(PipelineRun).filter_by(portal_id=portal.id, status="completed").first()
        is not None
    )
    return {"active": active, "accepted": accepted, "needs_vol": needs_vol, "has_run": has_run}


def render_next_action(db: Session) -> None:
    """One primary next step, chosen by workflow stage:
    1. no completed run  → spusti pipeline
    2. new/seen topics   → posúď ich (link na Portál)
    3. accepted topics   → priprav briefy (link na Kanban)
    4. inak              → hotovo, počkaj na ďalší beh / nahraj exporty
    """
    portals = {p.key: p for p in db.query(Portal).all() if p.key in PORTALS}
    if not portals:
        return
    stats = {k: _stats(db, p) for k, p in portals.items()}

    total_active = sum(s["active"] for s in stats.values())
    total_accepted = sum(s["accepted"] for s in stats.values())
    total_needs_vol = sum(s["needs_vol"] for s in stats.values())
    any_run = any(s["has_run"] for s in stats.values())

    st.markdown("#### 🎯 Ďalší krok")

    if not any_run:
        st.info(
            "**Spusti pipeline** (tlačidlo nižšie na tejto stránke) — stiahne témy "
            "zo všetkých zdrojov a naplní zásobník kandidátov."
        )
    elif total_active > 0:
        per_portal = " · ".join(
            f"{PORTALS[k].name}: **{s['active']}**" for k, s in stats.items() if s["active"]
        )
        st.success(
            f"**{total_active} tém čaká na tvoje posúdenie** ({per_portal}). "
            f"Otvor zoznam, označ témy v tabuľke a prijmi ✅ / zamietni ❌ ich."
        )
        st.page_link("pages/1_Portál.py", label=f"👉 Posúdiť témy ({total_active})", icon="📋")
    elif total_accepted > 0:
        st.success(
            f"Všetky témy sú posúdené ✅ — **{total_accepted} prijatých** čaká na "
            f"spracovanie (brief → článok)."
        )
        st.page_link("pages/4_Pipeline.py", label=f"👉 Otvoriť Kanban ({total_accepted})", icon="📌")
    else:
        st.info(
            "Všetko je spracované ✅ — nové témy pribudnú pri ďalšom behu pipeline "
            "(mesačný rytmus), alebo nahraj čerstvé Ahrefs/GSC exporty a spusti beh hneď."
        )

    if total_needs_vol > 0:
        st.caption(
            f"💡 {total_needs_vol} tém má zatiaľ neoverenú hľadanosť — kľúčovky na Ahrefs "
            f"export nájdeš v **Nastavenia → Portály** (pripravený blok na kopírovanie)."
        )
