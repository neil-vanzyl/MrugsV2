"""
prompts/discovery_scout.py — Lightweight Grok discovery prompt.

Single job: given a research brief, find 8-10 real companies with
one-line evidence each. Fast and cheap — no scoring, no power map,
no schema overhead. Output feeds the GUI company selection UI.

Full scout.py research runs AFTER the rep selects companies.
"""

from datetime import datetime


def build_discovery_prompt(brief: str, bu: str = "", max_companies: int = 10) -> str:
    today = datetime.now().strftime("%B %d, %Y")

    bu_context = {
        "NAM":  "North America (US, Canada, Mexico)",
        "E&L":  "Europe or Latin America",
        "APAC": "Asia Pacific (including Australia and New Zealand)",
    }.get(bu, "any region")

    return f"""Today's date: {today}

You are a B2B sales intelligence researcher for Accedo (https://www.accedo.tv),
a premium OTT front-end development firm.

YOUR ONLY JOB: Find {max_companies} real companies that match the research brief below.
Use your live web search. Return company names with ONE LINE of evidence each.

DO NOT score. DO NOT build a power map. DO NOT produce a full prospect profile.
This is a fast discovery sweep — deep research runs separately on companies the rep selects.

RESEARCH BRIEF:
{brief}

GEOGRAPHY: Focus on companies headquartered in {bu_context}.

QUALITY RULES:
- Every company must be real and verifiable — no hallucinations
- Evidence must be a specific recent signal (press release, job posting, announcement, funding)
  not a generic description of what the company does
- Prefer signals from the last 12 months
- Tier 1 and Tier 2 organisations only — no startups under ~50 employees
- No duplicate parent/subsidiary pairs — pick the entity Accedo would actually sell to
- If you cannot find {max_companies} companies with real signals, return fewer — quality over quantity

Return ONLY a valid JSON object, zero preamble, zero markdown fences:
{{
  "companies": [
    {{
      "name": "Company Name",
      "domain": "companydomain.com",
      "hq_country": "Country",
      "evidence": "One specific sentence: what signal was found and where (source + date if known)",
      "signal_type": "CTV launch | rights deal | vendor migration | hiring | funding | app redesign | FAST launch | M&A | DTC pivot | platform complaint | other",
      "source_url": "https://... or empty string if not available"
    }}
  ],
  "search_summary": "2-3 sentences on what you searched and what pattern you found"
}}"""