"""Trendy — hlavný dashboard."""
import sys
from pathlib import Path

# Ensure src is on path when running via `streamlit run app/Home.py`
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import streamlit as st
from trendy.config import PORTALS, settings
from trendy.db import get_db, init_db, get_engine

st.set_page_config(
    page_title="Trendy — msg portály",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- Init DB on first run ---
@st.cache_resource
def _init():
    engine = get_engine()
    init_db(engine)
    _seed_portals(engine)
    return engine

def _seed_portals(engine):
    from sqlalchemy.orm import Session
    from trendy.db import Portal
    with Session(engine) as s:
        for key, cfg in PORTALS.items():
            if not s.query(Portal).filter_by(key=key).first():
                s.add(Portal(key=key, name=cfg.name, url=cfg.url))
        s.commit()

_init()

# --- Sidebar ---
st.sidebar.title("🔍 Trendy")
st.sidebar.caption("Trendové témy pre msg portály")
st.sidebar.divider()
st.sidebar.page_link("Home.py", label="🏠 Dashboard", icon="🏠")
st.sidebar.page_link("pages/1_Portal.py", label="Portál — kandidáti", icon="📋")
st.sidebar.page_link("pages/2_Kandidat.py", label="Detail kandidáta", icon="🔎")
st.sidebar.page_link("pages/3_Pokrytie.py", label="Pokrytie & Gap", icon="🗺️")
st.sidebar.page_link("pages/4_Pipeline.py", label="Pipeline (Kanban)", icon="📌")
st.sidebar.page_link("pages/5_Nastavenia.py", label="Nastavenia", icon="⚙️")

# --- Main ---
st.title("🔍 Trendy — prehľad")
st.caption(f"Databáza: `{settings.database_url}`")

db = get_db()

try:
    from trendy.db import Candidate, PipelineRun, Portal

    col1, col2, col3 = st.columns(3)
    portal_keys = list(PORTALS.keys())

    for col, pkey in zip([col1, col2, col3], portal_keys):
        cfg = PORTALS[pkey]
        portal_row = db.query(Portal).filter_by(key=pkey).first()

        with col:
            st.markdown(f"### {cfg.name}")

            if portal_row:
                total = db.query(Candidate).filter_by(portal_id=portal_row.id).count()
                new_count = (
                    db.query(Candidate)
                    .filter_by(portal_id=portal_row.id)
                    .filter(Candidate.status.in_(["new", "seen"]))
                    .count()
                )
                rising = (
                    db.query(Candidate)
                    .filter_by(portal_id=portal_row.id, tag="rising")
                    .filter(Candidate.status.in_(["new", "seen"]))
                    .order_by(Candidate.trend_score.desc())
                    .limit(3)
                    .all()
                )

                m1, m2 = st.columns(2)
                m1.metric("Aktívnych tém", new_count)
                m2.metric("Spolu v DB", total)

                if rising:
                    st.markdown("**🚀 Top Rising:**")
                    for c in rising:
                        st.markdown(f"- {c.keyword} *(score: {c.trend_score:.0f})*")
                else:
                    st.info("Žiadne Rising témy — spusti pipeline.")

                last_run = (
                    db.query(PipelineRun)
                    .filter_by(portal_id=portal_row.id)
                    .order_by(PipelineRun.started_at.desc())
                    .first()
                )
                if last_run:
                    st.caption(f"Posledný beh: {last_run.started_at.strftime('%d.%m.%Y %H:%M')}")
                else:
                    st.caption("Pipeline ešte nebehal")
            else:
                st.warning("Portál nie je inicializovaný v DB")

    st.divider()
    st.subheader("Manuálny refresh")
    st.info(
        "📥 **Workflow pre mesačný refresh:**\n"
        "1. Exportuj z Ahrefs Keywords Explorer a ulož CSV do `data/ahrefs_inbox/<portal>/`\n"
        "2. Exportuj z GSC (Performance → Queries → 90 dní) a ulož CSV do `data/gsc_inbox/<portal>/`\n"
        "3. Klikni **Spustiť pipeline** nižšie"
    )

    with st.expander("📖 Mesačný workflow — postup krok za krokom"):
        st.markdown(f"""
### Krok 1 — Ahrefs export

1. Otvor **Ahrefs → Keywords Explorer**
2. Do vyhľadávacieho poľa zadaj **témy relevantné pre portál** — napr. pre msg-life.sk sú to oblasti ako `HR`, `životopis`, `employer branding`. Kompletný zoznam nájdeš v **Nastavenia → Portály**.
   > Nemusíš zadávať všetky naraz — môžeš robiť viac exportov po menších tematických skupinách a všetky CSV súbory hodiť do rovnakého priečinka.
3. Ľavé menu → **Matching terms**
4. Filtre: **Country = Slovakia**, Volume ≥ 20
5. **Export → CSV**
6. Súbor ulož do príslušného priečinka:

| Portál | Priečinok |
|---|---|
| msg-life.sk | `{settings.ahrefs_inbox_dir / "msg-life" / "YYYY-MM-DD_keywords.csv"}` |
| msgtester.sk | `{settings.ahrefs_inbox_dir / "msgtester" / "YYYY-MM-DD_keywords.csv"}` |
| msgprogramator.sk | `{settings.ahrefs_inbox_dir / "msgprogramator" / "YYYY-MM-DD_keywords.csv"}` |

> 💡 Dátum v názve súboru (`YYYY-MM-DD`) je voliteľný, ale pomáha pri orientácii.

---

### Krok 2 — GSC export

1. Otvor **Google Search Console** → vyber property portálu
2. Ľavé menu → **Výkonnosť → Výsledky vyhľadávania**
3. Rozsah: **Posledných 90 dní**
4. Záložka **Vyhľadávané výrazy** → **⬇️ Exportovať → CSV**
5. Ulož do priečinka:

| Portál | Priečinok |
|---|---|
| msg-life.sk | `{settings.gsc_inbox_dir / "msg-life" / "YYYY-MM-DD_queries.csv"}` |
| msgtester.sk | `{settings.gsc_inbox_dir / "msgtester" / "YYYY-MM-DD_queries.csv"}` |
| msgprogramator.sk | `{settings.gsc_inbox_dir / "msgprogramator" / "YYYY-MM-DD_queries.csv"}` |

---

### Krok 3 — Cluster mapa *(voliteľné)*

Ak máš autoritatívne XLSX súbory s klastrovými mapami, ulož ich do:

```
{settings.clusters_dir / "msg-life" / "clusters.xlsx"}
{settings.clusters_dir / "msgtester" / "clusters.xlsx"}
{settings.clusters_dir / "msgprogramator" / "clusters.xlsx"}
```

Pipeline sa spustí aj bez nich — témy budú bez priradenia ku klastru.

---

### Krok 4 — Spustiť pipeline

Po uložení súborov klikni **▶️ Spustiť pipeline** nižšie.
Nové a aktualizované témy nájdeš v **Portál → kandidáti** (ľavé menu).

> 🔁 Tento postup opakuj každý mesiac — pipeline porovná nové dáta s predchádzajúcimi behmi a automaticky aplikuje cooldown na témy, ktoré už boli spracované.
""")


    selected_portal = st.selectbox(
        "Vyber portál pre refresh:",
        options=portal_keys,
        format_func=lambda k: PORTALS[k].name,
    )

    if st.button("▶️ Spustiť pipeline", type="primary"):
        st.warning("⚙️ Pipeline nie je ešte implementovaný (Phase 2). Skontroluj späť čoskoro.")

finally:
    db.close()
