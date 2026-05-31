"""Nastavenia — scoring váhy, thresholdy, cooldowny, seed kľúčovky."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))

import streamlit as st
from trendy.config import settings, PORTALS
from trendy.db import get_db, PipelineRun

st.set_page_config(page_title="Trendy — Nastavenia", layout="wide")
st.title("⚙️ Nastavenia")

db = get_db()

try:
    tab1, tab2, tab3, tab4 = st.tabs(["📊 Scoring", "🗂️ Portály", "🔄 Scheduler", "ℹ️ Systém"])

    # ─── Scoring ────────────────────────────────────────────────────────────
    with tab1:
        st.subheader("Váhy TrendScore")
        st.caption("Suma váh musí byť 1.0. Zmeny sa aplikujú po ďalšom spustení pipeline.")

        col1, col2 = st.columns(2)
        with col1:
            w_volume = st.slider("w1 — Volume", 0.0, 1.0, settings.weight_volume, 0.05, key="w_vol")
            w_growth = st.slider("w2 — Growth (trend)", 0.0, 1.0, settings.weight_growth, 0.05, key="w_grow")
        with col2:
            w_gap = st.slider("w3 — Gap", 0.0, 1.0, settings.weight_gap, 0.05, key="w_gap")
            w_opportunity = st.slider("w4 — Opportunity (KD+intent)", 0.0, 1.0, settings.weight_opportunity, 0.05, key="w_opp")

        total = w_volume + w_growth + w_gap + w_opportunity
        if abs(total - 1.0) > 0.01:
            st.warning(f"⚠️ Suma váh = {total:.2f} (musí byť 1.0)")
        else:
            st.success(f"✅ Suma váh = {total:.2f}")

        st.divider()
        st.subheader("Volume thresholdy (min. hľadaní/mes)")
        c1, c2, c3 = st.columns(3)
        with c1:
            th_ml = st.number_input("msg-life.sk", min_value=1, value=settings.threshold_msg_life, key="th_ml")
        with c2:
            th_mt = st.number_input("msgtester.sk", min_value=1, value=settings.threshold_msgtester, key="th_mt")
        with c3:
            th_mp = st.number_input("msgprogramator.sk", min_value=1, value=settings.threshold_msgprogramator, key="th_mp")

        st.divider()
        st.subheader("Lifecycle cooldowny")
        cc1, cc2, cc3 = st.columns(3)
        with cc1:
            cd_rejected = st.number_input("Zamietnuté (dni)", min_value=1, value=settings.cooldown_rejected_days)
        with cc2:
            cd_published = st.number_input("Publikované (dni)", min_value=1, value=settings.cooldown_published_days)
        with cc3:
            cd_snooze = st.number_input("Default snooze (dni)", min_value=1, value=settings.snooze_default_days)

        st.info("💡 Zmeny nastavení sa ukladajú do `.env` — reštartuj aplikáciu pre ich načítanie.")

        if st.button("💾 Uložiť do .env", type="primary"):
            env_path = Path(__file__).resolve().parents[2] / ".env"
            _update_env(env_path, {
                "WEIGHT_VOLUME": str(w_volume),
                "WEIGHT_GROWTH": str(w_growth),
                "WEIGHT_GAP": str(w_gap),
                "WEIGHT_OPPORTUNITY": str(w_opportunity),
                "THRESHOLD_MSG_LIFE": str(th_ml),
                "THRESHOLD_MSGTESTER": str(th_mt),
                "THRESHOLD_MSGPROGRAMATOR": str(th_mp),
                "COOLDOWN_REJECTED_DAYS": str(cd_rejected),
                "COOLDOWN_PUBLISHED_DAYS": str(cd_published),
                "SNOOZE_DEFAULT_DAYS": str(cd_snooze),
            })
            st.success("Uložené. Reštartuj Streamlit (`Ctrl+C` → `uv run streamlit run app/Home.py`).")

    # ─── Portály ────────────────────────────────────────────────────────────
    with tab2:
        st.subheader("Seed kľúčovky per portál")
        st.caption("Používajú sa ako základ pre Ahrefs Keywords Explorer export a pytrends rising queries.")
        for pkey, pcfg in PORTALS.items():
            with st.expander(f"{pcfg.name}"):
                st.markdown("**Seed kľúčovky:**")
                for kw in pcfg.seed_keywords:
                    st.markdown(f"- `{kw}`")
                st.markdown("**Konkurenti (Ahrefs Site Explorer):**")
                for d in pcfg.competitor_domains:
                    st.markdown(f"- `{d}`")
                st.caption("Seed kľúčovky a konkurenti sa editujú v `src/trendy/config.py`.")

        st.divider()
        st.subheader("Priečinky inboxov")
        st.code(f"""Ahrefs inbox:  {settings.ahrefs_inbox_dir}
GSC inbox:     {settings.gsc_inbox_dir}
Clusters:      {settings.clusters_dir}
Databáza:      {settings.database_url}""")

    # ─── Scheduler ────────────────────────────────────────────────────────
    with tab3:
        st.subheader("Plánovaný beh (APScheduler)")
        st.markdown(f"""
**Aktuálny rozvrh:** každého **{settings.scheduler_cron_day}.** v mesiaci o **{settings.scheduler_cron_hour:02d}:{settings.scheduler_cron_minute:02d}**

Pipeline sa spúšťa automaticky ak je Streamlit app spustená. Pre produkčné nasadenie odporúčame nastaviť externý cron alebo GitHub Actions.
""")

        sc1, sc2, sc3 = st.columns(3)
        with sc1:
            sched_day = st.number_input("Deň v mesiaci (1–28)", min_value=1, max_value=28,
                                         value=settings.scheduler_cron_day,
                                         help="Odporúčame max. 28 — platí aj pre február.")
        with sc2:
            sched_hour = st.number_input("Hodina (0–23)", min_value=0, max_value=23,
                                          value=settings.scheduler_cron_hour)
        with sc3:
            sched_minute = st.number_input("Minúta (0–59)", min_value=0, max_value=59,
                                            value=settings.scheduler_cron_minute)

        if st.button("💾 Uložiť rozvrh do .env"):
            env_path = Path(__file__).resolve().parents[2] / ".env"
            _update_env(env_path, {
                "SCHEDULER_CRON_DAY": str(sched_day),
                "SCHEDULER_CRON_HOUR": str(sched_hour),
                "SCHEDULER_CRON_MINUTE": str(sched_minute),
            })
            st.success("Uložené. Reštartuj Streamlit pre aktiváciu nového rozvrhu.")

        st.divider()
        st.subheader("História behov")
        from trendy.db import PipelineRun, Portal as DbPortal
        runs = (
            db.query(PipelineRun, DbPortal.key)
            .join(DbPortal)
            .order_by(PipelineRun.started_at.desc())
            .limit(20)
            .all()
        )
        if runs:
            import pandas as pd
            rows = [{
                "Portál": pkey,
                "Začiatok": r.started_at.strftime("%d.%m.%Y %H:%M"),
                "Status": r.status,
                "Nových": r.candidates_found,
                "Suppressed": r.candidates_suppressed,
            } for r, pkey in runs]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info("Pipeline ešte nebehal.")

        if st.button("▶️ Spustiť pipeline pre všetky portály"):
            with st.spinner("Beží..."):
                from trendy.pipeline import run_pipeline
                for pkey in PORTALS:
                    try:
                        s = run_pipeline(pkey)
                        st.success(f"{pkey}: {s['candidates_found']} nových, {s['candidates_suppressed']} suppressed")
                    except Exception as e:
                        st.error(f"{pkey}: {e}")

    # ─── Systém ─────────────────────────────────────────────────────────────
    with tab4:
        st.subheader("Systémové informácie")
        import platform
        st.json({
            "Python": platform.python_version(),
            "Database": settings.database_url,
            "Pytrends geo": settings.pytrends_geo,
            "Pytrends lang": settings.pytrends_language,
            "GSC API": "disabled (manual CSV import)",
            "Ahrefs MCP": "stub (CSV fallback)",
        })

        st.divider()
        st.subheader("Future: GSC API")
        st.code("""# Odkomentuj v .env keď bude GSC API dostupné:
# GSC_SERVICE_ACCOUNT_JSON=secrets/gsc-service-account.json
# GSC_MSG_LIFE_PROPERTY=sc-domain:msg-life.sk""")

        st.subheader("Future: Ahrefs MCP")
        st.code("""# Keď bude MCP connector aktívny:
# AHREFS_MCP_ENABLED=true
# Zmeň AhrefsMCPStub._connected = True v sources/ahrefs.py""")

finally:
    db.close()


def _update_env(env_path: Path, updates: dict) -> None:
    """Update or append key=value pairs in .env file."""
    existing = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                existing[k.strip()] = v.strip()

    existing.update(updates)
    lines = [f"{k}={v}" for k, v in existing.items()]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
