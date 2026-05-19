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