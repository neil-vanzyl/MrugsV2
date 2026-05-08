"""
gui.py — Accedo Strategic Lead Scout
=====================================
Streamlit GUI with:
  - Live research waterfall with progress status
  - Full result previews: score, emails, power map, signals, objection counters
  - Persistent run history (session memory)
  - Hot / Cold tab routing visibility
  - Both Apollo keys in API status panel
  - Live log viewer tab for debugging
"""

import json
import logging
import os
from io import StringIO
import streamlit as st
import pandas as pd
import main
import config
from utils.helpers import setup_logging
from utils.usage_tracker import load_usage_history
from datetime import datetime

# Setup logging once at import
setup_logging(level=logging.INFO)

# ---------------------------------------------------------------------------
# In-memory log capture (Streamlit Cloud safe — no filesystem dependency)
# ---------------------------------------------------------------------------

if "log_stream" not in st.session_state:
    st.session_state["log_stream"] = StringIO()
    _stream_handler = logging.StreamHandler(st.session_state["log_stream"])
    _stream_handler.setFormatter(
        logging.Formatter("%(asctime)s  %(levelname)-8s  %(name)s  —  %(message)s")
    )
    logging.getLogger("ott_lead_gen").addHandler(_stream_handler)

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Accedo Lead Scout",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Run history helpers
# ---------------------------------------------------------------------------

def _get_history() -> list:
    if "run_history" not in st.session_state:
        st.session_state["run_history"] = []
    return st.session_state["run_history"]


def _append_to_history(record: dict) -> None:
    history = _get_history()
    history.insert(0, record)
    st.session_state["run_history"] = history[:50]


# ---------------------------------------------------------------------------
# Visual helpers
# ---------------------------------------------------------------------------

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
# Cost / usage panel
# ---------------------------------------------------------------------------

def render_usage_panel(usage_summary: dict) -> None:
    """Render a compact per-run cost breakdown in an expander."""
    if not usage_summary:
        return

    total = usage_summary.get("total_cost_usd", 0)
    per_p = usage_summary.get("cost_per_prospect", 0)
    n     = usage_summary.get("prospects", 0)

    with st.expander(
        f"💰 Run Cost: ${total:.4f} total  ·  ${per_p:.4f}/prospect  ·  {n} prospect(s)",
        expanded=False,
    ):
        g = usage_summary.get("grok", {})
        s = usage_summary.get("sonnet", {})
        o = usage_summary.get("opus", {})
        e = usage_summary.get("exa", {})
        a = usage_summary.get("apollo", {})

        col1, col2 = st.columns(2)

        with col1:
            st.markdown("**Token Usage**")
            st.markdown(
                f"| Tool | Input | Output | Est. Cost |\n"
                f"|------|-------|--------|-----------|\n"
                f"| Grok grok-4-1 | {g.get('input_tokens',0):,} | {g.get('output_tokens',0):,} | ${g.get('cost_usd',0):.4f} |\n"
                f"| Claude Sonnet | {s.get('input_tokens',0):,} | {s.get('output_tokens',0):,} | ${s.get('cost_usd',0):.4f} |\n"
                f"| Claude Opus | {o.get('input_tokens',0):,} | {o.get('output_tokens',0):,} | ${o.get('cost_usd',0):.4f} |"
            )

        with col2:
            st.markdown("**API Credits**")
            st.markdown(
                f"| Tool | Usage | Est. Cost |\n"
                f"|------|-------|-----------|\n"
                f"| Exa | {e.get('credits',0)} credits | ${e.get('cost_usd',0):.4f} |\n"
                f"| Apollo Enrich | {a.get('enrich_credits',0)} credits | ${a.get('cost_usd',0):.4f} |\n"
                f"| Apollo Search | {a.get('search_calls',0)} calls | $0.00 (free) |"
            )

        st.divider()
        st.markdown(f"**Total: ${total:.4f}** across {n} prospect(s)  ·  ${per_p:.4f} per prospect")

        per_p_list = usage_summary.get("per_prospect", [])
        if per_p_list:
            st.markdown("**Per-prospect breakdown**")
            rows = []
            for p in per_p_list:
                rows.append({
                    "Company": p.get("company", ""),
                    "Grok in": p.get("grok_input_tokens", 0),
                    "Grok out": p.get("grok_output_tokens", 0),
                    "Sonnet": f"{p.get('sonnet_input_tokens',0)}in/{p.get('sonnet_output_tokens',0)}out",
                    "Opus": f"{p.get('opus_input_tokens',0)}in/{p.get('opus_output_tokens',0)}out",
                    "Exa": p.get("exa_credits_total", 0),
                    "Apollo": p.get("apollo_enrich_credits", 0),
                    "Cost $": f"{p.get('cost_usd', 0):.4f}",
                })
            st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)


# ---------------------------------------------------------------------------
# Full result card
# ---------------------------------------------------------------------------

def render_result_card(r: dict, card_idx: int) -> None:
    company  = r.get("company", "Unknown")
    verdict  = r.get("verdict", "?")
    score    = r.get("refined_score")
    grok_sc  = r.get("grok_score")
    is_cold  = verdict == "COLD"
    error    = r.get("error")

    prospect = r.get("prospect", {})
    analyst  = r.get("analyst", {})
    emails   = r.get("emails", {})

    label = f"{'❄️' if is_cold else '🔥'} {company}   ·   {score}/100   ·   {verdict}"

    with st.expander(label, expanded=(card_idx == 0 and not is_cold)):

        if error:
            st.error(f"Pipeline error: {error}")
            return

        h1, h2, h3, h4 = st.columns([2, 1.5, 1.5, 3])

        with h1:
            st.markdown("**Opportunity Score**")
            st.markdown(_score_bar_html(score), unsafe_allow_html=True)
            if grok_sc and grok_sc != score:
                st.caption(f"Grok raw: {grok_sc} → Analyst adjusted: {score}")

        with h2:
            st.markdown("**Verdict**")
            st.markdown(_verdict_chip(verdict), unsafe_allow_html=True)

        with h3:
            st.markdown("**Sheet Tab**")
            st.markdown("❄️ Cold Leads" if is_cold else "🔥 Leads")

        with h4:
            gap = prospect.get("transition_gap_timer", "")
            if gap:
                st.markdown("**Transition Gap**")
                st.info(gap)

        st.divider()

        t_intel, t_emails, t_obj = st.tabs([
            "🧠 Intelligence", "✉️ Outreach Emails", "🛡️ Objection Counters"
        ])

        with t_intel:
            ic1, ic2 = st.columns(2)

            with ic1:
                inflection = prospect.get("causal_inflection", "")
                if inflection:
                    st.markdown("**Causal Inflection**")
                    st.write(inflection)
                entry = analyst.get("top_entry_point", "")
                if entry:
                    st.markdown("**Accedo Entry Point**")
                    st.success(entry)
                risk = analyst.get("key_risk_if_no_action", "")
                if risk:
                    st.markdown("**Risk if Accedo Waits 90 Days**")
                    st.warning(risk)
                reasoning = analyst.get("score_delta_reasoning", "")
                if reasoning:
                    st.markdown("**Analyst Reasoning**")
                    st.caption(reasoning)

            with ic2:
                pm  = prospect.get("power_map", {})
                vis = pm.get("the_visionary", {})
                ops = pm.get("the_operator", {})

                st.markdown("**👤 Visionary**")
                if vis.get("name"):
                    li = vis.get("linkedin", "")
                    nm = f"[{vis['name']}]({li})" if li else vis["name"]
                    st.markdown(f"{nm} — *{vis.get('title', '')}*")
                    if vis.get("public_quote"):
                        st.caption(f'💬 "{vis["public_quote"][:220]}"')
                    if vis.get("angle"):
                        st.info(f"Hook: {vis['angle']}")
                else:
                    st.caption("Not identified in this run")

                st.markdown("**⚙️ Operator**")
                if ops.get("name"):
                    li = ops.get("linkedin", "")
                    nm = f"[{ops['name']}]({li})" if li else ops["name"]
                    st.markdown(f"{nm} — *{ops.get('title', '')}*")
                    if ops.get("public_quote"):
                        st.caption(f'💬 "{ops["public_quote"][:220]}"')
                    if ops.get("angle"):
                        st.info(f"Hook: {ops['angle']}")
                else:
                    st.caption("Not identified in this run")

            signals = prospect.get("signals", [])
            if signals:
                st.markdown("---")
                st.markdown("**📡 Research Signals**")
                for sig in signals[:4]:
                    conf = sig.get("confidence", "")
                    icon = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(conf, "⚪")
                    stype = sig.get("signal_type", "")
                    ev = sig.get("evidence", "")[:250]
                    src = sig.get("source_url") or sig.get("source_type", "")
                    src_md = f" · [source]({src})" if src and src.startswith("http") else (f" · {src}" if src else "")
                    st.markdown(f"{icon} **{stype}** — {ev}{src_md}")

        with t_emails:
            vis_email = emails.get("visionary_email", {})
            ops_email = emails.get("operator_email", {})
            ec1, ec2 = st.columns(2)

            with ec1:
                vis_name = vis.get("name", "Visionary")
                subj = vis_email.get("subject_line", "")
                body = vis_email.get("body", "")
                st.markdown(f"**✉️ To: {vis_name}**")
                if subj:
                    st.markdown(
                        f'<div style="background:#f0f4ff;border-left:3px solid #4a6fa5;'
                        f'padding:6px 10px;border-radius:4px;margin-bottom:8px;font-size:0.9em">'
                        f'<strong>Subject:</strong> {subj}</div>',
                        unsafe_allow_html=True,
                    )
                if body and "refused" not in body and "failed" not in body:
                    st.text_area("vis_body", value=body, height=230,
                                 key=f"vis_{card_idx}", label_visibility="collapsed")
                else:
                    st.caption(body or "No draft generated.")

            with ec2:
                ops_name = ops.get("name", "Operator")
                subj = ops_email.get("subject_line", "")
                body = ops_email.get("body", "")
                st.markdown(f"**✉️ To: {ops_name}**")
                if subj:
                    st.markdown(
                        f'<div style="background:#f0fff4;border-left:3px solid #28a745;'
                        f'padding:6px 10px;border-radius:4px;margin-bottom:8px;font-size:0.9em">'
                        f'<strong>Subject:</strong> {subj}</div>',
                        unsafe_allow_html=True,
                    )
                if body and "refused" not in body and "failed" not in body:
                    st.text_area("ops_body", value=body, height=230,
                                 key=f"ops_{card_idx}", label_visibility="collapsed")
                else:
                    st.caption(body or "No draft generated.")

        with t_obj:
            outreach = prospect.get("outreach", {})
            obj_stack = outreach.get("objection_stack", [])
            if obj_stack:
                for obj in obj_stack:
                    objection = obj.get("objection", "")
                    counter   = obj.get("counter", "")
                    evidence  = obj.get("counter_evidence_source", "")
                    if objection:
                        st.markdown(f"**❓ {objection}**")
                        if counter:
                            st.success(f"**Counter:** {counter}")
                        if evidence:
                            st.caption(f"Evidence: {evidence}")
                        st.divider()
            note = outreach.get("salesforce_note", "")
            if note:
                st.markdown("**📋 Salesforce Note**")
                st.code(note, language=None)


# ---------------------------------------------------------------------------
# Run history sidebar
# ---------------------------------------------------------------------------

def render_history_sidebar() -> None:
    history = _get_history()
    if not history:
        st.caption("No runs yet this session.")
        return

    for run in history:
        ts    = run.get("timestamp", "")
        q     = run.get("query", "")[:45]
        total = run.get("prospect_count", 0)
        hot   = run.get("hot_count", 0)
        warm  = run.get("warm_count", 0)
        cold  = run.get("cold_count", 0)
        dry   = run.get("dry_run", True)
        written = run.get("rows_written", 0)

        tag   = "🔍" if dry else "✅"
        with st.expander(f"{tag} {ts} · {q}", expanded=False):
            cols = st.columns(4)
            cols[0].metric("Total", total)
            cols[1].metric("🔥", hot)
            cols[2].metric("🌡️", warm)
            cols[3].metric("❄️", cold)
            if not dry:
                st.caption(f"{written} row(s) → Sheets")
            companies = run.get("companies", [])
            if companies:
                st.dataframe(pd.DataFrame(companies), width='stretch', hide_index=True)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("## 🎯 Lead Scout")
    st.caption("Accedo · Director of Strategic Accounts")
    st.divider()

    is_dry_run = st.checkbox(
        "Dry Run Mode",
        value=False,
        help="ON = research + qualify only, no Sheets writes.\nOFF = full pipeline.",
    )
    if is_dry_run:
        st.warning("🔍 Dry Run active")
    else:
        st.success("✅ Live Mode — will write to Sheets")

    st.divider()
    st.markdown("**Google Sheets**")
    st.caption(f"Sheet: `{config.GOOGLE_SHEET_NAME}`")
    st.caption(f"Hot: `{config.GOOGLE_WORKSHEET_NAME}`")
    st.caption(f"Cold: `{config.GOOGLE_COLD_WORKSHEET_NAME}`")

    st.divider()
    st.markdown("**API Status**")

    def _api_status(attr: str, label: str) -> None:
        val = getattr(config, attr, "")
        icon = "🟢" if val else "🔴"
        st.caption(f"{icon} {label}")

    _api_status("XAI_API_KEY",            "Grok / xAI")
    _api_status("ANTHROPIC_API_KEY",      "Claude / Anthropic")
    _api_status("EXA_API_KEY",            "Exa (LinkedIn)")
    _api_status("APOLLO_MASTER_API_KEY",  "Apollo Master Key (Search)")
    _api_status("APOLLO_API_KEY",         "Apollo Standard Key (Enrich)")

    st.divider()
    st.markdown("**Run History**")
    render_history_sidebar()

    st.divider()
    st.markdown("**Usage History**")
    with st.expander("📊 Historical Cost Log", expanded=False):
        try:
            history = load_usage_history(max_runs=10)
            if history:
                rows = [
                    {
                        "Date": h.get("timestamp", "")[:16],
                        "Query": h.get("query", "")[:30],
                        "Prospects": h.get("prospects", 0),
                        "Total $": f"{h.get('total_cost_usd', 0):.4f}",
                        "$/prospect": f"{h.get('cost_per_prospect', 0):.4f}",
                    }
                    for h in history
                ]
                st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
            else:
                st.caption("No usage history yet — run the pipeline first.")
        except Exception as exc:
            st.caption(f"Could not load usage history: {exc}")


# ---------------------------------------------------------------------------
# Main content
# ---------------------------------------------------------------------------

st.title("🎯 Accedo Strategic Lead Scout")
st.markdown(
    "**Grok-4** live research · **Claude Sonnet** qualification · "
    "**Claude Opus** outreach · **Exa** LinkedIn intel · **Apollo** contact enrichment"
)
st.divider()

query = st.text_input(
    "Discovery Scope",
    placeholder="e.g., Regional sports networks migrating from ViewLift 2025...",
    help=(
        "Describe the OTT pain signal or company type to hunt for. "
        "Grok will autonomously search SEC filings, job boards, "
        "app stores, and industry press."
    ),
)

btn_col1, btn_col2 = st.columns([3, 1])
with btn_col1:
    run_btn = st.button(
        "🚀 Start Research Waterfall",
        type="primary",
        use_container_width=True,
        disabled=not query,
    )
with btn_col2:
    rotate_btn = st.button(
        "🔄 Rotate Queries",
        use_container_width=True,
        help="Run all built-in signal queries sequentially",
    )


# ---------------------------------------------------------------------------
# Execution + display
# ---------------------------------------------------------------------------

def _run_and_display(query_str: str, dry: bool) -> None:
    # Clear log buffer at the start of each run so the viewer shows only this run
    log_stream = st.session_state.get("log_stream")
    if log_stream:
        log_stream.truncate(0)
        log_stream.seek(0)

    with st.status(
        f"Researching: *{query_str[:80]}{'…' if len(query_str) > 80 else ''}*",
        expanded=True,
    ) as status:
        st.write("🔍 Grok-4 research waterfall — searching SEC, jobs, press, app stores…")
        try:
            results = main.run_pipeline(query_str, dry_run=dry)
            status.update(label="✅ Research complete!", state="complete", expanded=False)
        except Exception as exc:
            status.update(label="❌ Pipeline error", state="error", expanded=True)
            st.error(f"**Error:** {exc}")
            st.exception(exc)
            return

    if not results:
        st.warning("⚠️ Grok found no prospects. Try a broader discovery scope.")
        return

    hot     = sum(1 for r in results if r.get("verdict") == "HOT")
    warm    = sum(1 for r in results if r.get("verdict") == "WARM")
    cold    = sum(1 for r in results if r.get("verdict") == "COLD")
    written = sum(r.get("rows_written", 0) for r in results)

    st.divider()
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Prospects Found", len(results))
    m2.metric("🔥 HOT", hot)
    m3.metric("🌡️ WARM", warm)
    m4.metric("❄️ COLD", cold)
    m5.metric("Written to Sheets", "—" if dry else written)

    if results:
        usage_summary = results[0].get("usage_summary", {})
        if usage_summary:
            render_usage_panel(usage_summary)

    if not dry:
        if written > 0:
            st.success(f"✅ {written} lead(s) written to **{config.GOOGLE_SHEET_NAME}**")
        else:
            st.error(
                "⚠️ No rows written to Sheets. Possible causes:\n"
                "- Dry Run Mode is still ON in the sidebar\n"
                "- Column count mismatch between config.py and sheets.py\n"
                "- All leads were duplicate domains\n"
                "- Google Sheets credentials issue\n\n"
                "Check the Pipeline Log expander below for the specific error."
            )

    st.divider()
    st.subheader("Summary")

    rows = []
    for r in results:
        p = r.get("prospect", {})
        rows.append({
            "Company":  r.get("company", ""),
            "Domain":   r.get("domain", ""),
            "Grok":     r.get("grok_score", "?"),
            "Score":    r.get("refined_score", "?"),
            "Verdict":  r.get("verdict", "?"),
            "Tab":      "❄️ Cold" if r.get("verdict") == "COLD" else "🔥 Hot",
            "Exa":      "✓" if r.get("exa_enriched") else "—",
            "Apollo":   "✓" if r.get("apollo_active") and config.APOLLO_ENABLED else "—",
            "Written":  "✅" if r.get("rows_written", 0) > 0 else ("🔍" if dry else "—"),
            "Type":     p.get("opportunity_type", ""),
        })

    df = pd.DataFrame(rows)

    def _color_row(val):
        c = {"HOT": "#d4f0dc", "WARM": "#fff7d6", "COLD": "#fde8e8"}
        return f"background-color: {c.get(val, '')}"

    st.dataframe(
        df.style.map(_color_row, subset=["Verdict"]),  # FIX: applymap → map (pandas 2.1+)
        width='stretch',
        hide_index=True,
    )

    st.divider()
    st.subheader("Lead Intelligence & Outreach")
    st.caption("Click any card to expand the full intelligence report, emails, and objection counters.")

    sort_order = {"HOT": 0, "WARM": 1, "COLD": 2}
    for i, r in enumerate(sorted(results, key=lambda x: sort_order.get(x.get("verdict", "COLD"), 2))):
        render_result_card(r, i)

    # Live log viewer — reads from in-memory buffer (Streamlit Cloud safe)
    st.divider()
    with st.expander("📋 Pipeline Log (last run)", expanded=False):
        log_stream = st.session_state.get("log_stream")
        if log_stream:
            log_contents = log_stream.getvalue()
            if log_contents:
                st.code(log_contents[-12000:], language=None)
            else:
                st.caption("No log output captured for this run.")
        else:
            st.caption("Log stream not initialised — refresh the page and try again.")

    _append_to_history({
        "timestamp":      datetime.now().strftime("%Y-%m-%d %H:%M"),
        "query":          query_str,
        "prospect_count": len(results),
        "hot_count":      hot,
        "warm_count":     warm,
        "cold_count":     cold,
        "rows_written":   written,
        "dry_run":        dry,
        "companies": [
            {"Company": r.get("company", ""), "Score": r.get("refined_score", ""), "Verdict": r.get("verdict", "")}
            for r in results
        ],
    })


if run_btn:
    _run_and_display(query, is_dry_run)

elif rotate_btn:
    queries = config.OTT_SIGNAL_QUERIES
    st.info(f"Running {len(queries)} signal queries sequentially…")
    for i, q in enumerate(queries, 1):
        st.markdown(f"---\n**Query {i}/{len(queries)}:** `{q[:80]}`")
        _run_and_display(q, is_dry_run)

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
st.divider()
st.caption(
    f"Accedo Lead Scout · "
    f"{config.GROK_SCOUT_MODEL} · "
    f"{config.CLAUDE_ANALYST_MODEL} analyst · "
    f"{config.CLAUDE_COPYWRITER_MODEL} copywriter · "
    f"Last render: {datetime.now().strftime('%H:%M:%S')}"
)
