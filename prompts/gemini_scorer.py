"""
prompts/gemini_scorer.py — Gemini's single job: assemble a structured
research brief from the intake form selections.

The brief is passed directly to Grok, which does its own live web search
to find and research companies. Exa is no longer used in the discovery path.
"""

# ---------------------------------------------------------------------------
# Brief assembly prompt
# ---------------------------------------------------------------------------

BRIEF_ASSEMBLY_PROMPT = """
You are a Senior Sales Intelligence Researcher at Accedo (https://www.accedo.tv),
a premium OTT front-end development firm. You have 12 years of experience closing
deals with NBC Sports, FloSports, Spark Sport, SonyLIV, MasterClass, and dozens of others.

Your job is to take a sales rep's intake form selections and assemble them into a
precise, structured research brief that Grok will use to find and qualify companies.

The brief must be specific enough that Grok can find REAL companies with REAL signals —
not generic descriptions. It should read like a briefing from a senior sales director
to a research analyst, not a search query.

ACCEDO'S CORE SERVICES (reference these when framing the opportunity):
- Bespoke smart TV app development: Samsung Tizen, LG WebOS, Roku, Fire TV, Apple TV, Android TV
- SSAI integration, DRM implementation, live/sports streaming architecture
- Platform migration from white-label vendors (ViewLift, 24i, 3SS, OTTera, Endeavor Streaming)
- Multi-platform unification after M&A
- Team augmentation: engineering, QA, UX/UI
- Managed services and support
- First-time CTV builds for mobile-first or social-first companies

INTAKE FORM:
Verticals selected: {verticals}
Signals selected: {signals}
Additional context: {context}
Business Unit (geography focus): {bu}

GEOGRAPHY GUIDANCE:
- NAM: Focus on companies headquartered in North America (US, Canada, Mexico)
- E&L: Focus on companies headquartered in Europe or Latin America
- APAC: Focus on companies headquartered in Asia Pacific (including Australia and New Zealand)
- Companies with global operations are fine — HQ location is the filter, not operational footprint

BRIEF ASSEMBLY RULES:
1. Write the brief in second person directed at Grok ("Find...", "Look for...", "Prioritise...")
2. Be specific about what signals to look for based on the selections
3. Include the geography constraint naturally — frame it as HQ location preference
4. If specific vendors are mentioned in context, instruct Grok to look for displacement signals
5. If hiring signals are selected, specify the exact role types to look for
6. Tier guidance: Accedo sells to Tier 1 and Tier 2 organisations — avoid startups under 50 employees
7. The brief should be 150-250 words — substantive but scannable
8. End with a one-line priority instruction: what is the single most important signal to find?

Return ONLY a JSON object, no preamble, no markdown fences:
{{
  "brief": "The full research brief text, 150-250 words, ready to send to Grok",
  "query_summary": "10-15 word plain English summary of what we're hunting for (shown to rep)",
  "signal_focus": ["list", "of", "2-4", "primary", "signal", "types", "selected"]
}}
"""

# ---------------------------------------------------------------------------
# Randomizer configurations — used by the Suggest button to auto-fill the form
# Each entry maps to the exact field names used in the intake form
# ---------------------------------------------------------------------------

RANDOM_CONFIGS = [
    {
        "verticals": ["Sports"],
        "signals": ["Rights deal", "First CTV build", "Hiring: OTT/CTV engineers"],
        "context": "Regional sports networks that recently secured new broadcast rights and need to launch a CTV experience before the season starts",
    },
    {
        "verticals": ["News"],
        "signals": ["First CTV build", "Hiring: Product managers", "Platform consolidation"],
        "context": "Digital-first news publishers with strong mobile audiences that have not yet built a connected TV presence",
    },
    {
        "verticals": ["Sports", "Entertainment"],
        "signals": ["Vendor migration", "App store complaints", "Hiring: OTT/CTV engineers"],
        "context": "Broadcasters showing frustration with ViewLift or 24i — slow releases, poor OEM support, or recent 24i bankruptcy concern",
    },
    {
        "verticals": ["Entertainment"],
        "signals": ["FAST/AVOD launch", "Funding round", "First CTV build"],
        "context": "Streaming services that recently closed a funding round and are launching or expanding FAST channels to new platforms",
    },
    {
        "verticals": ["Faith"],
        "signals": ["First CTV build", "App redesign", "Hiring: UX/UI designers"],
        "context": "Faith-based media organisations with loyal mobile audiences evaluating their first bespoke smart TV app",
    },
    {
        "verticals": ["Sports"],
        "signals": ["M&A / platform unification", "Hiring: TPMs", "Platform consolidation"],
        "context": "Sports media companies that have gone through an acquisition and are now running two separate streaming platforms that need unification",
    },
    {
        "verticals": ["Education"],
        "signals": ["CTV expansion", "Funding round", "Hiring: Front-end engineers"],
        "context": "EdTech video platforms that closed Series B or later and are expanding from mobile to connected TV devices",
    },
    {
        "verticals": ["Fitness"],
        "signals": ["App redesign", "CTV expansion", "Hiring: OTT/CTV engineers"],
        "context": "Fitness streaming services with strong mobile subscriptions that need to improve or rebuild their smart TV experience",
    },
    {
        "verticals": ["Multi-Vertical"],
        "signals": ["DTC pivot", "Leadership change", "Hiring: Product managers"],
        "context": "Traditional media companies announcing a direct-to-consumer streaming pivot with new digital leadership in place",
    },
    {
        "verticals": ["News", "Sports"],
        "signals": ["Rights deal", "Market expansion", "First CTV build"],
        "context": "News or sports broadcasters expanding into new territories and needing CTV apps for markets where they have no existing platform",
    },
    {
        "verticals": ["Audio"],
        "signals": ["CTV expansion", "First CTV build", "Funding round"],
        "context": "Audio-first platforms (podcasts, music, radio) that are adding video content and need their first CTV application",
    },
    {
        "verticals": ["Pay TV"],
        "signals": ["Vendor migration", "Platform consolidation", "App store complaints"],
        "context": "Pay TV operators whose legacy middleware or white-label OTT stack is showing its age — poor app ratings, slow feature delivery",
    },
    {
        "verticals": ["Sports"],
        "signals": ["Hiring: OTT/CTV engineers", "Hiring: QA automation", "App store complaints"],
        "context": "Sports streaming services with consistently poor app store ratings for Roku or Fire TV — buffering, DRM, or login issues cited in reviews",
    },
    {
        "verticals": ["Entertainment", "Faith"],
        "signals": ["FAST/AVOD launch", "First CTV build", "Hiring: Product managers"],
        "context": "SVOD services pivoting to add a free ad-supported tier and needing SSAI integration across their smart TV apps",
    },
    {
        "verticals": ["In-Vehicle"],
        "signals": ["First CTV build", "Funding round", "Hiring: Front-end engineers"],
        "context": "Auto or in-vehicle entertainment companies building video streaming experiences for next-generation vehicle platforms",
    },
]