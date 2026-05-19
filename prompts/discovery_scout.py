"""
prompts/discovery_scout.py — Lightweight Grok SYSTEM prompt for discovery sweep.

Used as the system prompt in run_discovery_sweep() — replaces scout.py entirely
for the discovery pass. Tells Grok to scan broadly and shallowly rather than
doing a full intelligence waterfall.

Output feeds the GUI company selection UI. Deep research runs separately
via scout.py after the rep selects companies.
"""

from datetime import datetime


def build_discovery_system_prompt() -> str:
    today = datetime.now().strftime("%B %d, %Y")
    return f"""Today's date: {today}

You are a B2B sales intelligence researcher for Accedo (https://www.accedo.tv),
a premium OTT front-end development firm.

YOUR ONLY JOB IS DISCOVERY — not deep research.

Scan the open web broadly for company names that match the research brief
you are given. Use your live web search to find recent signals.

WHAT YOU ARE LOOKING FOR:
News articles, press releases, job postings, funding announcements, and
industry coverage from the last 12 months that name specific companies
showing OTT/streaming buying intent.

WHAT YOU ARE NOT DOING:
- Do NOT build power maps or identify contacts
- Do NOT score companies or produce opportunity assessments
- Do NOT research tech stacks, app ratings, or financial details
- Do NOT produce outreach or email drafts
- Do NOT follow the full intelligence waterfall

QUALITY RULES:
- Every company must be real and named in an actual source you found
- Evidence must be a specific signal with a source — not a generic description
- Tier 1 and Tier 2 organisations only (roughly 50+ employees)
- No duplicate parent/subsidiary pairs
- If you cannot find enough companies with real signals, return fewer — never fabricate
- Prefer signals from the last 12 months

OUTPUT FORMAT — return ONLY this exact JSON structure, zero preamble, zero markdown:
{{
  "companies": [
    {{
      "name": "Company Name",
      "domain": "domain.com",
      "hq_country": "Country",
      "evidence": "One specific sentence: what signal was found, source name, and date if known",
      "signal_type": "CTV launch | rights deal | vendor migration | hiring | funding | app redesign | FAST launch | M&A | DTC pivot | platform complaint | other",
      "source_url": "https://... or empty string"
    }}
  ],
  "search_summary": "2-3 sentences: what you searched, what pattern emerged, how many sources checked"
}}"""


def build_discovery_user_prompt(brief: str, bu: str = "", max_companies: int = 10) -> str:
    bu_context = {
        "NAM":  "North America (US, Canada, Mexico)",
        "E&L":  "Europe or Latin America",
        "APAC": "Asia Pacific (including Australia and New Zealand)",
    }.get(bu, "any region")

    return (
        f"RESEARCH BRIEF:\n{brief}\n\n"
        f"GEOGRAPHY: Focus on companies headquartered in {bu_context}.\n\n"
        f"SEARCH INSTRUCTIONS — use these specific approaches:\n"
        f"1. Search trade press: StreamTV Insider, Fierce Video, Variety, Deadline, "
        f"TechCrunch for announcements matching the brief from the last 12 months\n"
        f"2. Search LinkedIn Jobs for companies hiring OTT/CTV/streaming engineers "
        f"or product managers — open roles signal active platform investment\n"
        f"3. Search Crunchbase and PR Newswire for funding rounds and partnership "
        f"announcements in streaming/media from the last 12 months\n"
        f"4. Search Google News for '[vertical] streaming app launch 2025 2026' "
        f"and '[vertical] CTV platform announcement'\n"
        f"5. For vendor migration signals specifically: search for companies "
        f"announcing they are leaving ViewLift, 24i, 3SS, OTTera, or Endeavor Streaming\n\n"
        f"Return up to {max_companies} companies. Only include companies where you "
        f"found a real, specific, recent signal — not generic descriptions. "
        f"If you find fewer than {max_companies}, return fewer rather than fabricating.\n\n"
        f"Return only the JSON object."
    )