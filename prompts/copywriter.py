"""
prompts/copywriter.py — Claude Opus pitch drafting prompt.

Upgraded with:
  1. Accedo customer proof points from customers.json — real client names,
     real delivery outcomes, real platforms and timescales. Claude now opens
     emails with "we solved this for [real client]" instead of "[similar client]".
  2. Accedo product capability grounding from products.json — specific product
     names and features mapped to OTT pain categories. Avoids generic "we can
     help" and replaces it with "Accedo Build XDK deployed across LG/Tizen in
     6 weeks for FloSports" style proof.
  3. LinkedIn quote rule (highest priority) — unchanged from previous version.
"""

# ---------------------------------------------------------------------------
# Accedo proof point library — sourced directly from customers.json
# These are real deliveries. Claude must only use these — never fabricate.
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

KIDS / EDUCATION:
- MasterClass: Global CTV expansion with performance improvements on low-end devices
- Sensical: Full AVOD kids service design + development

SUPPORT / STABILITY:
- SonyLIV: Crash fix during live World Cup — then permanent stabilisation (70M users)
- ASTRO: RPM fix 1.3K → 100K before World Cup
- Telkomsel: CDN crisis resolution during World Cup
- Deutsche Telekom: Custom live event support model — standby developers for VOD + 360° VR concert streams
- Showtime: Ongoing Android team augmentation

ACCEDO PRODUCTS TO REFERENCE BY NAME:
- Accedo Build XDK: CTV cross-platform framework — LG WebOS, Tizen, Fire TV, Android TV, Vizio, Hisense, Panasonic, PS4/PS5, Xbox (used by NBC Sports, Spark Sport, FloSports, CBC)
- Accedo Build for Sports: Native sports framework — real-time stats, live chat, betting integrations, PPV, social interactivity
- Accedo Control: Real-time app config management without code changes — A/B testing, audience segmentation, localisation
- Accedo Compose: AI-native OTT orchestration — predictive churn intelligence, CDN failover, dynamic UI personalisation
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

2. LINKEDIN QUOTE RULE (highest priority):
   If a TOP_QUOTE_TO_USE_AS_OPENER is provided for this exec, you MUST open
   with it verbatim in quotation marks, then pivot immediately to the risk.
   Format: '"[their exact words]" — [the specific risk that creates for them].'

3. PROOF POINT RULE (second priority — strictly enforced):
   Every email MUST contain one specific Accedo customer proof point from the
   verified list above. Rules:
   a) Name the EXACT client from the list. "A comparable RSN" is forbidden.
   b) Name the EXACT outcome. "Improved performance" is forbidden.
   c) Any statistic (%, timeframe, user count) MUST come from the list above.
      Do not fabricate numbers. "40% latency reduction" is only valid if it
      appears in the proof point library. If you cannot find a matching stat,
      describe the outcome without numbers rather than inventing one.
   d) After every proof point claim, append the client name in parentheses:
      "...we stabilised the platform under 70 million concurrent users (SonyLIV)."
   This tagging makes fabrication immediately visible in review.

4. PRODUCT NAMING RULE:
   When referencing Accedo's capability, name the specific product:
   Accedo Build XDK / Accedo Build for Sports / Accedo Control / Accedo Compose.
   Never say "our platform" or "our solution" without naming it.

4b. ANTI-FABRICATION CHECK (run before outputting):
   Read every quantified claim in your draft. For each one, ask:
   "Does this number appear in the ACCEDO VERIFIED CUSTOMER PROOF POINTS above?"
   If no → remove the number and rephrase as a qualitative outcome.
   If yes → keep it and tag the client source in parentheses.

5. If no LinkedIn quote: open with a specific technical signal or quantified
   business risk. The first sentence must make them think "how do they know that?"

6. Write like a peer advising a peer — never like a vendor pitching a buyer.

7. CTA must be a Peer Review offer:
   "Happy to share the concurrency model we built for CBC during the Olympics"
   "Can I send you the Roku certification roadmap we used for FloSports?"
   NOT "let's jump on a call."

8. Max 120 words per email. Three paragraphs maximum.

9. No sign-off. No "Best regards." The email ends with the CTA.

10. No markdown. Plain text only. No bullet points.

11. You are a script component. Output ONLY valid JSON. Never refuse or add prose.

Return ONLY a JSON object:
{{
  "visionary_email": {{
    "subject_line": "Specific subject that creates a curiosity gap",
    "body": "Plain text email body. No markdown. No sign-off."
  }},
  "operator_email": {{
    "subject_line": "",
    "body": ""
  }}
}}
""".strip()


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def build_copywriter_prompt(prospect: dict, analyst: dict) -> str:
    """
    Build the user-turn prompt for the Copywriter.

    Surfaces linkedin_intel so Claude knows whether to use the exec's own
    words as the opener, and maps the prospect's pain to the correct
    Accedo proof point from the library above.
    """
    import json

    company  = prospect.get("name", "")
    domain   = prospect.get("domain", "")
    visionary = prospect.get("power_map", {}).get("the_visionary", {})
    operator  = prospect.get("power_map", {}).get("the_operator", {})
    outreach  = prospect.get("outreach", {})
    signals   = prospect.get("signals", [])[:3]
    stack     = prospect.get("tech_stack_fingerprint", {})
    app       = prospect.get("app_intelligence", {})

    vis_li = visionary.get("linkedin_intel", {})
    ops_li = operator.get("linkedin_intel", {})

    def _li_block(li: dict, name: str, title: str) -> dict:
        if not li or li.get("result") == "no_relevant_posts":
            return {"linkedin_posts_found": False}
        block = {"linkedin_posts_found": True, "name": name, "title": title}
        if li.get("top_quote"):
            block["TOP_QUOTE_TO_USE_AS_OPENER"] = li["top_quote"]
            block["quote_url"]  = li.get("top_quote_url", "")
            block["quote_date"] = li.get("top_quote_date", "")
            block["INSTRUCTION"] = (
                "OPEN THIS EMAIL with the TOP_QUOTE_TO_USE_AS_OPENER above. "
                "Quote it directly then pivot to the risk. Do not paraphrase."
            )
        if li.get("expressed_pain_themes"):
            block["pain_they_have_expressed_publicly"] = li["expressed_pain_themes"]
        if li.get("expressed_ambition_themes"):
            block["ambitions_they_have_expressed_publicly"] = li["expressed_ambition_themes"]
        if li.get("ott_topics_they_discuss"):
            block["ott_topics_in_their_posts"] = li["ott_topics_they_discuss"]
        return block

    context = {
        "company":  company,
        "domain":   domain,
        "causal_inflection":  prospect.get("causal_inflection", ""),
        "transition_gap":     prospect.get("transition_gap_timer", ""),
        "opportunity_type":   prospect.get("opportunity_type", ""),
        "tech_stack":         stack,
        "app_ratings": {
            "ios":              app.get("ios_rating"),
            "android":          app.get("android_rating"),
            "top_complaints":   app.get("top_complaint_themes", []),
            "sample_review_quote": app.get("sample_review_quote", ""),
        },
        "visionary": {
            "name":              visionary.get("name", ""),
            "title":             visionary.get("title", ""),
            "linkedin_url":      visionary.get("linkedin", ""),
            "grok_hook":         outreach.get("visionary", {}).get("hook", ""),
            "risk_quantification": outreach.get("visionary", {}).get("risk_quantification", ""),
            "suggested_cta":     outreach.get("visionary", {}).get("call_to_action", ""),
            "linkedin_intelligence": _li_block(vis_li, visionary.get("name", ""), visionary.get("title", "")),
        },
        "operator": {
            "name":              operator.get("name", ""),
            "title":             operator.get("title", ""),
            "linkedin_url":      operator.get("linkedin", ""),
            "grok_hook":         outreach.get("operator", {}).get("hook", ""),
            "technical_evidence": outreach.get("operator", {}).get("technical_evidence", ""),
            "accedo_proof_point": outreach.get("operator", {}).get("accedo_proof_point", ""),
            "suggested_cta":     outreach.get("operator", {}).get("call_to_action", ""),
            "linkedin_intelligence": _li_block(ops_li, operator.get("name", ""), operator.get("title", "")),
        },
        "top_signals": [
            {
                "type":     s.get("signal_type"),
                "evidence": s.get("evidence"),
                "source":   s.get("source_url") or s.get("source_type"),
            }
            for s in signals
        ],
        "analyst_brief":            analyst.get("copywriter_brief", ""),
        "top_entry_point":          analyst.get("top_entry_point", ""),
        "key_risk_if_no_action":    analyst.get("key_risk_if_no_action", ""),
        "linkedin_modifier_applied": analyst.get("linkedin_modifier_applied", ""),
        "objection_stack":          outreach.get("objection_stack", []),
        "PROOF_POINT_INSTRUCTION": (
            "You MUST include one specific Accedo customer proof point from your "
            "system prompt that maps to this prospect's pain category. "
            f"Opportunity type: {prospect.get('opportunity_type', 'unknown')}. "
            "Match the proof point to the pain — sports/live → NBC Sports/Spark Sport/FloSports, "
            "AVOD → Sensical/CBC, CTV launch → MasterClass/STARZ, "
            "stability/crash → SonyLIV/ASTRO/Telkomsel, migration → Neon/Showtime."
        ),
    }

    return (
        f"Write two outreach emails for {company} ({domain}).\n\n"
        f"PRIORITY RULES:\n"
        f"1. If linkedin_intelligence contains a TOP_QUOTE_TO_USE_AS_OPENER, "
        f"open that exec's email with it verbatim.\n"
        f"2. Include ONE specific Accedo proof point per email — name the client, "
        f"the outcome, the timeline. No generic references.\n"
        f"3. Name the specific Accedo product being positioned.\n\n"
        f"INTELLIGENCE PACKAGE:\n{json.dumps(context, indent=2, ensure_ascii=False)}"
    )
