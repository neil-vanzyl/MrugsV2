"""
gui.py — Accedo Strategic Lead Scout (Deterministic Edition)
============================================================
Three tracks:
  1. 🔍 Discovery — Gemini + Exa find companies -> Unified Pipeline
  2. 📥 Event List — Upload CSV -> Unified Pipeline
  3. 📋 Account Intelligence — Tracked accounts -> Unified Pipeline
"""

import logging
from datetime import datetime
from io import StringIO

import pandas as pd
import streamlit as st

import config
import main
from utils.helpers import setup_logging
from utils.usage_tracker import load_usage_history

# --- CRITICAL: THIS MUST BE THE ABSOLUTE FIRST STREAMLIT COMMAND ---
st.set_page_config(
    page_title="Accedo Lead Scout",
    layout="wide",
    initial_sidebar_state="expanded",
)

setup_logging(level=logging.INFO)

# ---------------------------------------------------------------------------
# In-memory log capture
# ---------------------------------------------------------------------------

if "log_stream" not in st.session_state:
    st.session_state["log_stream"] = StringIO()
    _stream_handler = logging.StreamHandler(st.session_state["log_stream"])
    _stream_handler.setFormatter(
        logging.Formatter("%(asctime)s  %(levelname)-8s  %(name)s  —  %(message)s")
    )
    logging.getLogger("ott_lead_gen").addHandler(_stream_handler)

# ---------------------------------------------------------------------------
# Page config & visual helpers
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Accedo Lead Scout",
    layout="wide",
    initial_sidebar_state="expanded",
)

def _verdict_color(verdict: str) -> str:
    return {"HOT": "#28a745", "WARM": "#e6a817", "COLD": "#dc3545"}.get(verdict or "", "#6c757d")

def _score_bar_html(score) -> str:
    if score is None:
        return ""
    pct = min(int(score), 100)
    color = "#28a745" if pct >= 70 else "#e6a817" if pct >= 50 else "#dc3545"
    return (
        f'<div style="background:#e9ecef;border-radius:6px;height:10px;width:100%;margin-bottom:4px">'
        f'<div style="background:{color};width:{pct}%;height:10px;border-radius:6px"></div></div>'
        f'<span style="font-size:0.85em;color:#555">{pct}/100</span>'
    )

def _verdict_chip(verdict: str) -> str:
    color = _verdict_color(verdict)
    return (
        f'<span style="background:{color};color:white;padding:3px 12px;'
        f'border-radius:12px;font-weight:700;font-size:0.82em">{verdict or "?"}</span>'
    )

# ---------------------------------------------------------------------------
# Shared Results Display
# ---------------------------------------------------------------------------
def _display_results(results: list, dry: bool, query_str: str, bu: str, track_name: str) -> None:
    if not results:
        st.warning("⚠️ No results returned.")
        return

    hot = sum(1 for r in results if r.get("verdict") == "HOT")
    warm = sum(1 for r in results if r.get("verdict") == "WARM")
    cold = sum(1 for r in results if r.get("verdict") == "COLD")
    written = sum(r.get("rows_written", 0) for r in results)

    st.divider()
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Prospects Processed", len(results))
    m2.metric("🔥 HOT", hot)
    m3.metric("🌡️ WARM", warm)
    m4.metric("❄️ COLD", cold)
    m5.metric("Written to Sheets", "—" if dry else written)
    m6.metric("BU / Track", f"{bu} / {track_name}")

    if not dry and written > 0:
        st.success(f"✅ {written} lead(s) written to **{config.GOOGLE_SHEET_NAME}**")

    st.subheader("Summary")
    rows = []
    for r in results:
        p = r.get("prospect", {})
        rows.append({
            "Company": r.get("company", ""),
            "Domain": r.get("domain", ""),
            "Score": r.get("refined_score", "?"),
            "Verdict": r.get("verdict", "?"),
            "Tab": "❄️ Cold" if r.get("verdict") == "COLD" else "🔥 Hot",
            "Written": "✅" if r.get("rows_written", 0) > 0 else ("🔍" if dry else "—"),
            "Error": r.get("error", "None")
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, width='stretch', hide_index=True)

    with st.expander("📋 Pipeline Log (last run)", expanded=False):
        log_stream = st.session_state.get("log_stream")
        if log_stream:
            st.code(log_stream.getvalue(), language=None)

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("## 🎯 Lead Scout")
    st.caption("Accedo · Deterministic Pipeline")
    st.divider()

    selected_bu = st.selectbox(
        "Business Unit",
        options=config.BU_OPTIONS,
        index=config.BU_OPTIONS.index(config.BU_DEFAULT)
    )
    st.session_state["selected_bu"] = selected_bu

    is_dry_run = st.checkbox("Dry Run Mode", value=False)
    if is_dry_run:
        st.warning("🔍 Dry Run active (No Sheets writes)")
    else:
        st.success("✅ Live Mode")

# ---------------------------------------------------------------------------
# Main Content
# ---------------------------------------------------------------------------
st.title("MRUGS: Accedo Lead Scout")
st.markdown("**Concurrent Enrichment Engine** · Exa + Apollo + Claude")

bu = st.session_state.get("selected_bu", config.BU_DEFAULT)

tab_discovery, tab_list, tab_accounts = st.tabs([
    "🔍 1. Discovery", 
    "📥 2. Event List Enrichment", 
    "📋 3. Account Intelligence"
])

# ---------------------------------------------------------------------------
# TRACK 1: DISCOVERY
# ---------------------------------------------------------------------------
with tab_discovery:
    st.subheader(f"Broad Search & Enrich · BU={bu}")
    st.caption("Define your signal. The engine will find matching companies and enrich them concurrently.")

    search_query = st.text_area(
        "Research Brief",
        placeholder="e.g. Find Tier 1 and Tier 2 Sports broadcasters in NAM that recently announced a new rights deal but lack a CTV platform.",
        height=100
    )

    if st.button("🔍 Find & Enrich Companies", type="primary", use_container_width=True):
        if not search_query.strip():
            st.error("Please enter a research brief.")
        else:
            log_stream = st.session_state.get("log_stream")
            if log_stream:
                log_stream.truncate(0)
                log_stream.seek(0)
                
            with st.status("Running Discovery & Concurrent Enrichment...", expanded=True) as status:
                try:
                    results = main.run_discovery_track(query=search_query, bu=bu, dry_run=is_dry_run)
                    status.update(label="✅ Discovery & Enrichment Complete!", state="complete", expanded=False)
                    _display_results(results, is_dry_run, search_query, bu, "Discovery")
                except Exception as exc:
                    status.update(label="❌ Pipeline Error", state="error", expanded=True)
                    st.error(f"Error: {exc}")

# ---------------------------------------------------------------------------
# TRACK 2: EVENT LIST ENRICHMENT (Net-New)
# ---------------------------------------------------------------------------
with tab_list:
    st.subheader(f"Bulk List Enrichment · BU={bu}")
    st.caption("Upload a CSV of companies (e.g., from an event or conference) to instantly verify contacts, scrape LinkedIn signals, and draft outreach.")

    uploaded_list = st.file_uploader("Upload CSV (Requires 'Company' and 'Domain' columns)", type=["csv"], key="list_upload")

    if uploaded_list:
        try:
            df_list = pd.read_csv(uploaded_list)
            df_list.columns = [c.strip() for c in df_list.columns]

            if not {"Company", "Domain"}.issubset(df_list.columns):
                st.error("CSV must contain 'Company' and 'Domain' columns.")
            else:
                df_list = df_list.dropna(subset=["Company", "Domain"])
                st.dataframe(df_list.head(), hide_index=True)

                if st.button("🚀 Enrich List Concurrently", type="primary", use_container_width=True):
                    prospects = [{"name": row["Company"], "domain": row["Domain"]} for _, row in df_list.iterrows()]
                    
                    log_stream = st.session_state.get("log_stream")
                    if log_stream:
                        log_stream.truncate(0)
                        log_stream.seek(0)

                    with st.status(f"Enriching {len(prospects)} targets concurrently...", expanded=True) as status:
                        try:
                            results = main.run_list_track(prospects=prospects, bu=bu, dry_run=is_dry_run)
                            status.update(label="✅ Bulk Enrichment Complete!", state="complete", expanded=False)
                            _display_results(results, is_dry_run, "CSV Upload", bu, "List Enrichment")
                        except Exception as exc:
                            status.update(label="❌ Pipeline Error", state="error", expanded=True)
                            st.error(f"Error: {exc}")
        except Exception as exc:
            st.error(f"Failed to read CSV: {exc}")

# ---------------------------------------------------------------------------
# TRACK 3: ACCOUNT INTELLIGENCE
# ---------------------------------------------------------------------------
with tab_accounts:
    st.subheader(f"Tracked Accounts Monitor · BU={bu}")
    st.caption("Run the deterministic engine against your pre-existing targeted accounts list to check for new signals.")

    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("🔄 Load Accounts"):
            try:
                from core.sheets import SheetsClient
                st.session_state["tracked_accounts"] = SheetsClient().get_accounts(bu_filter=bu)
            except Exception as exc:
                st.error(f"Error loading accounts: {exc}")

    accounts = st.session_state.get("tracked_accounts", [])
    
    if accounts:
        st.dataframe(pd.DataFrame(accounts), hide_index=True)

        if st.button("▶️ Run Intelligence on Tracked Accounts", type="primary", use_container_width=True):
            log_stream = st.session_state.get("log_stream")
            if log_stream:
                log_stream.truncate(0)
                log_stream.seek(0)

            with st.status(f"Running Accounts Check for {len(accounts)} targets...", expanded=True) as status:
                try:
                    results = main.run_accounts_track(bu=bu, dry_run=is_dry_run)
                    status.update(label="✅ Account Check Complete!", state="complete", expanded=False)
                    _display_results(results, is_dry_run, "Accounts Check", bu, "Accounts")
                except Exception as exc:
                    status.update(label="❌ Pipeline Error", state="error", expanded=True)
                    st.error(f"Error: {exc}")
    else:
        st.info("No accounts loaded. Click 'Load Accounts' to fetch from Sheets.")