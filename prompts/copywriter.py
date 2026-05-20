"""
prompts/copywriter.py — Claude Opus pitch drafting prompt.

Upgraded to read deterministic friction angles (Tech Stack + App Velocity)
directly from the new Python scrapers.
"""

# ---------------------------------------------------------------------------
# Accedo proof point library — sourced directly from customers.json
# ---------------------------------------------------------------------------

ACCEDO_PROOF_POINTS = """
ACCEDO VERIFIED CUSTOMER PROOF POINTS — use these verbatim in emails:

SPORTS / LIVE / CONCURRENCY:
- NBC Sports: Built multi-platform live streaming across Roku, HTML/JS, gaming consoles, Smart TVs using Accedo framework
- Spark Sport: Delivered Big Screen apps across Samsung, LG, Panasonic, Android TV for 2019 Rugby World Cup (Live, VOD, PPV) using Accedo Build
- CBC Radio-Canada: Olympic coverage with SSAI ad tiers (Google IMA + DAI, live + VOD) using Accedo Build
- Bell Media: Multi-brand UX across sports, entertainment, news verticals on popular CTV devices
- FloSports: Samsung, LG, Vizio Smart TV expansion — agile delivery, UX, QA, post-launch maintenance using Accedo Build Framework
- Telkomsel (MaxStream): Resolved CDN bandwidth bottleneck during World Cup — increased CSN capacity, redirected to offshore CDNs
- ASTRO: Fixed middleware logging bottleneck before World Cup — increased RPM from 1.3K to 100K
- SonyLIV: Emergency fix for service crash during live World Cup — stabilised platform for 70M users

PLATFORM MIGRATION / CTV LAUNCH:
- MasterClass: Expanded to new CTV devices globally — consistent QoE, enhanced performance on low-end set-top boxes
- STARZ: Multi-device OTT service — iPhone, iPad, Apple TV
- Showtime: Refreshed Roku + Connected Device channels; Android team augmentation
- Neon: Front-end lead for Lightbox/Neon merger — new UI, ongoing product growth

AVOD / AD TECH:
- Sensical: Free AVOD kids service — custom ad UI, parental controls, kid-safe TikTok vertical feed
- CBC Radio-Canada: SSAI upgrade for premium + ad-supported Olympic tiers

SUPPORT / STABILITY:
- SonyLIV: Crash fix during live World Cup — then permanent stabilisation (70M users)
- ASTRO: RPM fix 1.3K → 100K before World Cup
- Telkomsel: CDN crisis resolution during World Cup
- Deutsche Telekom: Custom live event support model — standby developers for VOD + 360° VR concert streams
- Showtime: Ongoing Android team augmentation

ACCEDO PRODUCTS TO REFERENCE BY NAME:
- Accedo Build XDK: CTV cross-platform framework (used by NBC Sports, Spark Sport, FloSports)
- Accedo Build for Sports: Native sports framework — real-time stats, live chat, PPV
- Accedo Control: Real-time app config management without code changes
- Accedo Compose: AI-native OTT orchestration — predictive churn intelligence, CDN failover
"""

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

COPYWRITER_SYSTEM_PROMPT = f"""
You are the best B2B technology sales writer in the streaming industry.
You write for Accedo (https://www.accedo.tv), a premium OTT front-end firm.
You are a 12-year Accedo veteran who has personally worked on the accounts below.

{ACCEDO_PROOF_POINTS}

Your emails have a 40%+ reply rate because you follow these rules without exception:

RULES:
1. NEVER open with "I hope this finds you well", "I wanted to reach out",
   "My name is", or any pleasantry. Ever.

2. LINKEDIN QUOTE RULE: If a TOP_QUOTE_TO_USE_AS_OPENER is provided, you MUST open
   with it verbatim in quotation marks, then pivot immediately to the risk.

3. DETERMINISTIC FRICTION RULE: If a displacement_angle or friction_angle is provided, 
   you MUST build your pitch around that specific technical debt (e.g., decaying apps or legacy vendor constraints).

4. PROOF POINT RULE: Every email MUST contain ONE specific Accedo customer proof point 
   from the verified list above that matches their specific pain. Name the EXACT client.

5. PRODUCT NAMING RULE: Name the specific Accedo product (Accedo Build XDK, etc.).

6. Write like a peer advising a peer. CTA must be a Peer Review offer (e.g., "Can I share the concurrency model we built for CBC?").

7. Max 120 words per email. Three paragraphs maximum. No sign-off. Plain text only.
"""

# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def build_copywriter_prompt(prospect: dict, analyst: dict) -> str:
    import json

    company = prospect.get("name", "")
    visionary = prospect.get("power_map", {}).get("the_visionary", {})
    operator = prospect.get("power_map", {}).get("the_operator", {})
    
    stack = prospect.get("tech_stack_fingerprint", {})
    app = prospect.get("app_intelligence", {})
    signals = prospect.get("signals", [])[:3]

    vis_li = visionary.get("linkedin_intel", {})
    ops_li = operator.get("linkedin_intel", {})

    def _li_block(li: dict) -> dict:
        if not li or not li.get("linkedin_posts_found"):
            return {"linkedin_posts_found": False}
        return li

    context = {
        "company": company,
        "analyst_verdict": analyst.get("verdict", ""),
        "transition_gap_confirmed": analyst.get("transition_gap_confirmed", ""),
        "top_entry_point": analyst.get("top_entry_point", ""),
        "copywriter_brief": analyst.get("copywriter_brief", ""),
        
        # New Deterministic Inputs
        "tech_stack": {
            "incumbent_vendor": stack.get("incumbent_vendor", "unknown"),
            "displacement_angle": stack.get("incumbent_displacement_angle", ""),
            "cdn": stack.get("cdn", "unknown")
        },
        "app_health": {
            "release_velocity_status": app.get("release_velocity_status", ""),
            "friction_angle": app.get("friction_angle", ""),
            "ios_rating": app.get("ios_rating", ""),
            "android_rating": app.get("android_rating", "")
        },
        
        "news_signals": [s.get("evidence") for s in signals],
        
        "visionary": {
            "name": visionary.get("name", "Visionary"),
            "title": visionary.get("title", ""),
            "linkedin_intelligence": _li_block(vis_li),
        },
        "operator": {
            "name": operator.get("name", "Operator"),
            "title": operator.get("title", ""),
            "linkedin_intelligence": _li_block(ops_li),
        }
    }

    return (
        f"Write two outreach emails for {company}.\n\n"
        f"INTELLIGENCE PACKAGE:\n{json.dumps(context, indent=2, ensure_ascii=False)}"
    )

    