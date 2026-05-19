"""
tools/discovery.py — Pre-Grok company discovery orchestration.

Pipeline:
  1. Gemini translates user query into 2-3 Exa search strings
  2. Exa searches LinkedIn company pages, targets 10+ companies
  3. If fewer than 5 found, skip Gemini scoring and return raw list
  4. Gemini creatively scores all found companies, selects top 5
  5. Returns selected companies + metadata for Grok and GUI display

This runs BEFORE Grok's deep research waterfall. The companies returned
here are passed to run_prospect_mode() in main.py instead of the broad
single-query Grok waterfall.
"""

import logging
from typing import List, Optional

import config
from tools.gemini import translate_query, score_companies

logger = logging.getLogger("ott_lead_gen.discovery")

MIN_COMPANIES_FOR_SCORING = 3  # below this, skip Gemini scoring
TARGET_COMPANY_COUNT = 10      # how many Exa aims to find


# ---------------------------------------------------------------------------
# Exa company search
# ---------------------------------------------------------------------------

def _search_companies_exa(search_strings: List[str]) -> List[dict]:
    """
    Search the open web for signal articles, press releases, and job postings
    that name companies with OTT buying intent. Extract company names from results.
    Returns deduplicated list of dicts with 'name', 'source_url', and 'exa_snippet'.
    """
    if not config.EXA_API_KEY:
        logger.warning("Discovery: EXA_API_KEY not set — skipping company search")
        return []

    try:
        from exa_py import Exa
        exa = Exa(api_key=config.EXA_API_KEY)
    except ImportError:
        logger.warning("Discovery: exa-py not installed — skipping company search")
        return []

    companies = []
    seen_names: set = set()

    for search_str in search_strings:
        try:
            logger.info(f"Discovery: Exa searching — '{search_str[:80]}'")
            results = exa.search(
                search_str,
                type="auto",
                num_results=6,
                text={"max_characters": 500},
            )

            for r in getattr(results, "results", []):
                url   = getattr(r, "url", "") or ""
                title = getattr(r, "title", "") or ""

                # Skip results that are clearly not useful
                if any(skip in url.lower() for skip in [
                    "linkedin.com/jobs", "indeed.com", "glassdoor.com",
                    "wikipedia.org", "youtube.com",
                ]):
                    continue

                raw_text = getattr(r, "text", "") or ""
                snippet  = raw_text.strip()[:500]

                # Extract company name: prefer title, fall back to domain
                name = ""
                if title:
                    # Titles like "Nexstar Launches CTV App on Roku" → "Nexstar"
                    # Titles like "Company X | About" → "Company X"
                    name = title.split("|")[0].split("–")[0].split("—")[0].strip()
                    # Trim trailing verbs that leaked in ("Nexstar Launches" is fine,
                    # but cap at 5 words to avoid full headline as name)
                    words = name.split()
                    if len(words) > 5:
                        name = ""  # too long to be a clean company name — use domain

                if not name:
                    from urllib.parse import urlparse
                    host = urlparse(url).hostname or ""
                    parts = host.replace("www.", "").split(".")
                    name = parts[0].replace("-", " ").title() if parts else ""

                if not name:
                    continue

                # Deduplicate by normalised name
                name_key = name.lower().strip()
                if name_key in seen_names:
                    continue
                seen_names.add(name_key)

                companies.append({
                    "name":        name,
                    "linkedin_url": "",          # populated by Grok later if needed
                    "source_url":  url,
                    "exa_title":   title,
                    "exa_snippet": snippet,
                })
                logger.debug(f"  Found: {name} ({url[:60]})")

        except Exception as exc:
            logger.warning(f"Discovery: Exa search failed for '{search_str[:60]}': {exc}")
            continue

    logger.info(f"Discovery: Exa found {len(companies)} unique companies from open-web signals")
    return companies


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def discover_companies(query: str, usage_tracker=None, sheets=None, run_id: str = "") -> dict:
    """
    Run the full discovery pipeline for a user query.

    Returns:
        {
            "selected":  [...],   # top 5 companies for Grok (list of dicts)
            "rejected":  [...],   # companies Gemini filtered out
            "all_found": [...],   # everything Exa found
            "search_strings": [], # Exa search strings Gemini generated
            "gemini_ran": bool,   # whether Gemini scoring ran
            "discovery_ran": bool # whether discovery ran at all
        }
    """
    empty = {
        "selected": [],
        "rejected": [],
        "all_found": [],
        "search_strings": [],
        "gemini_ran": False,
        "discovery_ran": False,
    }

    # Log debug info to confirm sheets is connected
    logger.info(f"Discovery: sheets={sheets is not None} run_id={run_id}")
    
    # Step 1 — Gemini translates query to Exa search strings
    search_strings = translate_query(query, usage_tracker=usage_tracker)

    if sheets and run_id:
        sheets.write_log(
            run_id=run_id, query=query, company="—", domain="—",
            step="Gemini",
            status="OK" if search_strings else "FAILED",
            detail=(
                f"Translated to {len(search_strings)} search string(s)"
                if search_strings
                else "Query translation returned no search strings"
            ),
        )

    if not search_strings:
        logger.warning("Discovery: no search strings from Gemini — skipping discovery")
        return empty

    # Step 2 — Exa searches LinkedIn for companies
    all_found = _search_companies_exa(search_strings)

    if not all_found:
        logger.warning("Discovery: Exa found no companies — skipping discovery")
        return {**empty, "search_strings": search_strings, "discovery_ran": True}

    # Record Exa usage
    if usage_tracker is not None:
        usage_tracker.record_exa(exec_searches=len(search_strings))

    # Step 3 — Skip Gemini scoring if too few companies found
    if len(all_found) < MIN_COMPANIES_FOR_SCORING:
        logger.info(
            f"Discovery: only {len(all_found)} companies found "
            f"(minimum {MIN_COMPANIES_FOR_SCORING} needed for Gemini scoring) — "
            f"passing all through to Grok"
        )
        return {
            "selected": all_found,
            "rejected": [],
            "all_found": all_found,
            "search_strings": search_strings,
            "gemini_ran": False,
            "discovery_ran": True,
        }

    # Step 4 — Gemini creatively scores and selects top 5
    scored = score_companies(all_found, query, usage_tracker=usage_tracker)

    return {
        "selected":       scored.get("selected", []),
        "rejected":       scored.get("rejected", []),
        "all_found":      all_found,
        "search_strings": search_strings,
        "gemini_ran":     True,
        "discovery_ran":  True,
    }