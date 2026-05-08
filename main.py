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





# ---------------------------------------------------------------------------
# Single prospect processor
# ---------------------------------------------------------------------------

def process_prospect(
    prospect: dict,
    sheets: SheetsClient,
    query: str,
    dry_run: bool = False,
    usage: "RunUsage" = None,
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
            logger.warning(
                "Apollo is ENABLED but one or both API keys are missing. "
                "Set APOLLO_MASTER_API_KEY and APOLLO_API_KEY in your .env"
            )
        else:
            try:
                prospect = enrich_power_map(prospect, usage_tracker=usage)
                result["prospect"] = prospect
                logger.info(f"  Apollo: power map enriched for '{company}'")
            except Exception as exc:
                logger.warning(
                    f"  Apollo: enrichment failed for '{company}' "
                    f"(continuing without it): {exc}"
                )

    # ------------------------------------------------------------------
    # Step 2 — Exa: LinkedIn post intelligence per exec
    # Uses confirmed LinkedIn URLs from Apollo when available.
    # Silently skips if EXA_API_KEY is not set.
    # ------------------------------------------------------------------
    logger.info(f"  Step 2: Exa LinkedIn Intelligence...")
    try:
        prospect = enrich_prospect_power_map(prospect, usage_tracker=usage)
        result["prospect"] = prospect

        # Check if Exa found anything useful
        pm = prospect.get("power_map", {})
        vis_li = pm.get("the_visionary", {}).get("linkedin_intel", {})
        ops_li = pm.get("the_operator", {}).get("linkedin_intel", {})
        result["exa_enriched"] = bool(
            vis_li.get("linkedin_posts_found") or
            ops_li.get("linkedin_posts_found")
        )
        if result["exa_enriched"]:
            logger.info(f"  Exa: LinkedIn intel found for '{company}'")
        else:
            logger.debug(f"  Exa: no LinkedIn posts found for '{company}'")

    except Exception as exc:
        logger.warning(
            f"  Exa: enrichment failed for '{company}' "
            f"(continuing without it): {exc}"
        )

    # ------------------------------------------------------------------
    # Step 3 — Qualify (Claude Sonnet)
    # Sees Apollo-validated power map + Exa LinkedIn posts
    # ------------------------------------------------------------------
    logger.info(f"  Step 3: Analyst Qualification (Sonnet)...")
    clean_prospect = json.loads(json.dumps(prospect, default=lambda o: None))
    try:
        analyst = qualify_prospect(clean_prospect, usage_tracker=usage)
    except Exception as exc:
        logger.error(f"  Analyst failed for '{company}': {exc}")
        result["error"] = f"Analyst: {exc}"
        return result

    result["refined_score"] = analyst.get("refined_score")
    result["verdict"]       = analyst.get("verdict")
    result["analyst"]       = analyst

    # Route based on verdict directly — not write_to_sheet alone
    # (write_to_sheet can be unreliable if Claude slightly deviates from schema)
    verdict  = analyst.get("verdict", "COLD")
    is_cold  = verdict == "COLD"

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

    if dry_run:
        logger.info(
            f"  [DRY RUN] '{company}' -> score={result['refined_score']} "
            f"verdict={verdict} exa={'YES' if result['exa_enriched'] else 'NO'} "
            f"-> {'COLD' if is_cold else 'HOT'} tab"
        )
        return result

    # ------------------------------------------------------------------
    # Step 4 — Draft outreach (Claude Opus)
    # GATED: only runs for HOT and WARM. COLD gets a stub — saves ~$0.07/call.
    # ------------------------------------------------------------------
    if is_cold:
        logger.info(
            f"  Step 4: Skipping Opus for COLD lead '{company}' "
            f"(verdict=COLD, saving ~$0.07)"
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
        try:
            emails = draft_outreach(prospect, analyst, usage_tracker=usage)
            if not isinstance(emails, dict) or "visionary_email" not in emails:
                raise ValueError("Copywriter returned prose instead of JSON")
        except Exception as exc:
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
    # Already enriched into power_map — just pull them out
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
    try:
        # Always one row per prospect — Apollo contact populates the Apollo columns
        # but does not create multiple rows. The Operator's identity is already
        # in the power map columns (Operator Name, Operator LinkedIn, etc.).
        # Using the first contact (Visionary) for the Apollo columns; if only
        # the Operator was found, that contact is used instead.
        primary_contact = contacts[0] if contacts else None

        written = sheets.append_lead(
            prospect, analyst, emails,
            contact=primary_contact,
            query=query,
            is_cold=is_cold,
        )
        if written:
            result["rows_written"] = 1

    except Exception as exc:
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

    usage   = RunUsage(query)
    usage   = RunUsage(query)
    sheets  = SheetsClient()
    summary = []

    # Step 1 — Grok research waterfall
    try:
        grok_result = run_research_waterfall(query, usage_tracker=usage)
    except Exception as exc:
        logger.error(f"Grok waterfall failed: {exc}")
        return [{"error": str(exc), "company": "", "domain": ""}]

    prospects = grok_result.get("prospects", [])

    if not prospects:
        logger.warning("Grok returned no prospects. Try a broader query.")
        if grok_result.get("research_gaps"):
            logger.info(f"Research gaps: {grok_result['research_gaps']}")
        return []

    if grok_result.get("top_recommendation"):
        logger.info(
            f"\n★ TOP PICK: {str(grok_result['top_recommendation'])[:200]}\n"
        )

    logger.info(
        f"Grok returned {len(prospects)} prospect(s) — "
        f"enriching + qualifying now\n"
    )

    # Steps 2-6 per prospect
    for i, prospect in enumerate(prospects, 1):
        company = prospect.get("name", "?")
        logger.info(f"[{i}/{len(prospects)}] {company}")
        result = process_prospect(prospect, sheets, query, dry_run=dry_run, usage=usage)
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
