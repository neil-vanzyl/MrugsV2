"""
tools/claude_client.py — Claude Sonnet (analyst) + Claude Opus (copywriter).

Two separate functions, two separate models, one shared Anthropic client.

The JSON parser uses a multi-strategy approach to handle the range of
output formats frontier models can produce — including partial JSON,
extra commentary, and nested code fences.
"""

import json
import logging
import re
from typing import Any

import anthropic

import config
from prompts.analyst import ANALYST_SYSTEM_PROMPT, build_analyst_prompt
from prompts.copywriter import COPYWRITER_SYSTEM_PROMPT, build_copywriter_prompt
from utils.helpers import with_retries

logger = logging.getLogger("ott_lead_gen.claude")

# Lazily initialised — avoids import-time crash if key is not yet set
_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not config.ANTHROPIC_API_KEY:
            raise ValueError(
                "ANTHROPIC_API_KEY is not set. Export it: export ANTHROPIC_API_KEY='sk-ant-...'"
            )
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


# ---------------------------------------------------------------------------
# Hardened JSON extractor
# ---------------------------------------------------------------------------

def _extract_json(raw: str) -> Any:
    """
    Multi-strategy JSON extractor. Handles the following Claude output patterns:
      1. Clean JSON (ideal case)
      2. JSON wrapped in ```json ... ``` fences
      3. JSON embedded in prose ("Here is the assessment: {...}")
      4. Partial commentary before/after the JSON object
      5. Single-quoted JSON (non-standard but occasionally produced)

    Raises json.JSONDecodeError only after all strategies are exhausted.
    """
    text = raw.strip()

    # Strategy 1: direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 2: strip ```json ... ``` fences
    fence_match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Strategy 3: extract outermost { ... } object
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    # Strategy 4: extract outermost [ ... ] array
    bracket_match = re.search(r"\[.*\]", text, re.DOTALL)
    if bracket_match:
        try:
            return json.loads(bracket_match.group(0))
        except json.JSONDecodeError:
            pass

    # Strategy 5: replace single quotes (non-standard JSON from some models)
    try:
        return json.loads(text.replace("'", '"'))
    except json.JSONDecodeError:
        pass

    logger.error(f"All JSON extraction strategies failed. Raw (first 500):\n{raw[:500]}")
    raise json.JSONDecodeError("All extraction strategies exhausted", raw, 0)


# ---------------------------------------------------------------------------
# Analyst — Claude Sonnet
# ---------------------------------------------------------------------------

@with_retries(max_attempts=3, delay=8.0, exceptions=(Exception,))
def qualify_prospect(prospect: dict, usage_tracker=None) -> dict:
    """
    Run the qualification analysis on a single Grok prospect.

    Uses Claude Sonnet for cost-effective structured reasoning.
    Produces a refined score, verdict (HOT/WARM/COLD), and copywriter brief.

    Args:
        prospect: Single prospect dict from Grok's Phase 3 output.

    Returns:
        Analyst assessment dict. Always returns — defaults to COLD on failure.
    """
    company = prospect.get("name", "unknown")
    logger.info(f"Analyst: qualifying '{company}' (Grok score: {prospect.get('opportunity_score', '?')})")

    client = _get_client()

    response = client.messages.create(
        model=config.CLAUDE_ANALYST_MODEL,
        max_tokens=config.CLAUDE_ANALYST_MAX_TOKENS,
        system=ANALYST_SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": build_analyst_prompt(prospect)}
        ],
    )

    raw = response.content[0].text
    tokens_in  = response.usage.input_tokens  if response.usage else 0
    tokens_out = response.usage.output_tokens if response.usage else 0
    logger.info(f"Analyst <<< Sonnet | {len(raw)} chars | tokens={tokens_in}in/{tokens_out}out")
    if usage_tracker is not None:
        usage_tracker.record_sonnet(input_tokens=int(tokens_in), output_tokens=int(tokens_out))
    logger.debug(f"Analyst raw response:\n{raw[:1200]}")

    try:
        result = _extract_json(raw)
    except json.JSONDecodeError:
        logger.error(
            f"Analyst PARSE FAILED for '{company}' — "
            f"Sonnet returned this instead of JSON:\n{raw[:600]}"
        )
        return {
            "refined_score": 0,
            "grok_score": prospect.get("opportunity_score", 0),
            "score_delta_reasoning": "Parse failure — manual review required",
            "verdict": "COLD",
            "top_entry_point": "",
            "transition_gap_confirmed": "",
            "key_risk_if_no_action": "",
            "copywriter_brief": "",
            "write_to_sheet": False,
            "skip_reason": "Analyst JSON parse failure",
        }

    logger.info(
        f"Analyst OK '{company}' | "
        f"grok={result.get('grok_score')} refined={result.get('refined_score')} | "
        f"verdict={result.get('verdict')} | write={result.get('write_to_sheet')} | "
        f"entry={str(result.get('top_entry_point',''))[:60]}"
    )
    logger.debug(f"Analyst full JSON:\n{json.dumps(result, indent=2)[:1500]}")
    return result


# ---------------------------------------------------------------------------
# Copywriter — Claude Opus
# ---------------------------------------------------------------------------

@with_retries(max_attempts=3, delay=8.0, exceptions=(Exception,))
def draft_outreach(prospect: dict, analyst: dict, usage_tracker=None) -> dict:
    """
    Draft two personalised outreach emails: Visionary + Operator.

    Uses Claude Opus — this is the output the Sales Director reads and sends.
    The quality delta between Opus and Sonnet is most visible in sales copy.

    Args:
        prospect: Full Grok prospect dict.
        analyst:  Assessment from qualify_prospect().

    Returns:
        Dict with "visionary_email" and "operator_email", each containing
        "subject_line" and "body". Returns empty strings on parse failure.
    """
    company = prospect.get("name", "unknown")
    logger.info(f"Copywriter: drafting outreach for '{company}' (Opus)")

    client = _get_client()

    response = client.messages.create(
        model=config.CLAUDE_COPYWRITER_MODEL,
        max_tokens=config.CLAUDE_COPYWRITER_MAX_TOKENS,
        system=COPYWRITER_SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": build_copywriter_prompt(prospect, analyst)}
        ],
    )

    raw = response.content[0].text
    tokens_in  = response.usage.input_tokens  if response.usage else 0
    tokens_out = response.usage.output_tokens if response.usage else 0
    logger.info(f"Copywriter <<< Opus | {len(raw)} chars | tokens={tokens_in}in/{tokens_out}out")
    if usage_tracker is not None:
        usage_tracker.record_opus(input_tokens=int(tokens_in), output_tokens=int(tokens_out))
    logger.debug(f"Copywriter raw response:\n{raw[:1200]}")

    try:
        result = _extract_json(raw)
    except json.JSONDecodeError:
        logger.error(
            f"Copywriter PARSE FAILED for '{company}' — "
            f"Opus returned this instead of JSON:\n{raw[:600]}"
        )
        return {
            "visionary_email": {"subject_line": "", "body": "[Draft failed — manual write required]"},
            "operator_email":  {"subject_line": "", "body": "[Draft failed — manual write required]"},
        }

    vis_subj = result.get("visionary_email", {}).get("subject_line", "")
    ops_subj = result.get("operator_email",  {}).get("subject_line", "")
    logger.info(
        f"Copywriter OK '{company}' | "
        f"vis_subj='{vis_subj[:60]}' | ops_subj='{ops_subj[:60]}'"
    )
    return result
