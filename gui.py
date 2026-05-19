"""
gui.py — Accedo Strategic Lead Scout
=====================================
Two tracks:
  🔍 Discovery — Gemini + Exa find companies, Grok researches them
  📋 Account Intelligence — research tracked accounts from Sheets

Both tracks respect the BU selector (NAM / E&L / APAC).
"""

import logging
import random
from datetime import datetime
from io import StringIO

import pandas as pd
import streamlit as st

import config
import main
from utils.helpers import setup_logging
from utils.usage_tracker import load_usage_history

setup_logging(level=logging.INFO)


# ---------------------------------------------------------------------------
# Suggested prompts loader
# ---------------------------------------------------------------------------

def _load_suggested_prompts() -> list:
    try:
        with open("suggested_prompts.txt", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        return ["Regional sports broadcaster migrating from ViewLift 2026"]


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
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Accedo Lead Scout",
    page_icon=None,
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Session state helpers
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
    if not usage_summary:
        return
    total = usage_summary.get("total_cost_usd", 0)
    per_p = usage_summary.get("cost_per_prospect", 0)
    n     = usage_summary.get("prospects", 0)

    with st.expander(
        f"💰 Run Cost: ${total:.4f} total  ·  ${per_p:.4f}/prospect  ·  {n} prospect(s)",
        expanded=False,
    ):
        g    = usage_summary.get("grok", {})
        g_ai = usage_summary.get("gemini", {})
        s    = usage_summary.get("sonnet", {})
        o    = usage_summary.get("opus", {})
        e    = usage_summary.get("exa", {})
        a    = usage_summary.get("apollo", {})

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Token Usage**")
            st.markdown(
                f"| Tool | Input | Output | Est. Cost |\n"
                f"|------|-------|--------|-----------|\n"
                f"| Grok grok-4-1 | {g.get('input_tokens',0):,} | {g.get('output_tokens',0):,} | ${g.get('cost_usd',0):.4f} |\n"
                f"| Gemini Flash | {g_ai.get('input_tokens',0):,} | {g_ai.get('output_tokens',0):,} | ${g_ai.get('cost_usd',0):.4f} |\n"
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
                if p.get("company") == "_grok_research":
                    rows.append({
                        "Company": "🔍 Discovery + Grok (shared)",
                        "Grok in": p.get("grok_input_tokens", 0),
                        "Grok out": p.get("grok_output_tokens", 0),
                        "Gemini": f"{p.get('gemini_input_tokens',0)}in/{p.get('gemini_output_tokens',0)}out",
                        "Sonnet": "—", "Opus": "—",
                        "Exa": p.get("exa_credits_total", 0),
                        "Apollo": "—",
                        "Cost $": f"{p.get('cost_usd', 0):.4f}",
                    })
                    continue
                rows.append({
                    "Company": p.get("company", ""),
                    "Grok in": p.get("grok_input_tokens", 0),
                    "Grok out": p.get("grok_output_tokens", 0),
                    "Gemini": f"{p.get('gemini_input_tokens',0)}in/{p.get('gemini_output_tokens',0)}out",
                    "Sonnet": f"{p.get('sonnet_input_tokens',0)}in/{p.get('sonnet_output_tokens',0)}out",
                    "Opus": f"{p.get('opus_input_tokens',0)}in/{p.get('opus_output_tokens',0)}out",
                    "Exa": p.get("exa_credits_total", 0),
                    "Apollo": p.get("apollo_enrich_credits", 0),
                    "Cost $": f"{p.get('cost_usd', 0):.4f}",
                })
            st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)


# ---------------------------------------------------------------------------
# Result card (live run)
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
    bu       = r.get("bu", "")

    label = f"{'❄️' if is_cold else '🔥'} {company}   ·   {score}/100   ·   {verdict}   ·   {bu}"

    with st.expander(label, expanded=(card_idx == 0 and not is_cold)):
        if error:
            st.error(f"Pipeline error: {error}")
            return

        h1, h2, h3, h4, h5 = st.columns([2, 1.5, 1.5, 1.5, 3])
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
            st.markdown("**BU**")
            st.markdown(f"`{bu}`" if bu else "—")
        with h5:
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
# History card (from Sheets row)
# ---------------------------------------------------------------------------

def render_history_card(row: dict) -> None:
    company  = row.get("Company", "Unknown")
    score    = row.get("Opportunity Score", "").replace("/100", "")
    verdict  = row.get("Priority", "")
    tab      = row.get("_tab", "")
    domain   = row.get("Domain", "")
    ts       = row.get("Timestamp", "")
    bu       = row.get("BU", "")
    is_cold  = tab == "Cold Leads"

    st.markdown(f"### {'❄️' if is_cold else '🔥'} {company}")
    st.caption(f"{domain} · {ts} · {tab} · BU: {bu or '—'}")

    h1, h2, h3 = st.columns([2, 2, 2])
    with h1:
        st.markdown("**Opportunity Score**")
        try:
            st.markdown(_score_bar_html(int(score)), unsafe_allow_html=True)
        except Exception:
            st.caption(score or "—")
    with h2:
        st.markdown("**Verdict**")
        verdict_map = {"Critical": "HOT", "High": "HOT", "Med": "WARM", "Low": "COLD"}
        v = verdict_map.get(verdict, verdict)
        st.markdown(_verdict_chip(v), unsafe_allow_html=True)
    with h3:
        st.markdown("**Tab**")
        st.markdown("❄️ Cold Leads" if is_cold else "🔥 Leads")

    st.divider()
    t_intel, t_emails, t_obj = st.tabs([
        "🧠 Intelligence", "✉️ Outreach Emails", "🛡️ Objection Counters"
    ])

    with t_intel:
        ic1, ic2 = st.columns(2)
        with ic1:
            for field, label in [
                ("Causal Inflection", "**Causal Inflection**"),
                ("Transition Gap", "**Transition Gap**"),
                ("Opportunity Type", "**Opportunity Type**"),
            ]:
                val = row.get(field, "")
                if val:
                    st.markdown(label)
                    st.write(val) if field != "Opportunity Type" else st.caption(val)
            signal = row.get("Top Signal", "")
            if signal:
                st.markdown("**Top Signal**")
                conf = row.get("Signal Confidence", "")
                icon = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(conf, "⚪")
                src = row.get("Signal Source", "")
                src_md = f" · [source]({src})" if src and src.startswith("http") else (f" · {src}" if src else "")
                st.markdown(f"{icon} {signal[:250]}{src_md}")
        with ic2:
            vis_name  = row.get("Visionary Name", "")
            vis_title = row.get("Visionary Title", "")
            vis_li    = row.get("Visionary LinkedIn", "")
            vis_hook  = row.get("Visionary Hook", "")
            st.markdown("**👤 Visionary**")
            if vis_name:
                nm = f"[{vis_name}]({vis_li})" if vis_li else vis_name
                st.markdown(f"{nm} — *{vis_title}*")
                if vis_hook:
                    st.info(f"Hook: {vis_hook}")
            else:
                st.caption("Not identified")

            ops_name  = row.get("Operator Name", "")
            ops_title = row.get("Operator Title", "")
            ops_li    = row.get("Operator LinkedIn", "")
            ops_hook  = row.get("Operator Hook", "")
            st.markdown("**⚙️ Operator**")
            if ops_name:
                nm = f"[{ops_name}]({ops_li})" if ops_li else ops_name
                st.markdown(f"{nm} — *{ops_title}*")
                if ops_hook:
                    st.info(f"Hook: {ops_hook}")
            else:
                st.caption("Not identified")

            apollo_name  = row.get("Apollo Contact Name", "")
            apollo_title = row.get("Apollo Contact Title", "")
            apollo_email = row.get("Apollo Email", "")
            apollo_li    = row.get("Apollo LinkedIn", "")
            if apollo_name:
                st.markdown("**🔗 Apollo Contact**")
                st.markdown(f"{apollo_name} — *{apollo_title}*")
                if apollo_email:
                    st.caption(f"✉️ {apollo_email}")
                if apollo_li:
                    st.caption(f"[LinkedIn]({apollo_li})")

    with t_emails:
        ec1, ec2 = st.columns(2)
        with ec1:
            st.markdown(f"**✉️ To: {vis_name or 'Visionary'}**")
            vis_subj = row.get("Visionary Subject Line", "")
            vis_body = row.get("Visionary Email", "")
            if vis_subj:
                st.markdown(
                    f'<div style="background:#f0f4ff;border-left:3px solid #4a6fa5;'
                    f'padding:6px 10px;border-radius:4px;margin-bottom:8px;font-size:0.9em">'
                    f'<strong>Subject:</strong> {vis_subj}</div>',
                    unsafe_allow_html=True,
                )
            if vis_body:
                st.text_area("vis_hist_body", value=vis_body, height=230,
                             key=f"hist_vis_{company}", label_visibility="collapsed")
            else:
                st.caption("No draft available.")
        with ec2:
            st.markdown(f"**✉️ To: {ops_name or 'Operator'}**")
            ops_subj = row.get("Operator Subject Line", "")
            ops_body = row.get("Operator Email", "")
            if ops_subj:
                st.markdown(
                    f'<div style="background:#f0fff4;border-left:3px solid #28a745;'
                    f'padding:6px 10px;border-radius:4px;margin-bottom:8px;font-size:0.9em">'
                    f'<strong>Subject:</strong> {ops_subj}</div>',
                    unsafe_allow_html=True,
                )
            if ops_body:
                st.text_area("ops_hist_body", value=ops_body, height=230,
                             key=f"hist_ops_{company}", label_visibility="collapsed")
            else:
                st.caption("No draft available.")

    with t_obj:
        for objection, col in [
            ("We're building this in-house", "Objection: In-House"),
            ("We already have a vendor", "Objection: Incumbent"),
            ("Budget / timing isn't right", "Objection: Budget"),
        ]:
            counter = row.get(col, "")
            if counter:
                st.markdown(f"**❓ {objection}**")
                st.success(f"**Counter:** {counter}")
                st.divider()
        note = row.get("Salesforce Note", "")
        if note:
            st.markdown("**📋 Salesforce Note**")
            st.code(note, language=None)


# ---------------------------------------------------------------------------
# History sidebar
# ---------------------------------------------------------------------------

def render_history_sidebar(bu_filter: str = None) -> None:
    cache_key    = "recent_leads_cache"
    cache_ts_key = "recent_leads_cache_ts"
    cache_bu_key = "recent_leads_cache_bu"
    now = datetime.now().timestamp()
    cache_age = now - st.session_state.get(cache_ts_key, 0)
    cached_bu = st.session_state.get(cache_bu_key, None)

    # Invalidate cache if BU changed or older than 5 minutes
    if (cache_key not in st.session_state
            or cache_age > 900
            or cached_bu != bu_filter):
        try:
            from core.sheets import SheetsClient
            sc = SheetsClient()
            st.session_state[cache_key]    = sc.get_recent_leads(max_rows=10, bu_filter=bu_filter)
            st.session_state[cache_ts_key] = now
            st.session_state[cache_bu_key] = bu_filter
        except Exception as exc:
            st.caption(f"Could not load history: {exc}")
            return

    recent = st.session_state.get(cache_key, [])
    if not recent:
        st.caption(f"No leads for BU={bu_filter} yet.")
        return

    for row in recent:
        company = row.get("Company", "Unknown")
        score   = row.get("Opportunity Score", "").replace("/100", "")
        tab     = row.get("_tab", "")
        ts      = row.get("Timestamp", "")[:10]
        is_cold = tab == "Cold Leads"
        icon    = "❄️" if is_cold else "🔥"
        label   = f"{icon} {company[:22]}  ·  {score}  ·  {ts}"
        if st.button(label, key=f"hist_{company}_{ts}", use_container_width=True):
            st.session_state["history_view"] = row
            st.session_state["view_mode"]    = "history"
            st.rerun()


# ---------------------------------------------------------------------------
# Shared results display
# ---------------------------------------------------------------------------

def _display_results(results: list, dry: bool, query_str: str, bu: str) -> None:
    """Render summary table + result cards shared by both tracks."""
    hot     = sum(1 for r in results if r.get("verdict") == "HOT")
    warm    = sum(1 for r in results if r.get("verdict") == "WARM")
    cold    = sum(1 for r in results if r.get("verdict") == "COLD")
    written = sum(r.get("rows_written", 0) for r in results)

    st.divider()
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Prospects Found", len(results))
    m2.metric("🔥 HOT", hot)
    m3.metric("🌡️ WARM", warm)
    m4.metric("❄️ COLD", cold)
    m5.metric("Written to Sheets", "—" if dry else written)
    m6.metric("BU", bu)

    usage_summary = results[0].get("usage_summary", {}) if results else {}
    if usage_summary:
        render_usage_panel(usage_summary)

    # Discovery panel (only shown for discovery track)
    first = results[0] if results else {}
    discovery_meta = first.get("discovery_meta", {})
    if discovery_meta.get("discovery_ran"):
        with st.expander(
            f"🔍 Discovery: Exa found {len(discovery_meta.get('all_found', []))} companies "
            f"· Gemini selected {len(discovery_meta.get('selected', []))}",
            expanded=True,
        ):
            if discovery_meta.get("selected"):
                st.markdown("**✅ Selected for deep research**")
                for c in discovery_meta["selected"]:
                    li   = c.get("linkedin_url", "")
                    name = f"[{c['name']}]({li})" if li else c["name"]
                    st.markdown(
                        f"**{name}** — *{c.get('signal_type', '')}*  \n"
                        f"{c.get('reasoning', '')}"
                    )
            if discovery_meta.get("rejected"):
                st.markdown("**❌ Filtered out by Gemini**")
                for r in discovery_meta["rejected"]:
                    st.caption(f"**{r.get('name')}** — {r.get('reason', '')}")

    if not dry:
        if written > 0:
            st.success(f"✅ {written} lead(s) written to **{config.GOOGLE_SHEET_NAME}** · BU={bu}")
        else:
            st.error(
                "⚠️ No rows written to Sheets. Possible causes:\n"
                "- Dry Run Mode is still ON\n"
                "- Column count mismatch\n"
                "- All leads were duplicate domains\n"
                "- Google Sheets credentials issue\n\n"
                "Check the Pipeline Log expander below."
            )

    st.divider()
    st.subheader("Summary")

    rows = []
    for r in results:
        p = r.get("prospect", {})
        rows.append({
            "Company":  r.get("company", ""),
            "Domain":   r.get("domain", ""),
            "BU":       r.get("bu", ""),
            "Grok":     r.get("grok_score", "?"),
            "Score":    r.get("refined_score", "?"),
            "Verdict":  r.get("verdict", "?"),
            "Tab":      "❄️ Cold" if r.get("verdict") == "COLD" else "🔥 Hot",
            "Exa":      "✓" if r.get("exa_enriched") == "found" else ("~" if r.get("exa_enriched") == "ran" else "—"),
            "Apollo":   "✓" if r.get("apollo_active") and config.APOLLO_ENABLED else "—",
            "Written":  "✅" if r.get("rows_written", 0) > 0 else ("🔍" if dry else "—"),
            "Type":     p.get("opportunity_type", ""),
        })

    df = pd.DataFrame(rows)

    def _color_row(val):
        c = {"HOT": "#d4f0dc", "WARM": "#fff7d6", "COLD": "#fde8e8"}
        return f"background-color: {c.get(val, '')}"

    st.dataframe(
        df.style.map(_color_row, subset=["Verdict"]),
        width='stretch', hide_index=True,
    )

    st.divider()
    st.subheader("Lead Intelligence & Outreach")
    st.caption("Click any card to expand the full intelligence report, emails, and objection counters.")

    sort_order = {"HOT": 0, "WARM": 1, "COLD": 2}
    for i, r in enumerate(sorted(results, key=lambda x: sort_order.get(x.get("verdict", "COLD"), 2))):
        render_result_card(r, i)

    st.divider()
    with st.expander("📋 Pipeline Log (last run)", expanded=False):
        log_stream = st.session_state.get("log_stream")
        if log_stream:
            log_contents = log_stream.getvalue()
            if log_contents:
                st.code(log_contents, language=None)
            else:
                st.caption("No log output captured for this run.")
        else:
            st.caption("Log stream not initialised — refresh the page and try again.")

    st.session_state.pop("recent_leads_cache", None)
    st.session_state.pop("recent_leads_cache_ts", None)

    _append_to_history({
        "timestamp":      datetime.now().strftime("%Y-%m-%d %H:%M"),
        "query":          query_str,
        "prospect_count": len(results),
        "hot_count":      hot,
        "warm_count":     warm,
        "cold_count":     cold,
        "rows_written":   written,
        "dry_run":        dry,
        "bu":             bu,
        "companies": [
            {"Company": r.get("company", ""), "Score": r.get("refined_score", ""), "Verdict": r.get("verdict", ""), "BU": r.get("bu", "")}
            for r in results
        ],
    })


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("## 🎯 Lead Scout")
    st.caption("Accedo · Director of Strategic Accounts")
    st.divider()

    # -----------------------------------------------------------------------
    # Model selector
    # -----------------------------------------------------------------------
    with st.expander("⚙️ Model Configuration", expanded=False):
        st.caption("Select models for each pipeline stage. Changes apply to the next run.")

        def _model_selectbox(role: str, label: str) -> str:
            options = config.MODEL_OPTIONS[role]
            labels  = [o["label"] for o in options]
            key     = f"model_sel_{role}"
            if key not in st.session_state:
                st.session_state[key] = 0
            idx = st.selectbox(
                label,
                options=range(len(labels)),
                format_func=lambda i: labels[i],
                index=st.session_state[key],
                key=f"{key}_widget",
            )
            st.session_state[key] = idx
            chosen = options[idx]
            st.caption(
                f"💬 {chosen['note']}  \n"
                f"💰 ${chosen['input_cost']}/M in · ${chosen['output_cost']}/M out"
            )
            return chosen["model"]

        _model_selectbox("grok",       "🔬 Grok — Research")
        st.divider()
        _model_selectbox("gemini",     "🔍 Gemini — Discovery")
        st.divider()
        _model_selectbox("analyst",    "🧠 Claude — Analyst")
        st.divider()
        _model_selectbox("copywriter", "✉️ Claude — Copywriter")
        st.divider()
        st.markdown("**Exa — LinkedIn Intel**")
        st.caption("🔒 Fixed — no model selection  \n💰 ~$0.005 per exec search")
        st.divider()
        st.markdown("**Apollo — Contact Enrichment**")
        st.caption("🔒 Fixed — no model selection  \n💰 $0.49/credit (bulk enrich)")

    st.divider()

    # BU selector — affects both tracks
    selected_bu = st.selectbox(
        "Business Unit",
        options=config.BU_OPTIONS,
        index=config.BU_OPTIONS.index(config.BU_DEFAULT),
        help="Filters both Discovery and Account Intelligence runs. All results are tagged with this BU.",
    )
    st.session_state["selected_bu"] = selected_bu
    st.caption(f"Active BU: `{selected_bu}`")

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

    _api_status("XAI_API_KEY",           "Grok / xAI")
    _api_status("ANTHROPIC_API_KEY",     "Claude / Anthropic")
    _api_status("EXA_API_KEY",           "Exa (LinkedIn)")
    _api_status("APOLLO_MASTER_API_KEY", "Apollo Master Key (Search)")
    _api_status("APOLLO_API_KEY",        "Apollo Standard Key (Enrich)")
    _api_status("GEMINI_API_KEY",        "Gemini Flash (Discovery)")

    st.divider()
    st.markdown(f"**Run History** · BU={selected_bu}")
    render_history_sidebar(bu_filter=selected_bu)

    st.divider()
    st.markdown("**Usage History**")
    with st.expander("📊 Historical Cost Log", expanded=False):
        try:
            history = load_usage_history(max_runs=10)
            if history:
                rows = [
                    {
                        "Date":       h.get("timestamp", "")[:16],
                        "Query":      h.get("query", "")[:30],
                        "Prospects":  h.get("prospects", 0),
                        "Total $":    f"{h.get('total_cost_usd', 0):.4f}",
                        "$/prospect": f"{h.get('cost_per_prospect', 0):.4f}",
                    }
                    for h in history
                ]
                st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
            else:
                st.caption("No usage history yet.")
        except Exception as exc:
            st.caption(f"Could not load usage history: {exc}")


# ---------------------------------------------------------------------------
# Main content
# ---------------------------------------------------------------------------

st.title("Media & Entertainment Revenue Unearther GUI for Sales aka MRUGS")
st.markdown(
    "**Grok-4** live research · **Claude Sonnet** qualification · "
    "**Claude Opus** outreach · **Exa** LinkedIn intel · **Apollo** contact enrichment · "
    f"**BU: {st.session_state.get('selected_bu', config.BU_DEFAULT)}**"
)

bu = st.session_state.get("selected_bu", config.BU_DEFAULT)


# ---------------------------------------------------------------------------
# Model override helper — patches config at run time, no permanent changes
# ---------------------------------------------------------------------------

def _apply_model_overrides() -> None:
    for role, attr in [
        ("grok",       "GROK_SCOUT_MODEL"),
        ("gemini",     "GEMINI_DISCOVERY_MODEL"),
        ("analyst",    "CLAUDE_ANALYST_MODEL"),
        ("copywriter", "CLAUDE_COPYWRITER_MODEL"),
    ]:
        idx = st.session_state.get(f"model_sel_{role}", 0)
        options = config.MODEL_OPTIONS.get(role, [])
        if options and idx < len(options):
            setattr(config, attr, options[idx]["model"])


# ---------------------------------------------------------------------------
# View mode handler (history card)
# ---------------------------------------------------------------------------

def _handle_view_mode() -> bool:
    if st.session_state.get("view_mode") == "history":
        row = st.session_state.get("history_view", {})
        if row:
            col1, col2 = st.columns([1, 5])
            with col1:
                if st.button("← Back to last run"):
                    st.session_state["view_mode"] = "run"
                    st.rerun()
            with col2:
                st.caption(
                    f"Viewing historical record: **{row.get('Company', '')}** "
                    f"· {row.get('Timestamp', '')} · BU={row.get('BU', '—')}"
                )
            st.divider()
            render_history_card(row)
            return True
    return False


if _handle_view_mode():
    pass

else:
    # ---------------------------------------------------------------------------
    # Two-track tabs
    # ---------------------------------------------------------------------------
    tab_discovery, tab_accounts = st.tabs(["🔍 Discovery", "📋 Account Intelligence"])

    # -----------------------------------------------------------------------
    # DISCOVERY TAB
    # -----------------------------------------------------------------------
    with tab_discovery:
        btn_col1, btn_col2 = st.columns([3, 1])
        with btn_col2:
            suggest_btn = st.button(
                "💡 Suggest Prompt",
                use_container_width=True,
                key="suggest_btn",
                help="Pre-fill with a randomly selected lead generation prompt",
            )

        if suggest_btn:
            prompts = _load_suggested_prompts()
            st.session_state["suggested_prompt"] = random.choice(prompts)
            # Clear any previous discovery state when a new prompt is suggested
            st.session_state.pop("discovery_result", None)
            st.session_state.pop("selected_companies", None)

        query = st.text_input(
            "Discovery Scope",
            value=st.session_state.get("suggested_prompt", ""),
            placeholder="e.g., Regional sports networks migrating from ViewLift 2026...",
            help=(
                "Describe the OTT pain signal or company type to hunt for. "
                "Gemini + Exa will find companies, then you select which ones Grok researches."
            ),
            key="discovery_query",
        )

        # Clear discovery state if query changes
        if query != st.session_state.get("last_query", ""):
            st.session_state.pop("discovery_result", None)
            st.session_state.pop("selected_companies", None)
            st.session_state["last_query"] = query

        find_btn = st.button(
            "🔍 Find Companies",
            use_container_width=True,
            key="find_btn",
            disabled=not query,
        )

        # ---- Stage 0: Find companies ----
        if find_btn:
            _apply_model_overrides()
            log_stream = st.session_state.get("log_stream")
            if log_stream:
                log_stream.truncate(0)
                log_stream.seek(0)

            with st.status("🔍 Gemini + Exa finding companies…", expanded=True) as status:
                try:
                    disc = main.run_discovery(query, bu=bu)
                    st.session_state["discovery_result"] = disc
                    st.session_state.pop("selected_companies", None)
                    found = disc.get("discovery", {}).get("all_found", [])
                    status.update(
                        label=f"✅ Found {len(found)} companies — select up to 5 below",
                        state="complete",
                        expanded=False,
                    )
                except Exception as exc:
                    status.update(label="❌ Discovery error", state="error", expanded=True)
                    st.error(f"**Error:** {exc}")
                    st.exception(exc)

        # ---- Company selection UI ----
        disc_result = st.session_state.get("discovery_result")

        if disc_result:
            discovery = disc_result.get("discovery", {})
            all_found = discovery.get("all_found", [])
            gemini_scored = discovery.get("selected", [])  # Gemini's top picks
            rejected = discovery.get("rejected", [])

            if not all_found:
                st.warning(
                    "⚠️ Exa found no companies for this query. "
                    "Try rephrasing — use company category keywords rather than product format names."
                )
            else:
                st.divider()
                st.subheader(f"🔍 Exa found {len(all_found)} companies")
                st.caption(
                    f"Gemini pre-selected {len(gemini_scored)} — "
                    f"select up to 5 for Grok deep research. "
                    f"Gemini's picks are pre-checked."
                )

                # Build a lookup of Gemini's reasoning by company name
                gemini_map = {
                    c.get("name", ""): {
                        "reasoning":   c.get("reasoning", ""),
                        "signal_type": c.get("signal_type", ""),
                    }
                    for c in gemini_scored
                }
                gemini_names = set(gemini_map.keys())

                # Initialise selection state — pre-check Gemini's picks
                if "company_selections" not in st.session_state:
                    st.session_state["company_selections"] = {
                        c.get("name", ""): c.get("name", "") in gemini_names
                        for c in all_found
                    }

                selected_count = sum(st.session_state["company_selections"].values())

                # Display each company as a checkbox row
                for company in all_found:
                    name = company.get("name", "")
                    li   = company.get("linkedin_url", "")
                    g    = gemini_map.get(name, {})
                    reasoning   = g.get("reasoning", "")
                    signal_type = g.get("signal_type", "")
                    is_gemini   = name in gemini_names

                    col_check, col_info = st.columns([1, 8])
                    with col_check:
                        current = st.session_state["company_selections"].get(name, False)
                        # Disable checkbox if at limit and not already selected
                        disabled = (selected_count >= 5 and not current)
                        checked = st.checkbox(
                            "",
                            value=current,
                            key=f"sel_{name}",
                            disabled=disabled,
                            label_visibility="collapsed",
                        )
                        st.session_state["company_selections"][name] = checked
                        if checked != current:
                            st.rerun()

                    with col_info:
                        name_md = f"[{name}]({li})" if li else name
                        gemini_badge = " 🤖 Gemini pick" if is_gemini else ""
                        if reasoning:
                            st.markdown(
                                f"**{name_md}**{gemini_badge}  \n"
                                f"*{signal_type}* — {reasoning}"
                            )
                        else:
                            st.markdown(f"**{name_md}**")

                # Recount after potential rerun
                selected_count = sum(
                    v for v in st.session_state["company_selections"].values()
                )
                st.caption(f"{selected_count}/5 selected")

                if rejected:
                    with st.expander(f"❌ {len(rejected)} companies filtered out by Gemini", expanded=False):
                        for r in rejected:
                            st.caption(f"**{r.get('name')}** — {r.get('reason', '')}")

                st.divider()

                # Build the selected list
                selected_companies = [
                    c for c in all_found
                    if st.session_state["company_selections"].get(c.get("name", ""), False)
                ]

                research_btn = st.button(
                    f"🚀 Research {selected_count} Selected {'Company' if selected_count == 1 else 'Companies'}",
                    type="primary",
                    use_container_width=True,
                    key="research_btn",
                    disabled=selected_count == 0,
                )

                if research_btn:
                    _apply_model_overrides()
                    st.session_state["view_mode"] = "run"
                    log_stream = st.session_state.get("log_stream")
                    if log_stream:
                        log_stream.truncate(0)
                        log_stream.seek(0)

                    with st.status(
                        f"Grok researching {selected_count} selected {'company' if selected_count == 1 else 'companies'}…",
                        expanded=True,
                    ) as status:
                        st.write("🔍 Grok deep research waterfall running — this takes 2-3 minutes per company…")
                        try:
                            from core.sheets import SheetsClient
                            sc = SheetsClient()
                            grok_prospects = main.run_grok_only(
                                query=query,
                                bu=bu,
                                selected_companies=selected_companies,
                                run_id=disc_result.get("run_id", ""),
                                sheets=sc,
                            )
                            st.session_state["grok_prospects"] = grok_prospects
                            st.session_state["grok_run_id"]    = disc_result.get("run_id", "")
                            st.session_state["grok_discovery"] = discovery
                            st.session_state["grok_query"]     = query
                            st.session_state.pop("enrichment_selections", None)
                            status.update(
                                label=f"✅ Grok returned {len(grok_prospects)} prospect(s) — review scores below",
                                state="complete",
                                expanded=False,
                            )
                        except Exception as exc:
                            status.update(label="❌ Grok error", state="error", expanded=True)
                            st.error(f"**Error:** {exc}")
                            st.exception(exc)

        # ---- Grok results — enrichment selection UI ----
        grok_prospects = st.session_state.get("grok_prospects", [])

        if grok_prospects:
            st.divider()
            st.subheader(f"🧠 Grok returned {len(grok_prospects)} prospect(s) — select which to enrich")
            st.caption(
                "HOT and WARM leads are pre-checked. Unselected companies are archived to Cold Leads with no further processing."
            )

            if "enrichment_selections" not in st.session_state:
                st.session_state["enrichment_selections"] = {
                    p.get("name", ""): (p.get("opportunity_score") or 0) >= 50
                    for p in grok_prospects
                }

            for prospect in grok_prospects:
                name    = prospect.get("name", "")
                score   = prospect.get("opportunity_score") or 0
                verdict = "HOT" if score >= 70 else "WARM" if score >= 50 else "COLD"
                opp_type = prospect.get("opportunity_type", "")
                gap      = prospect.get("transition_gap_timer", "")

                col_check, col_score, col_info = st.columns([1, 2, 8])
                with col_check:
                    current = st.session_state["enrichment_selections"].get(name, False)
                    checked = st.checkbox(
                        "",
                        value=current,
                        key=f"enrich_{name}",
                        label_visibility="collapsed",
                    )
                    st.session_state["enrichment_selections"][name] = checked
                with col_score:
                    st.markdown(_score_bar_html(score), unsafe_allow_html=True)
                    st.markdown(_verdict_chip(verdict), unsafe_allow_html=True)
                with col_info:
                    detail = f"*{opp_type}*" if opp_type else ""
                    if gap:
                        detail += f" · {gap}"
                    st.markdown(f"**{name}**  \n{detail}" if detail else f"**{name}**")

            enrichment_count = sum(st.session_state["enrichment_selections"].values())
            st.caption(f"{enrichment_count} selected for enrichment · {len(grok_prospects) - enrichment_count} will be archived to Cold Leads")

            enrich_btn = st.button(
                f"🚀 Enrich & Draft Outreach for {enrichment_count} Selected" if enrichment_count > 0 else "⬆️ Select at least one company above",
                type="primary",
                use_container_width=True,
                key="enrich_btn",
                disabled=enrichment_count == 0,
            )

            if enrich_btn:
                _apply_model_overrides()
                enrichment_names = {
                    name for name, selected
                    in st.session_state["enrichment_selections"].items()
                    if selected
                }
                log_stream = st.session_state.get("log_stream")
                if log_stream:
                    log_stream.truncate(0)
                    log_stream.seek(0)

                with st.status(
                    f"Enriching {enrichment_count} selected prospect(s)…",
                    expanded=True,
                ) as status:
                    st.write("🔗 Apollo → Exa exec intel → Claude Sonnet → Claude Opus → Sheets…")
                    try:
                        from core.sheets import SheetsClient
                        sc = SheetsClient()
                        results = main.run_enrichment_from_selection(
                            query=st.session_state.get("grok_query", query),
                            bu=bu,
                            all_prospects=grok_prospects,
                            enrichment_names=enrichment_names,
                            run_id=st.session_state.get("grok_run_id", ""),
                            dry_run=is_dry_run,
                            discovery=st.session_state.get("grok_discovery"),
                            sheets=sc,
                        )
                        status.update(
                            label="✅ Enrichment complete!",
                            state="complete",
                            expanded=False,
                        )
                    except Exception as exc:
                        status.update(label="❌ Enrichment error", state="error", expanded=True)
                        st.error(f"**Error:** {exc}")
                        st.exception(exc)
                        results = []

                if results:
                    # Clear Grok state after successful enrichment run
                    st.session_state.pop("grok_prospects", None)
                    st.session_state.pop("enrichment_selections", None)
                    st.session_state.pop("discovery_result", None)
                    st.session_state.pop("company_selections", None)
                    _display_results(results, is_dry_run, query, bu)

        elif suggest_btn:
            pass

    # -----------------------------------------------------------------------
    # ACCOUNT INTELLIGENCE TAB
    # -----------------------------------------------------------------------
    with tab_accounts:
        st.subheader(f"Account Intelligence · BU={bu}")
        st.caption(
            "Import a prospect list or run intelligence on your tracked accounts. "
            "Discovery is skipped — Grok researches each account directly."
        )

        # ---- Import section ----
        with st.expander("📥 Import Accounts", expanded=False):
            st.caption(
                "CSV must have at minimum: **Company**, **Domain**. "
                "Optional: LinkedIn URL, Tier, Region."
            )
            uploaded = st.file_uploader(
                "Upload CSV",
                type=["csv"],
                key="account_upload",
                help="Company and Domain columns are required.",
            )

            if uploaded:
                try:
                    import io
                    df_import = pd.read_csv(io.StringIO(uploaded.read().decode("utf-8")))
                    df_import.columns = [c.strip() for c in df_import.columns]

                    required = {"Company", "Domain"}
                    missing_cols = required - set(df_import.columns)
                    if missing_cols:
                        st.error(f"Missing required columns: {', '.join(missing_cols)}")
                    else:
                        # Show coverage gap warnings
                        missing_li = df_import["LinkedIn URL"].isna().sum() if "LinkedIn URL" in df_import.columns else len(df_import)
                        if missing_li > 0:
                            st.warning(f"⚠️ {missing_li} row(s) missing LinkedIn URL — these will have reduced Exa enrichment.")

                        missing_domain = df_import["Domain"].isna().sum()
                        if missing_domain > 0:
                            st.error(f"❌ {missing_domain} row(s) missing Domain — these will be skipped.")
                            df_import = df_import.dropna(subset=["Domain"])

                        st.markdown(f"**Preview — {len(df_import)} accounts · BU={bu}**")
                        st.dataframe(df_import.head(10), hide_index=True)

                        if st.button("✅ Confirm Import", key="confirm_import"):
                            from core.sheets import SheetsClient
                            sc = SheetsClient()
                            imported = 0
                            for _, row in df_import.iterrows():
                                sc.upsert_account({
                                    "Company":      str(row.get("Company", "")),
                                    "Domain":       str(row.get("Domain", "")),
                                    "LinkedIn URL": str(row.get("LinkedIn URL", "")),
                                    "BU":           bu,
                                    "Tier":         str(row.get("Tier", "")),
                                    "Region":       str(row.get("Region", "")),
                                })
                                imported += 1
                            st.success(f"✅ {imported} account(s) imported to Accounts tab · BU={bu}")
                            # Invalidate accounts cache
                            st.session_state.pop("accounts_cache", None)
                            st.rerun()

                except Exception as exc:
                    st.error(f"Could not parse CSV: {exc}")

        # ---- Accounts table ----
        st.markdown(f"**Tracked Accounts · BU={bu}**")

        # on button click the load accounts
        acc_cache_key = f"accounts_{bu}"
        acc_cache_ts  = f"accounts_cache_ts_{bu}"
        
        col_refresh, _ = st.columns([1, 4])
        with col_refresh:
            if st.button("🔄 Load Accounts", key="load_accounts_btn"):
                try:
                    from core.sheets import SheetsClient
                    sc = SheetsClient()
                    st.session_state[acc_cache_key] = sc.get_accounts(bu_filter=bu)
                except Exception as exc:
                    st.error(f"Could not load accounts: {exc}")

        accounts = st.session_state.get(acc_cache_key, [])

        if accounts:
            acc_df = pd.DataFrame(accounts)
            # Highlight accounts never run or not run in 30+ days
            def _last_run_color(val):
                if not val:
                    return "background-color: #fde8e8"
                try:
                    from datetime import datetime as dt
                    last = dt.strptime(val, "%Y-%m-%d %H:%M UTC")
                    days = (dt.utcnow() - last).days
                    if days > 30:
                        return "background-color: #fff7d6"
                except Exception:
                    pass
                return ""
            st.dataframe(
                acc_df.style.applymap(_last_run_color, subset=["Last Run"]) if "Last Run" in acc_df.columns else acc_df,
                width='stretch', hide_index=True,
            )
        else:
            st.caption(f"No accounts found for BU={bu}. Import a CSV above to get started.")

        # ---- Run account intelligence ----
        st.divider()
        acc_run_btn = st.button(
            f"▶️ Run Account Intelligence · BU={bu}",
            type="primary",
            use_container_width=True,
            key="acc_run_btn",
            disabled=not accounts,
        )

        if acc_run_btn:
            _apply_model_overrides()
            st.session_state["view_mode"] = "run"
            log_stream = st.session_state.get("log_stream")
            if log_stream:
                log_stream.truncate(0)
                log_stream.seek(0)

            with st.status(
                f"Running account intelligence for {len(accounts)} account(s) · BU={bu}…",
                expanded=True,
            ) as status:
                st.write(f"🔍 Grok researching {len(accounts)} tracked account(s) — skipping discovery…")
                try:
                    from core.sheets import SheetsClient
                    sc = SheetsClient()
                    results = main.run_account_pipeline(
                        bu=bu,
                        dry_run=is_dry_run,
                        sheets_client=sc,
                    )
                    status.update(label="✅ Account intelligence complete!", state="complete", expanded=False)
                except Exception as exc:
                    status.update(label="❌ Pipeline error", state="error", expanded=True)
                    st.error(f"**Error:** {exc}")
                    st.exception(exc)
                    results = []

            if not results:
                st.warning("⚠️ No results returned. Check the Pipeline Log for details.")
            else:
                # Invalidate accounts cache so Last Run updates
                st.session_state.pop(acc_cache_key, None)
                _display_results(results, is_dry_run, f"[ACCOUNT] BU={bu}", bu)


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
st.divider()
st.caption(
    f"Accedo Lead Scout · "
    f"{config.GROK_SCOUT_MODEL} · "
    f"{config.GEMINI_DISCOVERY_MODEL} discovery · "
    f"{config.CLAUDE_ANALYST_MODEL} analyst · "
    f"{config.CLAUDE_COPYWRITER_MODEL} copywriter · "
    f"BU={st.session_state.get('selected_bu', config.BU_DEFAULT)} · "
    f"Last render: {datetime.now().strftime('%H:%M:%S')}"
)
