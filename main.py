"""
OTT Lead Generation Pipeline — Frontier Edition
================================================
Full pipeline steps per prospect:

  1. Grok        — autonomous research waterfall (SEC, jobs, press, app stores)
  2. Apollo      — validate/discover power map contacts, get LinkedIn URLs + emails
  3. Exa         — LinkedIn post intelligence per exec (uses Apollo's confirmed URLs)
  4. Claude Sonnet — qualification scoring (LinkedIn-aware)
  5. Claude Opus   — outreach drafting (opens with exec's own words)
  6. Google Sheets — Hot/Cold tab routing + persistence

Apollo and Exa are optional — pipeline degrades gracefully when keys are absent.

Usage:
  python main.py --query "sports broadcaster OTT launch 2025"
  python main.py --rotate
  python main.py --prospect "FuboTV" --prospect "Sling TV"
  python main.py --query "..." --dry-run
  python main.py --rotate --debug

Required env vars:
  XAI_API_KEY, ANTHROPIC_API_KEY, GOOGLE_SERVICE_ACCOUNT_JSON, GOOGLE_SHEET_NAME

Optional env vars (each module degrades gracefully if absent):
  EXA_API_KEY            — LinkedIn post intelligence
  APOLLO_MASTER_API_KEY  — Apollo People Search (zero credits)
  APOLLO_API_KEY         — Apollo Bulk Enrichment (1 credit/person)
"""

import time
from datetime import datetime, timezone

import argparse
import logging
import json
import sys
from typing import List

import config
from utils.helpers import setup_logging
from utils.usage_tracker import RunUsage
logger = logging.getLogger("ott_lead_gen.main")

from core.sheets import SheetsClient
from tools.grok import run_research_waterfall
from tools.apollo import enrich_power_map, get_contacts_from_power_map
from tools.exa import enrich_prospect_power_map
from tools.claude_client import qualify_prospect, draft_outreach
from tools.discovery import discover_companies




# ---------------------------------------------------------------------------
# Single prospect processor
# ---------------------------------------------------------------------------

def process_prospect(
    prospect: dict,
    sheets: SheetsClient,
    query: str,
    run_id: str,
    dry_run: bool = False,
    usage: "RunUsage" = None,
    exa_rejected: str = "",
    gemini_reasoning: str = "",
) -> dict:
    """
    Run the full enrichment + qualification + write pipeline for one prospect.

    Execution order:
      Apollo → Exa → Claude Sonnet → Claude Opus → Sheets

    Apollo runs first so Exa receives confirmed LinkedIn URLs.
    Exa runs before Claude so analyst and copywriter see real post content.
    """
    company = prospect.get("name", "unknown")
    domain  = prospect.get("domain", "")
    if usage:
        usage.start_prospect(company)

    logger.info(f"--- Processing Prospect: {company} ({domain}) ---")

    result = {
        "company":       company,
        "domain":        domain,
        "grok_score":    prospect.get("opportunity_score"),
        "refined_score": None,
        "verdict":       None,
        "exa_enriched":  False,
        "apollo_active": config.APOLLO_ENABLED,
        "rows_written":  0,
        "skipped":       False,
        "skip_reason":   "",
        "error":         None,
        # Full data for GUI preview
        "prospect":      prospect,
        "analyst":       {},
        "emails":        {},
    }

    # ------------------------------------------------------------------
    # Step 1 — Apollo: validate Grok's power map names + enrich contacts
    # Runs FIRST so Exa gets confirmed LinkedIn URLs, not Grok's guesses
    # ------------------------------------------------------------------
    if config.APOLLO_ENABLED:
        logger.info(f"  Step 1: Apollo Validation/Discovery...")
        if not config.APOLLO_MASTER_API_KEY or not config.APOLLO_API_KEY:
            sheets.write_log(
                run_id=run_id, query=query, company=company, domain=domain,
                step="Apollo", status="SKIPPED",
                detail="One or both API keys missing",
            )
            logger.warning("Apollo is ENABLED but one or both API keys are missing.")
        else:
            t0 = time.monotonic()
            try:
                prospect = enrich_power_map(prospect, usage_tracker=usage)
                result["prospect"] = prospect
                duration_ms = int((time.monotonic() - t0) * 1000)
                contacts_found = len(get_contacts_from_power_map(prospect))
                cur = usage._current
                sheets.write_log(
                    run_id=run_id, query=query, company=company, domain=domain,
                    step="Apollo",
                    status="OK",
                    detail=f"{contacts_found} contact(s) found",
                    credits=cur.apollo_enrich_credits if cur else 0,
                    cost_usd=cur.apollo_enrich_credits * 0.49 if cur else 0,
                    duration_ms=duration_ms,
                )
                logger.info(f"  Apollo: power map enriched for '{company}'")
            except Exception as exc:
                duration_ms = int((time.monotonic() - t0) * 1000)
                sheets.write_log(
                    run_id=run_id, query=query, company=company, domain=domain,
                    step="Apollo", status="FAILED",
                    error=str(exc), duration_ms=duration_ms,
                )
                logger.warning(f"  Apollo: enrichment failed for '{company}': {exc}")

    # ------------------------------------------------------------------
    # Step 2 — Exa: LinkedIn post intelligence per exec
    # Uses confirmed LinkedIn URLs from Apollo when available.
    # Silently skips if EXA_API_KEY is not set.
    # ------------------------------------------------------------------
    logger.info(f"  Step 2: Exa LinkedIn Intelligence...")
    t0 = time.monotonic()
    try:
        prospect = enrich_prospect_power_map(prospect, usage_tracker=usage)
        result["prospect"] = prospect
        pm     = prospect.get("power_map", {})
        vis_li = pm.get("the_visionary", {}).get("linkedin_intel", {})
        ops_li = pm.get("the_operator", {}).get("linkedin_intel", {})
        result["exa_enriched"] = bool(
            vis_li.get("linkedin_posts_found") or ops_li.get("linkedin_posts_found")
        )
        if vis_li.get("linkedin_posts_found") or ops_li.get("linkedin_posts_found"):
            result["exa_enriched"] = "found"
            exa_detail = "Posts found"
        elif vis_li or ops_li:
            result["exa_enriched"] = "ran"
            exa_detail = "Ran — no posts found in last 90 days"
        else:
            result["exa_enriched"] = False
            exa_detail = "Skipped — no exec name or key not set"
        duration_ms = int((time.monotonic() - t0) * 1000)
        cur = usage._current
        exa_credits = cur.exa_exec_searches if cur else 0
        sheets.write_log(
            run_id=run_id, query=query, company=company, domain=domain,
            step="Exa",
            status="SKIPPED" if not (vis_li or ops_li) else "OK",
            detail=exa_detail,
            credits=exa_credits,
            cost_usd=exa_credits * 0.005,
            duration_ms=duration_ms,
        )
        if result["exa_enriched"] == "found":
            logger.info(f"  Exa: LinkedIn intel found for '{company}'")
        elif result["exa_enriched"] == "ran":
            logger.info(f"  Exa: ran for '{company}' but no posts found")
        else:
            logger.debug(f"  Exa: skipped for '{company}'")
    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        sheets.write_log(
            run_id=run_id, query=query, company=company, domain=domain,
            step="Exa", status="FAILED",
            error=str(exc), duration_ms=duration_ms,
        )
        logger.warning(f"  Exa: enrichment failed for '{company}': {exc}")

    # ------------------------------------------------------------------
    # Step 3 — Qualify (Claude Sonnet)
    # Sees Apollo-validated power map + Exa LinkedIn posts
    # ------------------------------------------------------------------
    logger.info(f"  Step 3: Analyst Qualification (Sonnet)...")
    clean_prospect = json.loads(json.dumps(prospect, default=lambda o: None))
    t0 = time.monotonic()
    try:
        analyst = qualify_prospect(clean_prospect, usage_tracker=usage)
        duration_ms = int((time.monotonic() - t0) * 1000)
        result["refined_score"] = analyst.get("refined_score")
        result["verdict"]       = analyst.get("verdict")
        result["analyst"]       = analyst
        score = result["refined_score"]
        override = False
        if score is not None:
            if score >= 70:   enforced_verdict = "HOT"
            elif score >= 50: enforced_verdict = "WARM"
            else:             enforced_verdict = "COLD"
            if enforced_verdict != result["verdict"]:
                logger.warning(
                    f"  Analyst verdict override: Claude said {result['verdict']} "
                    f"but score={score} → forcing {enforced_verdict}"
                )
                result["verdict"]  = enforced_verdict
                analyst["verdict"] = enforced_verdict
                override = True
        cur = usage._current
        sheets.write_log(
            run_id=run_id, query=query, company=company, domain=domain,
            step="Sonnet",
            status="OK",
            detail=(
                f"score={result['refined_score']} verdict={result['verdict']}"
                + (" [OVERRIDE]" if override else "")
            ),
            tokens_in=cur.sonnet_input_tokens if cur else 0,
            tokens_out=cur.sonnet_output_tokens if cur else 0,
            cost_usd=(
                (cur.sonnet_input_tokens / 1e6 * 3) +
                (cur.sonnet_output_tokens / 1e6 * 15)
            ) if cur else 0,
            duration_ms=duration_ms,
        )
    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        sheets.write_log(
            run_id=run_id, query=query, company=company, domain=domain,
            step="Sonnet", status="FAILED",
            error=str(exc), duration_ms=duration_ms,
        )
        logger.error(f"  Analyst failed for '{company}': {exc}")
        result["error"] = f"Analyst: {exc}"
        return result

    verdict = result["verdict"] or "COLD"
    is_cold = verdict == "COLD"

    if is_cold:
        logger.info(
            f"  ROUTING '{company}' -> Cold Leads tab "
            f"(score: {result['refined_score']}, "
            f"reason: {analyst.get('skip_reason', 'score < 50')})"
        )
    else:
        logger.info(
            f"  ROUTING '{company}' -> Leads tab "
            f"(score: {result['refined_score']}, verdict: {verdict})"
        )

    # ------------------------------------------------------------------
    # Step 4 — Draft outreach (Claude Opus)
    # GATED: only runs for HOT and WARM. COLD gets a stub — saves ~$0.07/call.
    # ------------------------------------------------------------------
    if is_cold:
        logger.info(f"  Step 4: Skipping Opus for COLD lead '{company}'")
        sheets.write_log(
            run_id=run_id, query=query, company=company, domain=domain,
            step="Opus", status="SKIPPED",
            detail="COLD lead — outreach not drafted",
        )
        emails = {
            "visionary_email": {
                "subject_line": f"{company} — archived",
                "body": "COLD lead — no outreach drafted. Re-qualify in 90 days.",
            },
            "operator_email": {"subject_line": "", "body": ""},
        }
    else:
        logger.info(f"  Step 4: Copywriter Personalization (Opus)...")
        t0 = time.monotonic()
        try:
            emails = draft_outreach(prospect, analyst, usage_tracker=usage)
            if not isinstance(emails, dict) or "visionary_email" not in emails:
                raise ValueError("Copywriter returned prose instead of JSON")
            duration_ms = int((time.monotonic() - t0) * 1000)
            cur = usage._current
            sheets.write_log(
                run_id=run_id, query=query, company=company, domain=domain,
                step="Opus",
                status="OK",
                detail=f"subj='{emails['visionary_email'].get('subject_line','')[:60]}'",
                tokens_in=cur.opus_input_tokens if cur else 0,
                tokens_out=cur.opus_output_tokens if cur else 0,
                cost_usd=(
                    (cur.opus_input_tokens / 1e6 * 15) +
                    (cur.opus_output_tokens / 1e6 * 75)
                ) if cur else 0,
                duration_ms=duration_ms,
            )
        except Exception as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            sheets.write_log(
                run_id=run_id, query=query, company=company, domain=domain,
                step="Opus", status="FAILED",
                error=str(exc), duration_ms=duration_ms,
            )
            logger.error(f"  Copywriter failed for '{company}': {exc}")
            emails = {
                "visionary_email": {
                    "subject_line": "Strategizing for OTT",
                    "body": "AI draft failed — manual write required.",
                },
                "operator_email": {
                    "subject_line": "Technical Inquiry",
                    "body": "AI draft failed — manual write required.",
                },
            }
    result["emails"] = emails

    # ------------------------------------------------------------------
    # Step 5 — Extract Apollo contacts for Sheets columns
    # ------------------------------------------------------------------
    contacts = []
    if config.APOLLO_ENABLED:
        contacts = get_contacts_from_power_map(prospect)
        if contacts:
            logger.info(
                f"  Apollo: {len(contacts)} contact(s) ready for Sheets columns"
            )

    # ------------------------------------------------------------------
    # Step 6 — Write to Sheets (Hot or Cold tab)
    # ------------------------------------------------------------------
    t0 = time.monotonic()
    try:
        primary_contact = contacts[0] if contacts else None
        written = sheets.append_lead(
            prospect, analyst, emails,
            contact=primary_contact,
            query=query,
            is_cold=is_cold,
            exa_rejected=exa_rejected,
            gemini_reasoning=gemini_reasoning,
        )
        if written:
            result["rows_written"] = 1
        duration_ms = int((time.monotonic() - t0) * 1000)
        sheets.write_log(
            run_id=run_id, query=query, company=company, domain=domain,
            step="Sheets",
            status="OK" if written else "SKIPPED",
            detail=(
                f"Written to {'Cold Leads' if is_cold else 'Leads'}"
                if written else "Duplicate — skipped"
            ),
            duration_ms=duration_ms,
        )
    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        sheets.write_log(
            run_id=run_id, query=query, company=company, domain=domain,
            step="Sheets", status="FAILED",
            error=str(exc), duration_ms=duration_ms,
        )
        logger.error(f"  Sheets write failed for '{company}': {exc}")
        result["error"] = f"Sheets: {exc}"

    if usage:
        usage.end_prospect()
    return result



# ---------------------------------------------------------------------------
# Full pipeline run
# ---------------------------------------------------------------------------

def run_pipeline(query: str, dry_run: bool = False) -> List[dict]:
    """
    Run the full pipeline for a single discovery query.
    Returns a list of result dicts — one per prospect — for the run report and GUI.
    """
    logger.info(f"\n{'='*65}")
    logger.info(f"Pipeline: '{query}'")
    logger.info(f"{'='*65}")
    logger.info(
        f"Modules active: "
        f"Apollo={'ON' if config.APOLLO_ENABLED else 'OFF'} | "
        f"Exa={'ON' if config.EXA_ENABLED else 'OFF'}"
    )

    run_id  = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    usage   = RunUsage(query)
    sheets  = SheetsClient()
    sheets._connect()
    summary = []

    usage.start_prospect("_grok_research")

    # ------------------------------------------------------------------
    # Stage 0 — Discovery: Gemini + Exa find companies before Grok runs
    # ------------------------------------------------------------------
    logger.info("Stage 0: Company Discovery (Gemini + Exa)...")
    discovery = discover_companies(query, usage_tracker=usage, sheets=sheets, run_id=run_id,)

    rejected_companies = discovery.get("rejected", [])
    exa_rejected_str = ", ".join(
        r.get("name", "") for r in rejected_companies
    ) if rejected_companies else ""

    discovery_meta = {
        "discovery_ran":  discovery.get("discovery_ran", False),
        "gemini_ran":     discovery.get("gemini_ran", False),
        "all_found":      discovery.get("all_found", []),
        "selected":       discovery.get("selected", []),
        "rejected":       discovery.get("rejected", []),
        "search_strings": discovery.get("search_strings", []),
    }

    if discovery.get("discovery_ran") and discovery.get("selected"):
        company_names = [c.get("name", "") for c in discovery["selected"] if c.get("name")]
        gemini_reasonings = {
            c.get("name", ""): c.get("reasoning", "")
            for c in discovery["selected"]
        }
        logger.info(
            f"Discovery: passing {len(company_names)} companies to Grok — "
            f"{', '.join(company_names)}"
        )
        use_prospect_mode = True
    else:
        logger.info("Discovery: degrading to standard Grok waterfall")
        company_names = []
        gemini_reasonings = {}
        use_prospect_mode = False

    # ------------------------------------------------------------------
    # Stage 1 — Grok research waterfall
    # ------------------------------------------------------------------
    
    t0 = time.monotonic()

    try:
        if use_prospect_mode and company_names:
            # Grok researches each named company individually
            all_prospects = []
            for name in company_names:
                named_query = (
                    f"{name} OTT streaming platform 2024 2025 2026 — research their "
                    f"technology infrastructure, OEM platform strategy, SSAI/DRM "
                    f"implementation, recent acquisitions, sports rights deals, app store "
                    f"ratings, engineering job postings, and incumbent vendor. "
                    f"Return intelligence specifically about {name}."
                )
                grok_result = run_research_waterfall(named_query, usage_tracker=usage)
                all_prospects.extend(grok_result.get("prospects", []))

            prospects = all_prospects
            top_recommendation = ""
        else:
            grok_result = run_research_waterfall(query, usage_tracker=usage)
            prospects = grok_result.get("prospects", [])
            top_recommendation = grok_result.get("top_recommendation", "")

        duration_ms = int((time.monotonic() - t0) * 1000)
        cur = usage._prospects[0] if usage._prospects else None
        sheets.write_log(
            run_id=run_id, query=query, company="—", domain="—",
            step="Grok",
            status="OK",
            detail=(
                f"{len(prospects)} prospect(s) returned | "
                f"discovery={'YES' if use_prospect_mode else 'NO'}"
            ),
            tokens_in=cur.grok_input_tokens if cur else 0,
            tokens_out=cur.grok_output_tokens if cur else 0,
            duration_ms=duration_ms,
        )

    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        sheets.write_log(
            run_id=run_id, query=query, company="—", domain="—",
            step="Grok", status="FAILED",
            error=str(exc), duration_ms=duration_ms,
        )
        usage.end_prospect()
        logger.error(f"Grok waterfall failed: {exc}")
        return [{"error": str(exc), "company": "", "domain": ""}]

    usage.end_prospect()

    if not prospects:
        logger.warning("Grok returned no prospects. Try a broader query.")
        return []

    if top_recommendation:
        logger.info(f"\n★ TOP PICK: {str(top_recommendation)[:200]}\n")

    logger.info(
        f"Grok returned {len(prospects)} prospect(s) — "
        f"enriching + qualifying now\n"
    )

    # Steps 2-6 per prospect
    for i, prospect in enumerate(prospects, 1):
        company = prospect.get("name", "?")
        logger.info(f"[{i}/{len(prospects)}] {company}")

        gemini_reasoning = gemini_reasonings.get(company, "")

        result = process_prospect(
            prospect, sheets, query, run_id,
            dry_run=dry_run, usage=usage,
            exa_rejected=exa_rejected_str,
            gemini_reasoning=gemini_reasoning,
        )
        result["discovery_meta"] = discovery_meta
        summary.append(result)

    usage.finish()
    usage.save()
    usage_summary = usage.summary()
    for r in summary:
        r["usage_summary"] = usage_summary
    return summary


# ---------------------------------------------------------------------------
# Prospect-mode: deep research on named companies
# ---------------------------------------------------------------------------

def run_prospect_mode(
    company_names: List[str],
    dry_run: bool = False,
) -> List[dict]:
    """
    Run the full intelligence waterfall on specific named companies.
    Each company gets its own focused Grok query for maximum signal depth.
    """
    all_results = []
    for name in company_names:
        query = (
            f"{name} OTT streaming platform 2024 2025 — research their technology "
            f"infrastructure, OEM platform strategy, SSAI/DRM implementation, "
            f"recent acquisitions, sports rights deals, app store ratings, "
            f"engineering job postings, and incumbent vendor. "
            f"Return intelligence specifically about {name}, not similar companies."
        )
        results = run_pipeline(query, dry_run=dry_run)
        all_results.extend(results)
    return all_results


# ---------------------------------------------------------------------------
# Run report
# ---------------------------------------------------------------------------

def print_report(summary: List[dict]) -> None:
    if not summary:
        return

    logger.info(f"\n{'='*80}")
    logger.info("RUN COMPLETE")
    logger.info(f"{'='*80}")
    logger.info(
        f"{'Company':<30} {'Grok':>5} {'Score':>6} {'Verdict':>7} "
        f"{'Exa':>5} {'Written':>7} {'Status':>7}"
    )
    logger.info(f"{'-'*80}")

    total_written = 0
    for r in summary:
        if r.get("error") and not r.get("company"):
            logger.info(f"  Pipeline error: {r['error']}")
            continue

        status  = "SKIP" if r.get("skipped") else ("ERR" if r.get("error") else "OK")
        exa     = "YES" if r.get("exa_enriched") else "no"
        written = r.get("rows_written", 0)
        total_written += written

        logger.info(
            f"{r.get('company', '?')[:30]:<30} "
            f"{str(r.get('grok_score', '?')):>5} "
            f"{str(r.get('refined_score', '?')):>6} "
            f"{str(r.get('verdict', '?')):>7} "
            f"{exa:>5} "
            f"{written:>7} "
            f"{status:>7}"
        )
        if r.get("skip_reason"):
            logger.info(f"  └ {r['skip_reason']}")

    logger.info(f"{'-'*80}")
    logger.info(f"Total rows written: {total_written}")
    logger.info(
        f"Apollo: {'ENABLED' if config.APOLLO_ENABLED else 'OFF'} | "
        f"Exa: {'ON' if config.EXA_ENABLED else 'OFF (set EXA_API_KEY to enable)'}"
    )
    logger.info(f"{'='*80}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="OTT Lead Gen — Frontier Edition",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --query "sports broadcaster OTT launch 2025"
  python main.py --rotate
  python main.py --prospect "FuboTV" --prospect "Sling TV"
  python main.py --query "OTT migration 2025" --dry-run
  python main.py --rotate --debug
        """,
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--query", "-q", type=str,
                      help="Discovery scope passed to Grok's research waterfall")
    mode.add_argument("--rotate", "-r", action="store_true",
                      help="Rotate through all OTT_SIGNAL_QUERIES in config.py")
    mode.add_argument("--prospect", "-p", type=str, action="append",
                      dest="prospects", metavar="COMPANY",
                      help="Research a specific company (repeatable: -p FuboTV -p Sling)")

    parser.add_argument("--dry-run", action="store_true", default=False,
                        help="Research + qualify only — no Sheets writes")
    parser.add_argument("--debug", action="store_true", default=False,
                        help="Enable DEBUG-level logging")

    args = parser.parse_args()

    # Call setup_logging HERE — after args are parsed — so --debug works correctly.
    # The module-level logger exists already but only emits once handlers are attached.
    setup_logging(level=logging.DEBUG if args.debug else logging.INFO)

    if args.dry_run:
        logger.info("[DRY RUN — no Sheets writes]")

    # Validate required keys
    missing = []
    if not config.XAI_API_KEY:
        missing.append("XAI_API_KEY")
    if not config.ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if not config.GOOGLE_SERVICE_ACCOUNT_JSON and not args.dry_run:
        missing.append("GOOGLE_SERVICE_ACCOUNT_JSON")
    if missing:
        logger.error(f"Missing required env vars: {', '.join(missing)}")
        sys.exit(1)

    # Log optional module status at startup
    logger.info(
        f"Optional modules: "
        f"Exa={'ON' if config.EXA_ENABLED else 'OFF'} | "
        f"Apollo={'ON' if config.APOLLO_ENABLED else 'OFF'}"
    )
    if config.APOLLO_ENABLED:
        missing_apollo = []
        if not config.APOLLO_MASTER_API_KEY:
            missing_apollo.append("APOLLO_MASTER_API_KEY")
        if not config.APOLLO_API_KEY:
            missing_apollo.append("APOLLO_API_KEY")
        if missing_apollo:
            logger.warning(
                f"APOLLO_ENABLED=True but missing keys: {', '.join(missing_apollo)}. "
                "Apollo will be skipped at runtime."
            )

    all_results = []

    if args.query:
        all_results = run_pipeline(args.query, dry_run=args.dry_run)

    elif args.rotate:
        queries = config.OTT_SIGNAL_QUERIES
        logger.info(f"Rotating through {len(queries)} signal queries")
        for i, query in enumerate(queries, 1):
            logger.info(f"\n--- Query {i}/{len(queries)} ---")
            all_results.extend(run_pipeline(query, dry_run=args.dry_run))

    elif args.prospects:
        logger.info(f"Prospect mode: {args.prospects}")
        all_results = run_prospect_mode(args.prospects, dry_run=args.dry_run)

    print_report(all_results)


if __name__ == "__main__":
    main()
