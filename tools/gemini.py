"""
tools/gemini.py — Gemini Flash client.

Two jobs:
  1. translate_query()  — converts user natural language query into 2-3
                          optimised Exa LinkedIn company search strings
  2. score_companies()  — creatively scores discovered companies, selects
                          top 5 most likely to need Accedo, returns reasoning
"""

import json
import logging
import os
import re
from typing import List

import requests

import config
from prompts.gemini_scorer import TRANSLATE_PROMPT, SCORE_PROMPT

logger = logging.getLogger("ott_lead_gen.gemini")

GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
GEMINI_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta/models"


# ---------------------------------------------------------------------------
# Shared request helper
# ---------------------------------------------------------------------------

def _call_gemini(prompt: str, max_tokens: int = config.GEMINI_DISCOVERY_MAX_TOKENS) -> tuple:
    """Fire a single-turn Gemini request and return the text response."""
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
    raw_text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
    if not raw_text:
        logger.error(f"Gemini: empty response body. Full response: {data}")
        raise ValueError("Gemini returned empty content")

    # Extract token counts from response
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
    # Strip markdown fences
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # Extract outermost JSON object
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if brace:
        text = brace.group(0)
    if not text:
        raise ValueError(f"Could not extract JSON from: {raw[:200]}")
    return json.loads(text)


# ---------------------------------------------------------------------------
# Job 1 — Query translation
# ---------------------------------------------------------------------------

def translate_query(query: str, usage_tracker=None) -> List[str]:
    """
    Convert a natural language query into optimised Exa search strings.
    Returns list of search strings, or empty list on failure.
    """
    logger.info("Gemini: translating query to Exa search strings")
    prompt = TRANSLATE_PROMPT.format(query=query)

    try:
        raw, tokens_in, tokens_out = _call_gemini(prompt, max_tokens=2048)
        if usage_tracker:
            usage_tracker.record_gemini(tokens_in, tokens_out)
        result = _extract_json(raw)
        strings = result.get("search_strings", [])
        logger.info(
            f"Gemini: translated to {len(strings)} search string(s) — "
            f"{result.get('reasoning', '')}"
        )
        return strings
    except Exception as exc:
        logger.warning(f"Gemini: query translation failed ({exc}) — skipping discovery")
        return []


# ---------------------------------------------------------------------------
# Job 2 — Creative company scoring
# ---------------------------------------------------------------------------

def score_companies(companies: List[dict], query: str, usage_tracker=None) -> dict:
    """
    Creatively score discovered companies and select top 5 for Grok research.

    Args:
        companies: List of dicts with 'name' and 'linkedin_url' keys
        query:     Original user query for context

    Returns:
        Dict with 'selected' (top 5) and 'rejected' lists, each with reasoning
    """
    if not companies:
        return {"selected": [], "rejected": []}

    logger.info(f"Gemini: scoring {len(companies)} discovered companies")

    company_list = "\n".join(
        f"- {c.get('name', 'Unknown')} ({c.get('linkedin_url', 'no URL')})"
        for c in companies
    )

    prompt = SCORE_PROMPT.format(companies=company_list, query=query)

    try:
        raw, tokens_in, tokens_out = _call_gemini(prompt, max_tokens=2048)
        if usage_tracker:
            usage_tracker.record_gemini(tokens_in, tokens_out)
        
        result = _extract_json(raw)
        selected = result.get("selected", [])[:5]
        rejected = result.get("rejected", [])
        
        logger.info(
            f"Gemini: selected {len(selected)} companies for deep research, "
            f"rejected {len(rejected)}"
        )
        for s in selected:
            logger.info(
                f"  ✓ {s.get('name')} — {s.get('signal_type')} — "
                f"{s.get('reasoning', '')[:80]}"
            )
        for r in rejected:
            logger.info(f"  ✗ {r.get('name')} — {r.get('reason', '')[:60]}")

        return {"selected": selected, "rejected": rejected}

    except Exception as exc:
        logger.warning(f"Gemini: company scoring failed ({exc})")
        return {
            "selected": [
                {
                    "name": c["name"],
                    "linkedin_url": c.get("linkedin_url", ""),
                    "reasoning": "Gemini scoring unavailable",
                    "signal_type": "unknown",
                }
                for c in companies[:5]
            ],
            "rejected": [],
        }