"""
tools/gemini.py — Gemini Flash client.

Single job: assemble_brief()
  Converts intake form selections into a structured research brief
  that is passed directly to Grok for discovery + research in one pass.
  Exa is no longer used in the discovery path.
"""

import logging
import os
import re
import json
from typing import List

import requests

import config
from prompts.gemini_scorer import BRIEF_ASSEMBLY_PROMPT

logger = logging.getLogger("ott_lead_gen.gemini")

GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
GEMINI_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta/models"


# ---------------------------------------------------------------------------
# Shared request helper
# ---------------------------------------------------------------------------

def _call_gemini(prompt: str, max_tokens: int = config.GEMINI_DISCOVERY_MAX_TOKENS) -> tuple:
    """Fire a single-turn Gemini request and return (text, tokens_in, tokens_out)."""
    if not GEMINI_API_KEY:
        raise ValueError(
            "GEMINI_API_KEY is not set. Add it to your Streamlit secrets or .env"
        )

    api_url = f"{GEMINI_BASE_URL}/{config.GEMINI_DISCOVERY_MODEL}:generateContent"

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": 0.4,
        },
    }

    resp = requests.post(
        f"{api_url}?key={GEMINI_API_KEY}",
        json=payload,
        timeout=30,
    )

    if resp.status_code != 200:
        logger.error(f"Gemini API error {resp.status_code}: {resp.text[:300]}")
        resp.raise_for_status()

    data = resp.json()
    raw_text = (
        data.get("candidates", [{}])[0]
        .get("content", {})
        .get("parts", [{}])[0]
        .get("text", "")
    )
    if not raw_text:
        logger.error(f"Gemini: empty response body. Full response: {data}")
        raise ValueError("Gemini returned empty content")

    usage_meta = data.get("usageMetadata", {})
    tokens_in  = usage_meta.get("promptTokenCount", 0)
    tokens_out = usage_meta.get("candidatesTokenCount", 0)

    return raw_text.strip(), tokens_in, tokens_out


def _extract_json(raw: str):
    """Strip markdown fences and parse JSON."""
    if not raw:
        raise ValueError("Empty response from Gemini")
    text = raw.strip()
    logger.debug(f"Gemini raw response: {text[:500]}")
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if brace:
        text = brace.group(0)
    if not text:
        raise ValueError(f"Could not extract JSON from: {raw[:200]}")
    return json.loads(text)


# ---------------------------------------------------------------------------
# Brief assembly — converts form selections to Grok research brief
# ---------------------------------------------------------------------------

def assemble_brief(
    verticals: List[str],
    signals: List[str],
    context: str,
    bu: str,
    usage_tracker=None,
) -> dict:
    """
    Assemble a structured Grok research brief from intake form selections.

    Returns:
        {
            "brief":         Full research brief text (passed to Grok),
            "query_summary": Short plain-English summary (shown in GUI),
            "signal_focus":  List of primary signal types selected,
        }
        On failure returns a fallback brief constructed directly from inputs.
    """
    logger.info(
        f"Gemini: assembling brief — verticals={verticals} "
        f"signals={len(signals)} bu={bu}"
    )

    prompt = BRIEF_ASSEMBLY_PROMPT.format(
        verticals=", ".join(verticals) if verticals else "Not specified",
        signals=", ".join(signals) if signals else "Not specified",
        context=context.strip() if context else "No additional context provided",
        bu=bu or "NAM",
    )

    try:
        raw, tokens_in, tokens_out = _call_gemini(prompt, max_tokens=1024)
        if usage_tracker:
            usage_tracker.record_gemini(tokens_in, tokens_out)

        result = _extract_json(raw)

        brief = result.get("brief", "").strip()
        summary = result.get("query_summary", "").strip()
        signal_focus = result.get("signal_focus", signals[:4])

        logger.info(f"Gemini brief assembled: '{summary}' ({len(brief)} chars)")
        return {
            "brief":        brief,
            "query_summary": summary,
            "signal_focus": signal_focus,
        }

    except Exception as exc:
        logger.warning(f"Gemini brief assembly failed ({exc}) — using fallback")

        # Fallback: construct a plain brief directly from selections
        fallback = (
            f"Find Tier 1 and Tier 2 {', '.join(verticals)} companies "
            f"headquartered in {bu} showing these signals: {', '.join(signals)}. "
        )
        if context:
            fallback += context
        return {
            "brief":        fallback,
            "query_summary": f"{', '.join(verticals)} — {', '.join(signals[:2])}",
            "signal_focus": signals[:4],
        }

# ---------------------------------------------------------------------------
# Query Translation (Brief -> Exa Search Strings)
# ---------------------------------------------------------------------------

def translate_query(query: str, usage_tracker=None) -> List[str]:
    """Converts the user's research brief into targeted Exa search strings."""
    prompt = f"""
    You are an expert search Boolean architect. Convert the following B2B sales research brief 
    into 3 distinct, highly targeted search queries optimized for finding recent news, 
    press releases, or job postings about companies matching the criteria.
    
    RESEARCH BRIEF:
    {query}
    
    RULES:
    1. Queries should be plain text optimized for semantic search engines (like Exa).
    2. Focus on the action/signal (e.g., "OTT platform launch", "streaming rights deal").
    3. Do not use complex boolean operators (AND/OR). Write natural semantic searches.
    
    Return ONLY a JSON object:
    {{
        "search_strings": ["query 1", "query 2", "query 3"]
    }}
    """
    
    try:
        raw, tokens_in, tokens_out = _call_gemini(prompt, max_tokens=512)
        if usage_tracker:
            usage_tracker.record_gemini(tokens_in, tokens_out)
        result = _extract_json(raw)
        
        search_strings = result.get("search_strings", [])
        logger.info(f"Gemini translated query into {len(search_strings)} search strings.")
        return search_strings
    except Exception as exc:
        logger.warning(f"Gemini query translation failed: {exc}")
        # Fallback to a basic string if Gemini fails
        return [f"{query[:50]} OTT streaming news"]


# ---------------------------------------------------------------------------
# Company Scoring (Filtering Exa Results)
# ---------------------------------------------------------------------------

def score_companies(companies: list, query: str, usage_tracker=None) -> dict:
    """Uses Gemini to select the top 5 most relevant companies from Exa's raw results."""
    prompt = f"""
    You are a B2B sales strategist. Review the following companies found via web search.
    Select up to the 5 most relevant companies that best match the original research brief.
    
    RESEARCH BRIEF:
    {query}
    
    FOUND COMPANIES:
    {json.dumps(companies, indent=2)}
    
    Return ONLY a JSON object categorizing them into 'selected' (max 5) and 'rejected':
    {{
        "selected": [
            {{"name": "Company A", "domain": "companya.com", "reasoning": "Why they fit"}}
        ],
        "rejected": [
            {{"name": "Company B", "reason": "Why they were skipped"}}
        ]
    }}
    """
    
    try:
        raw, tokens_in, tokens_out = _call_gemini(prompt, max_tokens=1024)
        if usage_tracker:
            usage_tracker.record_gemini(tokens_in, tokens_out)
        result = _extract_json(raw)
        
        # Re-attach the source URLs from the original Exa search to the selected companies
        selected = result.get("selected", [])
        for s in selected:
            match = next((c for c in companies if c.get("name", "").lower() == s.get("name", "").lower()), None)
            if match:
                s["source_url"] = match.get("source_url", "")
                s["exa_snippet"] = match.get("exa_snippet", "")
                # If Gemini hallucinated a domain, fall back to Exa's if available
                if not s.get("domain"):
                    s["domain"] = match.get("domain", "")
                    
        logger.info(f"Gemini selected {len(selected)} companies for deep enrichment.")
        return result
    except Exception as exc:
        logger.warning(f"Gemini company scoring failed: {exc}")
        # Fallback: If Gemini fails, just pass the first 5 companies through to the pipeline
        return {"selected": companies[:5], "rejected": companies[5:]}