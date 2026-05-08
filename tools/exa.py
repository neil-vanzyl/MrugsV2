"""
tools/exa.py — LinkedIn Executive Intelligence via Exa (SDK Edition)

Uses the official exa-py SDK with:
  - type="deep"       → multi-query synthesis for better exec post coverage
  - outputSchema      → structured JSON directly from Exa, no local classifiers
  - highlights        → compact excerpts for token efficiency
  - includeDomains    → locked to linkedin.com

Install: pip install exa-py

The outputSchema replaces the entire _classify() heuristic layer from v1.
Exa does the synthesis and returns grounded, structured intel directly.
Field-level citations come back in output.grounding automatically.

Pipeline position:
    Grok returns prospects (with exec names in power_map)
        ↓  [THIS FILE]
    enrich_prospect_power_map(prospect)
        ↓
    Claude Sonnet qualifies (sees linkedin_intel in prospect dict)
        ↓
    Claude Opus drafts pitch (opens email with exec's own words)

Cost: ~$0.001–0.005 per deep search. Two execs per prospect = ~$0.01/prospect.
Free tier: 1,000 searches/month — https://exa.ai
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional

import config
from utils.helpers import with_retries, track_performance

logger = logging.getLogger("ott_lead_gen.exa")


# ---------------------------------------------------------------------------
# SDK initialisation — lazy, so missing key doesn't crash on import
# ---------------------------------------------------------------------------

_exa_client = None
_exa_py_available = None   # None = unchecked, True/False after first attempt


def _check_exa_py_installed() -> bool:
    """Check once whether exa-py is installed and log clearly if not."""
    global _exa_py_available
    if _exa_py_available is not None:
        return _exa_py_available
    try:
        import exa_py  # noqa: F401
        _exa_py_available = True
        logger.info("Exa: exa-py SDK found ✓")
    except ImportError:
        _exa_py_available = False
        logger.error(
            "Exa: exa-py is NOT installed — LinkedIn enrichment disabled. "
            "Fix: pip install exa-py"
        )
    return _exa_py_available


def _get_client():
    global _exa_client
    if _exa_client is None:
        if not _check_exa_py_installed():
            raise ImportError("exa-py is not installed. Run: pip install exa-py")
        from exa_py import Exa
        if not config.EXA_API_KEY:
            raise ValueError(
                "EXA_API_KEY is not set. "
                "Get a free key at https://exa.ai and add it to your .env"
            )
        _exa_client = Exa(api_key=config.EXA_API_KEY)
        logger.info(f"Exa: client initialised (key: ...{config.EXA_API_KEY[-6:]})")
    return _exa_client


# ---------------------------------------------------------------------------
# Output schema — what we ask Exa to extract from LinkedIn posts
#
# Max nesting depth: 2. Max total properties: 10.
# Do NOT add citation/confidence fields — Exa returns grounding automatically.
# ---------------------------------------------------------------------------

LINKEDIN_INTEL_SCHEMA = {
    "type": "object",
    "description": (
        "Sales intelligence extracted from an OTT executive's recent LinkedIn posts. "
        "Focus on technology challenges, platform strategy, and vendor frustrations."
    ),
    "required": ["exec_name", "posts_found"],
    "properties": {
        "exec_name": {
            "type": "string",
            "description": "Full name of the executive whose posts were analysed"
        },
        "posts_found": {
            "type": "boolean",
            "description": "True if relevant LinkedIn posts were found for this exec"
        },
        "top_quote": {
            "type": "string",
            "description": (
                "The single most powerful sentence from their posts to use verbatim "
                "as a cold email opener. Must be their own words. "
                "Prioritise: OTT pain, platform challenges, technology frustrations, "
                "go-live pressure, or vendor dissatisfaction. "
                "Empty string if no relevant posts found."
            )
        },
        "top_quote_url": {
            "type": "string",
            "description": "URL of the LinkedIn post containing the top_quote"
        },
        "top_quote_date": {
            "type": "string",
            "description": "Publication date of the top_quote post (YYYY-MM-DD)"
        },
        "pain_summary": {
            "type": "string",
            "description": (
                "1-2 sentence summary of OTT/streaming pain points this exec has "
                "expressed publicly. Use their language where possible. "
                "Empty string if none found."
            )
        },
        "ambition_summary": {
            "type": "string",
            "description": (
                "1-2 sentence summary of OTT platform ambitions or launches this exec "
                "has announced. Include any timelines or OEM targets mentioned. "
                "Empty string if none found."
            )
        },
        "ott_topics_mentioned": {
            "type": "string",
            "description": (
                "Comma-separated list of OTT technologies or platforms this exec has "
                "discussed: e.g. 'Roku, Tizen, SSAI, HLS latency, DRM'. "
                "Empty string if none found."
            )
        },
        "confirmed_linkedin_url": {
            "type": "string",
            "description": (
                "The exec's LinkedIn profile URL if confirmed from post URLs. "
                "Format: https://www.linkedin.com/in/[slug]. "
                "Empty string if not determinable."
            )
        },
        "signal_type": {
            "type": "string",
            "description": (
                "Primary sales signal type from their posts. "
                "One of: 'pain' | 'ambition' | 'hiring' | 'strategy' | 'none'"
            )
        }
    }
}


# ---------------------------------------------------------------------------
# Data model — wraps Exa's structured output for clean pipeline use
# ---------------------------------------------------------------------------

@dataclass
class ExecLinkedInIntel:
    """
    Structured LinkedIn intelligence for one executive.
    Built from Exa's outputSchema response.
    Injected into prospect["power_map"][role]["linkedin_intel"] before Claude.
    """
    name: str = ""
    title: str = ""
    company: str = ""

    # From Exa outputSchema
    posts_found: bool = False
    top_quote: str = ""
    top_quote_url: str = ""
    top_quote_date: str = ""
    pain_summary: str = ""
    ambition_summary: str = ""
    ott_topics_mentioned: str = ""
    confirmed_linkedin_url: str = ""
    signal_type: str = "none"

    # Exa grounding metadata (for logging/debugging)
    grounding: list = field(default_factory=list)

    def has_useful_intel(self) -> bool:
        return self.posts_found and bool(self.top_quote or self.pain_summary)

    def to_prompt_block(self) -> dict:
        """
        Clean dict for injection into the Claude analyst and copywriter prompts.
        Structured so Claude knows exactly what to do with each field.
        """
        if not self.posts_found:
            return {
                "linkedin_posts_found": False,
                "result": "no_relevant_posts_in_last_90_days"
            }

        block: dict = {"linkedin_posts_found": True}

        if self.top_quote:
            block["TOP_QUOTE_TO_USE_AS_EMAIL_OPENER"] = self.top_quote
            block["quote_url"] = self.top_quote_url
            block["quote_date"] = self.top_quote_date
            block["INSTRUCTION"] = (
                "You MUST open this exec's email by quoting TOP_QUOTE_TO_USE_AS_EMAIL_OPENER "
                "verbatim in quotation marks, then immediately pivot to the business risk. "
                "Example format: '\"[their words]\" — [the risk that creates for them].'"
            )

        if self.pain_summary:
            block["pain_they_expressed_publicly"] = self.pain_summary

        if self.ambition_summary:
            block["ambitions_they_expressed_publicly"] = self.ambition_summary

        if self.ott_topics_mentioned:
            block["ott_topics_in_their_posts"] = self.ott_topics_mentioned

        if self.signal_type and self.signal_type != "none":
            block["primary_signal_type"] = self.signal_type

        return block


# ---------------------------------------------------------------------------
# Core Exa search
# ---------------------------------------------------------------------------

@with_retries(max_attempts=3, delay=8.0, exceptions=(Exception,))
def _fetch_linkedin_profile_posts(
    linkedin_url: str,
    exec_name: str,
    company: str,
    days_back: int = 90,
) -> Optional[object]:
    """
    URL-first strategy: when Apollo has confirmed a LinkedIn profile URL,
    fetch the profile directly via Exa get_contents() rather than searching
    for it by keyword.

    This is dramatically more reliable than keyword search because:
    - We know the exact profile URL — no ambiguity about which 'Charlie Myers'
    - get_contents() fetches the actual page text rather than indexed snippets
    - Avoids the site:linkedin.com keyword search that fails when exec doesn't
      appear prominently in Exa's LinkedIn index

    Called from enrich_exec() when exec_data["linkedin"] is populated by Apollo
    or confirmed by Grok. Falls back to keyword search if this fails.
    """
    if not linkedin_url or not linkedin_url.startswith("http"):
        return None

    try:
        exa = _get_client()
        logger.debug(f"Exa: URL-first fetch for '{exec_name}' at {linkedin_url}")

        # get_contents fetches the page directly — bypasses search index entirely
        result = exa.get_contents(
            [linkedin_url],
            highlights={
                "num_sentences": 5,
                "highlights_per_url": 3,
                "query": f"{exec_name} OTT streaming platform technology challenge"
            },
            text={"max_characters": 3000},
        )

        if result and getattr(result, "results", []):
            logger.info(
                f"Exa URL-first ✓ '{exec_name}' — "
                f"{len(result.results)} page(s) fetched from LinkedIn profile"
            )
            return result

        logger.debug(f"Exa URL-first: no content returned for {linkedin_url}")
        return None

    except Exception as exc:
        logger.debug(
            f"Exa URL-first failed for '{exec_name}' ({linkedin_url}): "
            f"{type(exc).__name__}: {exc} — falling back to keyword search"
        )
        return None


def _search_exec_linkedin(
    exec_name: str,
    company: str,
    days_back: int = 90,
) -> Optional[object]:
    """
    Search Exa for recent LinkedIn posts by a specific executive.

    Uses:
      - type="deep"      → multi-query synthesis, best for named person searches
      - outputSchema     → structured JSON response, no local parsing needed
      - includeDomains   → linkedin.com only
      - highlights       → compact excerpts alongside structured output
      - startPublishedDate → last 90 days only

    Returns the raw Exa response object, or None on failure.
    """
    exa = _get_client()

    start_date = (datetime.now() - timedelta(days=days_back)).strftime(
        "%Y-%m-%dT00:00:00.000Z"
    )

    # Semantic query describing the *content* we want, not just the person.
    # "deep" type will internally run multiple query variations and synthesise.
    query = (
        f"{exec_name} {company} OTT streaming platform technology "
        f"infrastructure challenge strategy"
    )

    logger.debug(f"Exa: deep search for '{exec_name}' at '{company}'")

    # Strategy: two-pass approach
    # Pass 1 — broad neural search finds the exec's posts (no domain lock)
    # Pass 2 — if LinkedIn URLs found, fetch them via get_contents for full text
    # This avoids the deep+includeDomains conflict documented in Exa's troubleshooting.
    
    logger.debug(f"Exa: pass 1 — neural search for '{exec_name}' posts")
    
    try:
        # Pass 1: find posts — type="auto" with LinkedIn bias in query
        result = exa.search(
            f"site:linkedin.com {query}",
            type="auto",
            num_results=5,
            start_published_date=start_date,
            output_schema=LINKEDIN_INTEL_SCHEMA,
            contents={
                "highlights": {
                    "max_characters": 2000,
                    "query": f"{exec_name} OTT streaming challenge strategy platform"
                }
            },
        )
        logger.debug(f"Exa: pass 1 complete — {len(getattr(result, 'results', []))} results")
        return result
        
    except TypeError as exc:
        # output_schema not supported in this SDK version — fall back without it
        logger.warning(
            f"Exa: output_schema rejected by SDK ({exc}) — "
            f"falling back to highlights-only mode"
        )
        result = exa.search(
            f"site:linkedin.com {query}",
            type="auto",
            num_results=5,
            start_published_date=start_date,
            contents={
                "highlights": {
                    "max_characters": 2000,
                    "query": f"{exec_name} OTT streaming challenge strategy platform"
                }
            },
        )
        return result


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------

def _parse_exa_response(
    response,
    exec_name: str,
    exec_title: str,
    company: str,
) -> ExecLinkedInIntel:
    """
    Parse Exa's structured output response into an ExecLinkedInIntel object.

    Exa returns:
      response.output.content  → structured JSON matching LINKEDIN_INTEL_SCHEMA
      response.output.grounding → field-level citations (for debugging)
      response.results          → raw search result list (fallback)
    """
    intel = ExecLinkedInIntel(name=exec_name, title=exec_title, company=company)

    # Primary path: structured output from outputSchema
    output = getattr(response, "output", None)
    if output and hasattr(output, "content") and output.content:
        content = output.content

        # content may be a dict or a Pydantic model depending on SDK version
        if hasattr(content, "__dict__"):
            content = vars(content)
        elif not isinstance(content, dict):
            try:
                import json
                content = json.loads(str(content))
            except Exception:
                content = {}

        intel.posts_found = bool(content.get("posts_found", False))
        intel.top_quote = (content.get("top_quote") or "").strip()
        intel.top_quote_url = (content.get("top_quote_url") or "").strip()
        intel.top_quote_date = (content.get("top_quote_date") or "")[:10]
        intel.pain_summary = (content.get("pain_summary") or "").strip()
        intel.ambition_summary = (content.get("ambition_summary") or "").strip()
        intel.ott_topics_mentioned = (content.get("ott_topics_mentioned") or "").strip()
        intel.confirmed_linkedin_url = (content.get("confirmed_linkedin_url") or "").strip()
        intel.signal_type = (content.get("signal_type") or "none").strip()

        # Store grounding for debugging
        grounding = getattr(output, "grounding", None)
        intel.grounding = grounding if isinstance(grounding, list) else []

        return intel

    # Fallback: no structured output — check if raw results exist
    results = getattr(response, "results", []) or []
    if results:
        intel.posts_found = True
        # Pull the best highlight as a fallback quote
        for r in results:
            highlights = getattr(r, "highlights", []) or []
            if highlights:
                intel.top_quote = highlights[0][:200] if isinstance(highlights[0], str) else ""
                intel.top_quote_url = getattr(r, "url", "")
                pub_date = getattr(r, "published_date", "") or ""
                intel.top_quote_date = pub_date[:10]
                intel.signal_type = "strategy"
                break

    return intel


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def enrich_exec(exec_name: str, exec_title: str, company: str, confirmed_linkedin_url: str = "") -> ExecLinkedInIntel:
    """
    Fetch and structure LinkedIn post intelligence for one executive.

    Gracefully returns empty intel when:
      - EXA_API_KEY is not set
      - exa-py is not installed
      - No relevant posts found in last 90 days
      - Any network/API error

    Never raises — Exa enrichment is additive, not critical path.

    Args:
        exec_name:  Full name, e.g. "Jane Smith"
        exec_title: Current title for context, e.g. "CTO"
        company:    Company name for search disambiguation

    Returns:
        ExecLinkedInIntel — check .has_useful_intel() before using in prompts
    """
    intel = ExecLinkedInIntel(name=exec_name, title=exec_title, company=company)

    if not exec_name or not exec_name.strip():
        return intel

    if not config.EXA_API_KEY:
        logger.debug(
            "Exa: EXA_API_KEY not set — skipping LinkedIn enrichment "
            "(pipeline continues without it)"
        )
        return intel

    try:
        response = None

        # Strategy 1: URL-first — if we have a confirmed LinkedIn URL from
        # Apollo or Grok, fetch the profile directly (no keyword guessing)
        if confirmed_linkedin_url:
            logger.debug(
                f"Exa: trying URL-first fetch for '{exec_name}' "
                f"using confirmed URL: {confirmed_linkedin_url}"
            )
            response = _fetch_linkedin_profile_posts(
                confirmed_linkedin_url, exec_name, company
            )
            if response and getattr(response, "results", []):
                logger.info(
                    f"Exa: URL-first succeeded for '{exec_name}' — "
                    "skipping keyword search"
                )
            else:
                logger.debug(
                    f"Exa: URL-first returned no content for '{exec_name}' "
                    "— falling through to keyword search"
                )
                response = None

        # Strategy 2: Keyword search (fallback when no URL or URL-first failed)
        if response is None:
            response = _search_exec_linkedin(exec_name, company)

        if response is None:
            return intel

        intel = _parse_exa_response(response, exec_name, exec_title, company)

    except ImportError as exc:
        logger.warning(f"Exa: {exc}")
        return intel
    except ValueError as exc:
        logger.warning(f"Exa config error: {exc}")
        return intel
    except Exception as exc:
        logger.warning(
            f"Exa: search failed for '{exec_name}' at '{company}': "
            f"{type(exc).__name__}: {exc}"
        )
        return intel

    if intel.has_useful_intel():
        logger.info(
            f"Exa ✓  '{exec_name}' @ {company} | "
            f"signal={intel.signal_type} | "
            f"quote={'YES' if intel.top_quote else 'none'} | "
            f"pain={'YES' if intel.pain_summary else 'none'} | "
            f"topics={intel.ott_topics_mentioned[:40] or 'none'}"
        )
    else:
        logger.info(
            f"Exa —  '{exec_name}' @ {company} | "
            f"posts_found={intel.posts_found} | no actionable intel"
        )

    return intel

@track_performance("exa")
def enrich_prospect_power_map(prospect: dict, usage_tracker=None) -> dict:
    """
    Run Exa LinkedIn enrichment for Visionary and Operator in a prospect's power map.
    Injects ExecLinkedInIntel.to_prompt_block() into each exec's data.
    Modifies prospect in place and returns it.

    Called from main.py AFTER Grok returns and BEFORE Claude qualifies.

    Args:
        prospect: Grok prospect dict (modified in place)

    Returns:
        Same prospect dict with power_map entries updated
    """
    # Log why Exa is skipping so it's always visible in the run output
    if not config.EXA_API_KEY:
        logger.warning(
            "Exa: EXA_API_KEY not found in environment — LinkedIn enrichment skipped. "
            "Add EXA_API_KEY to your .env file."
        )
        return prospect

    if not _check_exa_py_installed():
        logger.warning(
            "Exa: exa-py package not installed — LinkedIn enrichment skipped. "
            "Run: pip install exa-py"
        )
        return prospect

    company = prospect.get("name", "")
    power_map = prospect.get("power_map", {})

    for role_key, role_label in [
        ("the_visionary", "Visionary"),
        ("the_operator", "Operator"),
    ]:
        exec_data = power_map.get(role_key, {})
        if not isinstance(exec_data, dict):
            continue

        exec_name = (exec_data.get("name") or "").strip()
        exec_title = (exec_data.get("title") or "").strip()

        if not exec_name:
            logger.debug(f"Exa: no name for {role_label} at '{company}' — skipping")
            continue

        # Pass confirmed LinkedIn URL so enrich_exec can try URL-first fetching
        # Priority: Apollo-confirmed URL > Grok-returned URL > empty (keyword search)
        confirmed_url = (
            exec_data.get("linkedin") or   # could be from Apollo or Grok
            exec_data.get("confirmed_linkedin_url") or
            ""
        ).strip()

        intel = enrich_exec(exec_name, exec_title, company, confirmed_linkedin_url=confirmed_url)

        # Always write the prompt block — even empty intel is useful signal
        exec_data["linkedin_intel"] = intel.to_prompt_block()

        # Surface confirmed LinkedIn URL if Grok didn't find one
        if intel.confirmed_linkedin_url and not exec_data.get("linkedin"):
            exec_data["linkedin"] = intel.confirmed_linkedin_url
            exec_data["verified"] = True

        # Surface top quote into public_quote if Grok's is empty
        # Keeps the rest of the pipeline (Sheets column) populated
        if intel.top_quote and not exec_data.get("public_quote"):
            exec_data["public_quote"] = intel.top_quote
            exec_data["quote_source"] = intel.top_quote_url

    # Record Exa credit spend for this prospect
    if usage_tracker is not None:
        pm = prospect.get("power_map", {})
        exec_count = sum(
            1 for rk in ["the_visionary", "the_operator"]
            if (pm.get(rk) or {}).get("name", "").strip()
        )
        usage_tracker.record_exa(exec_searches=exec_count)

    return prospect
