"""
OTT Lead Generation Pipeline — Deterministic & Concurrent Edition
================================================================
A high-speed, hallucination-free pipeline that decouples discovery from research.

Three pipeline tracks:
  1. DISCOVERY — Uses Gemini + Exa to find domains, then enriches.
  2. CSV/LIST  — Bypasses discovery; directly enriches a provided list of domains.
  3. ACCOUNTS  — Reads tracked accounts from Google Sheets and checks for new signals.

All tracks feed into a Unified Enrichment Engine that executes data gathering
concurrently, scores deterministically with Claude Sonnet, drafts with Opus,
and writes to Google Sheets.
"""

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import List, Dict

import config
from core.sheets import SheetsClient
from tools.claude_client import qualify_prospect, draft_outreach
from tools.exa import enrich_prospect_power_map
from tools.apollo import enrich_power_map, get_contacts_from_power_map
from tools.discovery import discover_companies
from utils.helpers import setup_logging
from utils.usage_tracker import RunUsage

logger = logging.getLogger("ott_lead_gen.main")

# ---------------------------------------------------------------------------
# The Unified Enrichment Engine (Processes ONE Prospect)
# ---------------------------------------------------------------------------

def process_prospect_task(
    prospect: dict,
    sheets: SheetsClient,
    query: str,
    run_id: str,
    dry_run: bool = False,
    usage: RunUsage = None,
    bu: str = "",
    track_name: str = "discovery"
) -> dict:
    """
    The deterministic gauntlet for a single prospect.
    Executes Apollo -> Exa -> Data APIs -> Claude Sonnet -> Claude Opus -> Sheets.
    """
    company = prospect.get("name", "Unknown")
    domain = prospect.get("domain", "")
    
    if usage:
        usage.start_prospect(company)

    logger.info(f"--- [START] {company} ({domain}) | Track: {track_name} ---")

    result = {
        "company": company,
        "domain": domain,
        "refined_score": None,
        "verdict": None,
        "exa_enriched": False,
        "apollo_active": config.APOLLO_ENABLED,
        "rows_written": 0,
        "skipped": False,
        "error": None,
        "bu": bu,
        "prospect": prospect,
        "analyst": {},
        "emails": {},
        "track": track_name
    }

    try:
        # STEP 1: Apollo Validation (Deterministic Contacts)
        if config.APOLLO_ENABLED:
            logger.info(f"  [{company}] Running Apollo...")
            prospect = enrich_power_map(prospect, usage_tracker=usage)

        # STEP 2: Exa Executive Intel (Deterministic LinkedIn Quotes)
        logger.info(f"  [{company}] Running Exa LinkedIn Intel...")
        prospect = enrich_prospect_power_map(prospect, usage_tracker=usage)
        
        # STEP 2.5: Deterministic Data Gathering
        from tools.technographics import get_technographic_footprint
        from tools.app_store import get_app_store_velocity
        from tools.news_scanner import get_commercial_signals
        
        prospect = get_technographic_footprint(prospect)
        prospect = get_app_store_velocity(prospect)
        prospect = get_commercial_signals(prospect)

        # STEP 3: Claude Sonnet Qualification (Synthesis & Scoring)
        logger.info(f"  [{company}] Running Sonnet Qualification...")
        analyst = qualify_prospect(prospect, usage_tracker=usage)
        result["analyst"] = analyst
        result["refined_score"] = analyst.get("refined_score", 0)
        result["verdict"] = analyst.get("verdict", "COLD")
        
        is_cold = result["verdict"] == "COLD"
        
        if is_cold:
            logger.info(f"  [{company}] ROUTING to Cold Leads (Score: {result['refined_score']})")
            emails = {
                "visionary_email": {"subject_line": f"{company} — archived", "body": "COLD lead."},
                "operator_email": {"subject_line": "", "body": ""}
            }
        else:
            # STEP 4: Claude Opus Outreach (For HOT/WARM only)
            logger.info(f"  [{company}] Running Opus Copywriter...")
            emails = draft_outreach(prospect, analyst, usage_tracker=usage)
        
        result["emails"] = emails

        # STEP 5: Sheets Persistence
        logger.info(f"  [{company}] Writing to Sheets...")
        contacts = get_contacts_from_power_map(prospect)
        primary_contact = contacts[0] if contacts else None
        
        if not dry_run:
            written = sheets.append_lead(
                prospect=prospect,
                analyst=analyst,
                emails=emails,
                contact=primary_contact,
                query=query,
                is_cold=is_cold,
                bu=bu,
            )
            if written:
                result["rows_written"] = 1

    except Exception as exc:
        logger.error(f"  [{company}] PIPELINE FAILED: {exc}")
        result["error"] = str(exc)

    if usage:
        usage.end_prospect()
        
    logger.info(f"--- [END] {company} | Verdict: {result['verdict']} ---")
    return result


# ---------------------------------------------------------------------------
# The Parallel Orchestrator
# ---------------------------------------------------------------------------

def run_unified_pipeline(
    prospects: List[Dict],
    query: str,
    bu: str,
    track_name: str,
    dry_run: bool = False,
    usage: RunUsage = None,
) -> List[dict]:
    """
    Takes a list of domains and processes them CONCURRENTLY.
    This drops a 10-minute wait time down to roughly 60-90 seconds.
    """
    if not prospects:
        logger.warning(f"No prospects provided for {track_name} track.")
        return []

    sheets = SheetsClient()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    usage = usage or RunUsage(query)
    
    results = []
    
    logger.info(f"\n{'='*65}\nStarting Concurrent Enrichment ({len(prospects)} targets) | BU={bu}\n{'='*65}")

    # Process up to 5 companies concurrently to respect API rate limits
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_prospect = {
            executor.submit(
                process_prospect_task, p, sheets, query, run_id, dry_run, usage, bu, track_name
            ): p for p in prospects
        }
        
        for future in as_completed(future_to_prospect):
            try:
                res = future.result()
                results.append(res)
            except Exception as exc:
                p = future_to_prospect[future]
                logger.error(f"Fatal error processing {p.get('name')}: {exc}")
                results.append({"company": p.get("name"), "error": str(exc), "track": track_name})

    usage.finish()
    usage.save()
    
    usage_summary = usage.summary()
    for r in results:
        r["usage_summary"] = usage_summary

    return results

# ---------------------------------------------------------------------------
# Track 1: Discovery (The GUI Broad Search)
# ---------------------------------------------------------------------------

def run_discovery_track(query: str, bu: str, dry_run: bool = False) -> List[dict]:
    logger.info(f"TRACK 1: Discovery | Query: '{query}' | BU: {bu}")
    usage = RunUsage(query)
    
    # 1. Use Gemini/Exa to find the domains (bypassing Grok entirely)
    discovery_data = discover_companies(query, usage_tracker=usage)
    selected_prospects = discovery_data.get("selected", [])
    
    if not selected_prospects:
        logger.warning("Discovery found no qualified companies.")
        return []

    # 2. Feed domains into the Unified Pipeline
    return run_unified_pipeline(
        prospects=selected_prospects, 
        query=query, 
        bu=bu, 
        track_name="discovery", 
        dry_run=dry_run, 
        usage=usage
    )

# ---------------------------------------------------------------------------
# Track 2: CSV / Prospect Upload (The Event List)
# ---------------------------------------------------------------------------

def run_list_track(prospects: List[dict], bu: str, dry_run: bool = False) -> List[dict]:
    logger.info(f"TRACK 2: List Upload | {len(prospects)} targets | BU: {bu}")
    # Ensures basic structure is met before enrichment
    formatted_prospects = [{"name": p.get("name", ""), "domain": p.get("domain", "")} for p in prospects]
    
    return run_unified_pipeline(
        prospects=formatted_prospects, 
        query="CSV List Upload", 
        bu=bu, 
        track_name="csv_list", 
        dry_run=dry_run
    )

# ---------------------------------------------------------------------------
# Track 3: Accounts Check (The Nurture List)
# ---------------------------------------------------------------------------

def run_accounts_track(bu: str, dry_run: bool = False) -> List[dict]:
    logger.info(f"TRACK 3: Accounts Check | BU: {bu}")
    sheets = SheetsClient()
    accounts = sheets.get_accounts(bu_filter=bu)
    
    if not accounts:
        logger.warning(f"No accounts found in tracker for BU='{bu}'")
        return []
        
    formatted_prospects = [
        {"name": a.get("Company", ""), "domain": a.get("Domain", "")} 
        for a in accounts if a.get("Company") and a.get("Domain")
    ]
    
    results = run_unified_pipeline(
        prospects=formatted_prospects, 
        query=f"Account Health Check (BU={bu})", 
        bu=bu, 
        track_name="accounts", 
        dry_run=dry_run
    )
    
    # Update Last Run timestamp on the tracked accounts
    if not dry_run:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        for p in formatted_prospects:
            sheets.update_account_last_run(p["domain"], ts)
            
    return results

# ---------------------------------------------------------------------------
# CLI & Execution
# ---------------------------------------------------------------------------

def print_report(summary: List[dict]) -> None:
    if not summary:
        return
    logger.info(f"\n{'='*80}\nRUN COMPLETE\n{'='*80}")
    logger.info(f"{'Company':<30} {'Score':>6} {'Verdict':>7} {'Written':>7} {'Track':>10}")
    logger.info(f"{'-'*80}")
    
    total_written = 0
    for r in summary:
        if r.get("error") and not r.get("company"):
            logger.info(f"  Pipeline error: {r['error']}")
            continue
        written = r.get("rows_written", 0)
        total_written += written
        logger.info(
            f"{str(r.get('company', '?'))[:30]:<30} "
            f"{str(r.get('refined_score', '?')):>6} "
            f"{str(r.get('verdict', '?')):>7} "
            f"{written:>7} "
            f"{str(r.get('track', '')):>10}"
        )
    logger.info(f"{'-'*80}\nTotal rows written: {total_written}\n{'='*80}\n")

def main() -> None:
    parser = argparse.ArgumentParser(description="OTT Lead Gen — Deterministic Engine")

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--query", "-q", type=str, help="Run Track 1: Discovery")
    mode.add_argument("--accounts", "-a", action="store_true", help="Run Track 3: Accounts Check")
    # A lightweight simulation argument for Track 2 (CSV via CLI)
    mode.add_argument("--prospect", "-p", type=str, action="append", nargs=2, metavar=('NAME', 'DOMAIN'), help="Run Track 2: List Enrichment")

    parser.add_argument("--bu", type=str, default=config.BU_DEFAULT, choices=config.BU_OPTIONS)
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--debug", action="store_true", default=False)

    args = parser.parse_args()
    setup_logging(level=logging.DEBUG if args.debug else logging.INFO)

    all_results = []

    if args.query:
        all_results = run_discovery_track(args.query, bu=args.bu, dry_run=args.dry_run)
    elif args.accounts:
        all_results = run_accounts_track(bu=args.bu, dry_run=args.dry_run)
    elif args.prospect:
        # Convert the CLI pairs into the expected dict structure for Track 2
        prospect_list = [{"name": name, "domain": domain} for name, domain in args.prospect]
        all_results = run_list_track(prospect_list, bu=args.bu, dry_run=args.dry_run)

    print_report(all_results)

if __name__ == "__main__":
    main()