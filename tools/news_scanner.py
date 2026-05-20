"""
tools/news_scanner.py — Deterministic Commercial Signal Scanner
===============================================================
Reads curated trade press domains from Google Sheets and uses 
Exa to run a strict, domain-locked search for commercial triggers 
(Rights Deals, M&A, Funding, Platform Launches) in the last 90 days.
"""

import logging
from datetime import datetime, timedelta
from urllib.parse import urlparse
from typing import List

import config
from core.sheets import SheetsClient
from utils.helpers import with_retries, track_performance

logger = logging.getLogger("ott_lead_gen.news_scanner")

# ---------------------------------------------------------------------------
# Fallback Domains (If Google Sheet is empty or unreachable)
# ---------------------------------------------------------------------------
DEFAULT_TRADE_PRESS = [
    "variety.com", "deadline.com", "hollywoodreporter.com", 
    "streamtvinsider.com", "fiercevideo.com", "sportico.com", 
    "sportspromedia.com", "techcrunch.com"
]

# ---------------------------------------------------------------------------
# Exa Output Schema
# ---------------------------------------------------------------------------
NEWS_SIGNAL_SCHEMA = {
    "type": "object",
    "description": "Extract commercial OTT signals such as rights deals, mergers, funding, or app launches.",
    "properties": {
        "signals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "signal_type": {
                        "type": "string",
                        "description": "One of: Strategic Inflection, Funding Catalyst, Expansion Signal, M&A, Rights Deal"
                    },
                    "evidence": {
                        "type": "string",
                        "description": "A 1-2 sentence summary of the news, including specific dates, dollars, or OEM platforms mentioned."
                    },
                    "date": {
                        "type": "string",
                        "description": "Publication date of the news (YYYY-MM-DD)."
                    }
                },
                "required": ["signal_type", "evidence"]
            }
        }
    }
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _extract_domain(url: str) -> str:
    """Safely extracts the bare domain (e.g., 'variety.com') from a URL string."""
    if not url.startswith("http"):
        url = "http://" + url
    parsed = urlparse(url).hostname or ""
    return parsed.replace("www.", "")

def _get_trade_press_domains() -> List[str]:
    """Fetches domains from the Google Sheet, falls back to defaults."""
    try:
        sc = SheetsClient()
        sources = sc.get_press_sources()
        domains = [_extract_domain(s.get("URL", "")) for s in sources if s.get("URL")]
        # Filter out empty strings
        domains = [d for d in domains if d]
        
        if domains:
            logger.info(f"News Scanner: Loaded {len(domains)} trade press domains from Sheets.")
            return domains
    except Exception as exc:
        logger.warning(f"News Scanner: Could not load Sheets sources ({exc}). Using defaults.")
    
    return DEFAULT_TRADE_PRESS

# ---------------------------------------------------------------------------
# Core Search Logic
# ---------------------------------------------------------------------------
@with_retries(max_attempts=3, delay=5.0)
def _search_trade_press(company: str, domains: List[str]) -> List[dict]:
    """Runs a 90-day domain-locked Exa search and extracts structured signals."""
    if not config.EXA_API_KEY:
        logger.warning("News Scanner: EXA_API_KEY not set. Skipping news scan.")
        return []

    try:
        from exa_py import Exa
        exa = Exa(api_key=config.EXA_API_KEY)
    except ImportError:
        logger.error("News Scanner: exa-py not installed.")
        return []

    # Look back 90 days
    start_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%dT00:00:00.000Z")
    
    # Highly specific neural query
    query = f"{company} OTT streaming (rights deal OR launch OR acquisition OR funding OR partnership)"

    logger.debug(f"News Scanner: Searching {len(domains)} domains for {company}...")

    try:
        result = exa.search(
            query,
            type="auto",
            num_results=3,
            include_domains=domains,
            start_published_date=start_date,
            output_schema=NEWS_SIGNAL_SCHEMA