"""
config.py — Central configuration for the OTT Lead Gen frontier pipeline.
All secrets are loaded from environment variables. Never hardcode here.

Required .env entries:
    XAI_API_KEY="xai-..."
    ANTHROPIC_API_KEY="sk-ant-..."
    GOOGLE_SERVICE_ACCOUNT_JSON="service_account.json"
    GOOGLE_SHEET_NAME="OTT Leads"

Optional .env entries (pipeline degrades gracefully without them):
    EXA_API_KEY="..."                  # LinkedIn post intelligence
    APOLLO_MASTER_API_KEY="..."        # Apollo People Search (zero credits)
    APOLLO_API_KEY="..."               # Apollo Bulk Enrichment (1 credit/person)
    SERPER_API_KEY="..."               # Job board mining
    JINA_API_KEY="..."                 # Universal URL reader
    BUILTWITH_API_KEY="..."            # Tech stack fingerprinting

Apollo key setup:
    APOLLO_MASTER_API_KEY — required for /mixed_people/api_search
        Generate at: app.apollo.io -> Settings -> Integrations -> API Keys
        Click "Create New Key" -> select type "Master"
    APOLLO_API_KEY — standard key for /people/bulk_match
        Same page, standard key type
"""

try:
    import streamlit as st
    # Mirror Streamlit secrets into os.environ so the rest of config.py works unchanged
    for key, value in st.secrets.items():
        import os
        os.environ.setdefault(key, str(value))
except Exception:
    pass  # Not running on Streamlit Cloud — .env handles it locally

import os    
from typing import List
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Required API Keys
# ---------------------------------------------------------------------------
XAI_API_KEY: str = os.environ.get("XAI_API_KEY", "")
ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")

# ---------------------------------------------------------------------------
# Google Sheets
# ---------------------------------------------------------------------------
GOOGLE_SERVICE_ACCOUNT_JSON: str = os.environ.get(
    "GOOGLE_SERVICE_ACCOUNT_JSON", "service_account.json"
)
GOOGLE_SHEET_NAME: str = os.environ.get("GOOGLE_SHEET_NAME", "OTT Leads")
GOOGLE_WORKSHEET_NAME: str = os.environ.get("GOOGLE_WORKSHEET_NAME", "Leads")
GOOGLE_COLD_WORKSHEET_NAME: str = os.environ.get("GOOGLE_COLD_WORKSHEET_NAME", "Cold Leads")
GOOGLE_LOGS_WORKSHEET_NAME: str = os.environ.get("GOOGLE_LOGS_WORKSHEET_NAME", "Logs")

GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")

GEMINI_DISCOVERY_MODEL: str = "gemini-3-flash-preview"
GEMINI_DISCOVERY_MAX_TOKENS: int = 2048
# ---------------------------------------------------------------------------
# Model selection
# ---------------------------------------------------------------------------
GROK_SCOUT_MODEL: str = "grok-4-1-fast-reasoning"
GROK_SCOUT_MAX_TOKENS: int = 10000

CLAUDE_ANALYST_MODEL: str = "claude-sonnet-4-5"
CLAUDE_ANALYST_MAX_TOKENS: int = 2048

CLAUDE_COPYWRITER_MODEL: str = "claude-opus-4-5"
CLAUDE_COPYWRITER_MAX_TOKENS: int = 2048

# ---------------------------------------------------------------------------
# Exa — LinkedIn post intelligence
# ---------------------------------------------------------------------------
# Free tier: 1,000 searches/month. Sign up at https://exa.ai
# Pipeline skips LinkedIn enrichment silently if key is not set.

EXA_API_KEY: str = os.environ.get("EXA_API_KEY", "")
EXA_ENABLED: bool = bool(EXA_API_KEY)          # auto-enabled when key is present
EXA_DAYS_BACK: int = 90                         # how far back to search LinkedIn posts
EXA_MAX_RESULTS: int = 5                        # posts per exec search

# ---------------------------------------------------------------------------
# Apollo.io — Power map validation + contact enrichment
# ---------------------------------------------------------------------------
# TWO keys are required because two different endpoints have different auth:
#
#   APOLLO_MASTER_API_KEY  — People Search (/mixed_people/api_search)
#       Zero credits. Validates Grok's named execs + discovers fallback contacts.
#       Requires "Master" key type from Apollo dashboard.
#
#   APOLLO_API_KEY         — Bulk People Enrichment (/people/bulk_match)
#       1 credit per matched person (max 2 per prospect = 2 credits).
#       Returns verified email, LinkedIn URL, seniority, location.
#       Uses standard key type.
#
# Pipeline skips Apollo enrichment if either key is missing.

APOLLO_MASTER_API_KEY: str = os.environ.get("APOLLO_MASTER_API_KEY", "")
APOLLO_API_KEY: str = os.environ.get("APOLLO_API_KEY", "")

# Set True once both Apollo keys are in your .env
APOLLO_ENABLED: bool = True

# Rate limits (Apollo Basic/Professional tier)
APOLLO_REQUESTS_PER_MINUTE: int = 10   # search endpoint
# Bulk enrich rate limit is automatically set to 50% of above in apollo.py

# ---------------------------------------------------------------------------
# Optional enrichment keys (not yet wired — placeholders for future use)
# ---------------------------------------------------------------------------
SERPER_API_KEY: str = os.environ.get("SERPER_API_KEY", "")
JINA_API_KEY: str = os.environ.get("JINA_API_KEY", "")
BUILTWITH_API_KEY: str = os.environ.get("BUILTWITH_API_KEY", "")

# ---------------------------------------------------------------------------
# Pipeline behaviour
# ---------------------------------------------------------------------------

# Minimum score a prospect must reach to be written to Sheets.
# Prospects below this threshold are routed to the Cold Leads tab.
MIN_SCORE_TO_WRITE: int = 55

# Deduplication strategy for Sheets writes.
# "domain"         — skip if domain already exists (Apollo off)
# "domain+contact" — skip if (domain, contact_name) pair exists (Apollo on)
DEDUP_STRATEGY: str = "domain+contact"

# Max prospects Grok should return per query run.
MAX_PROSPECTS_PER_RUN: int = 5

# ---------------------------------------------------------------------------
# OTT Signal Query Library
# ---------------------------------------------------------------------------
OTT_SIGNAL_QUERIES: List[str] = [
    # SIGNAL 1: THE TALENT VOID
    "site:linkedin.com/jobs 'Director of OTT' OR 'VP Engineering' streaming 'Roku' OR 'Tizen' posted >60 days ago news sports",

    # SIGNAL 2: COMMERCIAL INFLECTION / RIGHTS DEALS
    "Sports broadcaster 'exclusive rights' 2025 2026 launch deadline OTT infrastructure transition",

    # SIGNAL 3: ACTIVE FRICTION / TECH DEBT
    "Tier 1 streaming app 'buffering' OR 'DRM' OR 'Roku' 1-star reviews news sports 2026",

    # SIGNAL 4: COMPETITIVE DISPLACEMENT
    "Media company migration from ViewLift OR 24i OR OTTera OR 3SS infrastructure 2026",

    # SIGNAL 5: M&A INTEGRATION DEBT
    "Streaming company merger OR acquisition 2025 2026 'platform unification' OR 'session-sync' challenges",
]

# ---------------------------------------------------------------------------
# Google Sheet columns — ORDER MATTERS, must match SheetsClient.append_lead()
# ---------------------------------------------------------------------------
SHEET_COLUMNS: List[str] = [
    # Identity
    "Timestamp",
    "Company",
    "Domain",
    "Priority",
    "Opportunity Score",
    "Opportunity Type",
    # Research intelligence
    "Transition Gap",
    "Causal Inflection",
    "Incumbent Vendor",
    "Tech Stack",
    "App Store Rating (iOS)",
    "App Store Rating (Android)",
    # Signals
    "Top Signal",
    "Signal Source",
    "Signal Confidence",
    "Adversarial Check",
    # Power map
    "Visionary Name",
    "Visionary Title",
    "Visionary LinkedIn",
    "Visionary Hook",
    "Operator Name",
    "Operator Title",
    "Operator LinkedIn",
    "Operator Hook",
    # Outreach
    "Visionary Subject Line",
    "Visionary Email",
    "Operator Subject Line",
    "Operator Email",
    "Objection: In-House",
    "Objection: Incumbent",
    "Objection: Budget",
    # Exa LinkedIn intelligence (populated when EXA_API_KEY is set)
    "Visionary LinkedIn Quote",
    "Visionary LinkedIn Pain",
    "Operator LinkedIn Quote",
    "Operator LinkedIn Pain",
    # Apollo enrichment (populated when APOLLO_ENABLED = True)
    "Apollo Contact Name",
    "Apollo Contact Title",
    "Apollo Email",
    "Apollo LinkedIn",
    # Meta
    "Salesforce Note",
    "Research Gaps",
    "Query",
    "Status",
    #discovery pipeline
    "Exa Rejected Companies",
    "Gemini Selected reasoning",
]
