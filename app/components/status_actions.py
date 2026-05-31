"""Status action buttons (accept, reject, snooze, etc.) component."""
from __future__ import annotations

from datetime import date, timedelta
import streamlit as st

from trendy.db import get_db, Candidate
from trendy.lifecycle import change_status, bulk_change_status, REJECTION_REASONS, _default_snooze_date


def render_bulk_actions(selected_ids: list[int], key_prefix: str = "bulk") -> None:
    """Render bulk action bar for selected candidates."""
    if not selected_ids:
        return

    st.markdown(f"**{len(selected_ids)} vybraných:**")
    cols = st.columns(5)

    with cols[0]:
        if st.button("✅ Prijať", key=f"{key_prefix}_accept"):
            _bulk_action(selected_ids, "accepted", "bulk accept")
            st.rerun()

    with cols[1]:
        if st.button("❌ Zamietnuť", key=f"{key_prefix}_reject_btn"):
            st.session_state[f"{key_prefix}_show_reject"] = True

    with cols[2]:
        if st.button("💤 Odložiť", key=f"{key_prefix}_snooze_btn"):
            st.session_state[f"{key_prefix}_show_snooze"] = True

    with cols[3]:
        if st.button("🚧 V práci", key=f"{key_prefix}_wip"):
            _bulk_action(selected_ids, "in_progress", "bulk in_progress")
            st.rerun()

    with cols[4]:
        if st.button("👁️ Seen", key=f"{key_prefix}_seen"):
            _bulk_action(selected_ids, "seen", "bulk seen")
            st.rerun()

    # Reject modal
    if st.session_state.get(f"{key_prefix}_show_reject"):
        with st.form(f"{key_prefix}_reject_form"):
            reason = st.selectbox("Dôvod zamietnutia:", REJECTION_REASONS)
            submitted = st.form_submit_button("Potvrdiť zamietnutie")
            if submitted:
                _bulk_action(selected_ids, "rejected", reason)
                st.session_state.pop(f"{key_prefix}_show_reject", None)
                st.rerun()

    # Snooze modal
    if st.session_state.get(f"{key_prefix}_show_snooze"):
        with st.form(f"{key_prefix}_snooze_form"):
            snooze_date = st.date_input(
                "Odložiť do:",
                value=_default_snooze_date(),
                min_value=date.today() + timedelta(days=1),
            )
            submitted = st.form_submit_button("Potvrdiť odloženie")
            if submitted:
                db = get_db()
                try:
                    bulk_change_status(selected_ids, "snoozed", db, snoozed_until=snooze_date)
                finally:
                    db.close()
                st.session_state.pop(f"{key_prefix}_show_snooze", None)
                st.rerun()


def render_single_actions(candidate: Candidate, key_prefix: str = "single") -> None:
    """Render action buttons for a single candidate detail view."""
    cols = st.columns(6)
    status = candidate.status

    with cols[0]:
        if status != "accepted" and st.button("✅ Prijať", key=f"{key_prefix}_accept"):
            _single_action(candidate, "accepted")

    with cols[1]:
        if status != "in_progress" and st.button("🚧 V práci", key=f"{key_prefix}_wip"):
            brief_url = st.session_state.get(f"{key_prefix}_brief_url", "")
            _single_action(candidate, "in_progress", brief_url=brief_url or None)

    with cols[2]:
        if status != "published" and st.button("🟢 Publikované", key=f"{key_prefix}_pub_btn"):
            st.session_state[f"{key_prefix}_show_pub"] = True

    with cols[3]:
        if status != "rejected" and st.button("❌ Zamietnuť", key=f"{key_prefix}_rej_btn"):
            st.session_state[f"{key_prefix}_show_rej"] = True

    with cols[4]:
        if status != "snoozed" and st.button("💤 Odložiť", key=f"{key_prefix}_snooze_btn"):
            st.session_state[f"{key_prefix}_show_snooze"] = True

    with cols[5]:
        if status not in ("new", "seen") and st.button("🔄 Reset", key=f"{key_prefix}_reset"):
            _single_action(candidate, "seen", reason="manuálny reset")

    # Publish modal
    if st.session_state.get(f"{key_prefix}_show_pub"):
        with st.form(f"{key_prefix}_pub_form"):
            pub_url = st.text_input("URL publikovaného článku:", placeholder="https://...")
            if st.form_submit_button("Potvrdiť") and pub_url:
                _single_action(candidate, "published", published_url=pub_url)
                st.session_state.pop(f"{key_prefix}_show_pub", None)

    # Reject modal
    if st.session_state.get(f"{key_prefix}_show_rej"):
        with st.form(f"{key_prefix}_rej_form"):
            reason = st.selectbox("Dôvod:", REJECTION_REASONS)
            if st.form_submit_button("Potvrdiť zamietnutie"):
                _single_action(candidate, "rejected", reason=reason)
                st.session_state.pop(f"{key_prefix}_show_rej", None)

    # Snooze modal
    if st.session_state.get(f"{key_prefix}_show_snooze"):
        with st.form(f"{key_prefix}_snooze_form"):
            snooze_until = st.date_input(
                "Odložiť do:",
                value=_default_snooze_date(),
                min_value=date.today() + timedelta(days=1),
            )
            if st.form_submit_button("Potvrdiť"):
                _single_action(candidate, "snoozed", snoozed_until=snooze_until)
                st.session_state.pop(f"{key_prefix}_show_snooze", None)


def _bulk_action(candidate_ids: list[int], status: str, reason: str = "") -> None:
    db = get_db()
    try:
        bulk_change_status(candidate_ids, status, db, reason=reason or None)
    finally:
        db.close()


def _single_action(candidate: Candidate, status: str, **kwargs) -> None:
    db = get_db()
    try:
        # Re-fetch in this session
        c = db.query(Candidate).get(candidate.id)
        if c:
            change_status(c, status, db, **kwargs)
            db.commit()
        st.rerun()
    finally:
        db.close()
