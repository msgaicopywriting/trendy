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
    return {
        "active": active,
        "verified_active": active - needs_vol,  # topics with real (Ahrefs) volume
        "accepted": accepted,
        "needs_vol": needs_vol,
        "has_run": has_run,
    }


def render_next_action(db: Session) -> None:
    """One primary next step, chosen by workflow stage:
    1. no completed run          → spusti pipeline
    2. only unverified topics    → nahraj Ahrefs/GSC exporty (triáž naslepo = riziko
                                   zamietnutia hodnotnej témy do 90-dňového cooldownu)
    3. volume-verified topics    → posúď ich (link na Portál)
    4. accepted topics           → priprav briefy (link na Kanban)
    5. inak                      → hotovo, počkaj na ďalší beh / nahraj exporty
    """
    portals = {p.key: p for p in db.query(Portal).all() if p.key in PORTALS}
    if not portals:
        return
    stats = {k: _stats(db, p) for k, p in portals.items()}

    total_active = sum(s["active"] for s in stats.values())
    total_verified = sum(s["verified_active"] for s in stats.values())
    total_accepted = sum(s["accepted"] for s in stats.values())
    total_needs_vol = sum(s["needs_vol"] for s in stats.values())
    any_run = any(s["has_run"] for s in stats.values())

    st.markdown("#### 🎯 Ďalší krok")

    if not any_run:
        st.info(
            "**Spusti pipeline** (tlačidlo nižšie na tejto stránke) — stiahne témy "
            "zo všetkých zdrojov a naplní zásobník kandidátov."
        )
    elif total_active > 0 and total_verified == 0:
        # Everything in the pool is a discovery idea with no verified search volume.
        # Reviewing now would mean judging blind — and a reject puts the topic into a
        # 90-day cooldown, possibly killing one that Ahrefs would rank highest.
        st.warning(
            f"Pipeline našiel **{total_active} tém**, ale zatiaľ sú to len nápady "
            f"**bez overenej hľadanosti** — chýbajú Ahrefs/GSC exporty. "
            f"**Nahraj exporty nižšie** a spusti pipeline znova: témam sa doplní reálny "
            f"objem hľadanosti, prepočíta sa skóre a až potom má zmysel ich posudzovať."
        )
        st.caption(
            "Kľúčovky pre Ahrefs Keywords Explorer máš pripravené na kopírovanie "
            "v **Nastavenia → Portály**. (Posúdiť témy sa dajú aj bez volume — "
            "Portál → Témy na overenie hľadanosti — ale rob to len pri témach, "
            "pri ktorých si si istá aj bez čísel.)"
        )
    elif total_verified > 0:
        per_portal = " · ".join(
            f"{PORTALS[k].name}: **{s['verified_active']}**"
            for k, s in stats.items() if s["verified_active"]
        )
        extra = f" (+{total_needs_vol} discovery tém bez overenej hľadanosti)" if total_needs_vol else ""
        st.success(
            f"**{total_verified} tém s overenou hľadanosťou čaká na posúdenie** "
            f"({per_portal}){extra}. Otvor zoznam, označ témy a prijmi ✅ / zamietni ❌ ich."
        )
        st.page_link("pages/1_Portál.py", label=f"👉 Posúdiť témy ({total_verified})", icon="📋")
    elif total_accepted > 0:
        st.success(
            f"Všetky témy sú posúdené ✅ — **{total_accepted} prijatých** čaká na "
            f"spracovanie (brief → článok)."
        )
        st.page_link("pages/4_Pipeline.py", label=f"👉 Otvoriť Kanban ({total_accepted})", icon="📌")
    else:
        from trendy.scheduler import next_scheduled_run_date
        st.info(
            f"Všetko je spracované ✅ — ďalší mesačný beh je odporúčaný "
            f"**{next_scheduled_run_date().strftime('%d.%m.%Y')}**, alebo nahraj "
            f"čerstvé Ahrefs/GSC exporty a spusti beh hneď."
        )
