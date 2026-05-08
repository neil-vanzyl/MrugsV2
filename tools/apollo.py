"""
tools/apollo.py — Apollo.io People Intelligence (Domain Pivot Edition)
======================================================================
"""

import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import requests

import config
from utils.helpers import RateLimiter, with_retries, track_performance

logger = logging.getLogger("ott_lead_gen.apollo")

_search_limiter = RateLimiter(requests_per_minute=config.APOLLO_REQUESTS_PER_MINUTE)
_enrich_limiter = RateLimiter(requests_per_minute=max(1, config.APOLLO_REQUESTS_PER_MINUTE // 2))

APOLLO_SEARCH_URL = "https://api.apollo.io/api/v1/mixed_people/api_search"
APOLLO_BULK_URL   = "https://api.apollo.io/api/v1/people/bulk_match"


# ---------------------------------------------------------------------------
# Role title / seniority maps
# ---------------------------------------------------------------------------

VISIONARY_TITLES = [
    "Chief Executive Officer", "CEO", "Chief Content Officer", "President",
    "General Manager", "SVP Content", "VP Content", "Head of Content", 
    "Managing Director", "Chief Revenue Officer",
]
VISIONARY_SENIORITIES = ["c_suite", "vp", "partner", "owner", "founder"]

OPERATOR_TITLES = [
    "Chief Technology Officer", "CTO", "VP Engineering", "SVP Engineering", 
    "Head of Engineering", "Head of OTT", "Head of Streaming", "Head of Video",
    "VP Product", "Director of Engineering", "VP Technology",
]
OPERATOR_SENIORITIES = ["c_suite", "vp", "head", "director"]


@dataclass
class ApolloContact:
    apollo_id:             str = ""
    name:                  str = ""
    first_name:            str = ""
    last_name:             str = ""
    title:                 str = ""
    seniority:             str = ""
    email:                 str = ""
    email_status:          str = ""
    linkedin_url:          str = ""
    organization_name:     str = ""
    city:                  str = ""
    country:               str = ""
    validated_grok_name:   bool = False
    was_fallback:          bool = False

    def to_power_map_patch(self) -> dict:
        patch = {
            "name":       self.name,
            "title":      self.title,
            "linkedin":   self.linkedin_url,
            "verified":   True,
            "apollo_id":  self.apollo_id,
            "source":     "apollo_validated" if self.validated_grok_name else "apollo_fallback",
        }
        if self.email:
            patch["email"] = self.email
            patch["email_status"] = self.email_status
        return patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_parent_domain(domain: str) -> str:
    """Hardened cleaner: Strips paths, protocols, and handles subdomains."""
    # 1. Strip protocol (http://) and paths (/play)
    clean_domain = domain.split('//')[-1].split('/')[0].lower()
    
    # 2. Simplify subdomains
    parts = clean_domain.split('.')
    if len(parts) > 2:
        return ".".join(parts[-2:])
    return clean_domain

def _search_headers() -> dict:
    return {"Content-Type": "application/json", "X-Api-Key": config.APOLLO_MASTER_API_KEY}

def _enrich_headers() -> dict:
    return {"Content-Type": "application/json", "X-Api-Key": config.APOLLO_API_KEY}

def _safe_post(url: str, payload: dict, context: str, headers: dict = None) -> Optional[dict]:
    resp = requests.post(url, json=payload, headers=headers or _enrich_headers(), timeout=25)
    if resp.status_code == 200: return resp.json()
    if resp.status_code == 429:
        time.sleep(int(resp.headers.get("Retry-After", 10)))
        resp.raise_for_status()
    logger.error(f"Apollo {resp.status_code} ({context}): {resp.text[:200]}")
    return None


# ---------------------------------------------------------------------------
# Core Logic
# ---------------------------------------------------------------------------

@with_retries(max_attempts=3)
def _search_validate_name(name: str, domain: str) -> Optional[dict]:
    _search_limiter.acquire()
    payload = {"q_keywords": name, "q_organization_domains_list": [domain], "per_page": 5}
    data = _safe_post(APOLLO_SEARCH_URL, payload, f"validate {name}", headers=_search_headers())
    if not data: return None
    
    name_lower = name.lower().split()
    for person in data.get("people", []):
        apollo_name = (person.get("name") or "").lower().split()
        if len(set(name_lower) & set(apollo_name)) >= min(2, len(name_lower)):
            return person
    return None

@with_retries(max_attempts=2)
def _search_by_name_no_domain(name: str, company_name: str) -> Optional[dict]:
    """
    Last-resort search: find a person by name + company name WITHOUT domain constraint.
    Used when all domain-based searches fail — e.g. stage.app, .io product domains,
    or subsidiaries not registered under their product domain in Apollo's database.
    Zero credits. Less precise — filters by name + company match after results return.
    Only called after all domain pivots are exhausted.
    """
    _search_limiter.acquire()
    payload = {
        "q_keywords": f"{name} {company_name}",
        "per_page":   5,
        "page":       1,
    }
    data = _safe_post(APOLLO_SEARCH_URL, payload, f"name-only {name}", headers=_search_headers())
    if not data:
        return None

    name_lower    = name.lower().split()
    company_lower = company_name.lower()

    for person in data.get("people", []):
        apollo_name = (person.get("name") or "").lower().split()
        # Name tokens must overlap sufficiently
        if len(set(name_lower) & set(apollo_name)) < min(2, len(name_lower)):
            continue
        # Organisation must loosely match the company name
        org = ((person.get("organization") or {}).get("name") or "").lower()
        if company_lower[:8] in org or org[:8] in company_lower:
            logger.info(
                f"Apollo name-only ✓ '{name}' → '{person.get('name')}' "
                f"at '{(person.get('organization') or {}).get('name', '?')}'"
            )
            return person

    return None


@with_retries(max_attempts=3)
def _search_by_role(domain: str, titles: List[str], seniorities: List[str], exclude: str = "") -> Optional[dict]:
    _search_limiter.acquire()
    payload = {"q_organization_domains_list": [domain], "person_titles": titles[:6], "person_seniorities": seniorities, "per_page": 5}
    data = _safe_post(APOLLO_SEARCH_URL, payload, f"fallback {domain}", headers=_search_headers())
    if not data: return None
    
    exclude_lower = exclude.lower()
    for person in data.get("people", []):
        if exclude_lower and exclude_lower in (person.get("name") or "").lower(): continue
        return person
    return None

@track_performance("apollo")
@with_retries(max_attempts=3)
def _bulk_enrich(search_results: List[dict], domain: str) -> List[dict]:
    if not search_results: return []
    _enrich_limiter.acquire()
    
    # We pass the Apollo ID for precision
    details = [{"id": p["id"], "domain": domain} for p in search_results if p.get("id")]
    
    # ADD THESE FLAGS to reveal protected data
    payload = {
        "details": details,
        "reveal_personal_emails": True,
        "reveal_phone_number": False 
    }
    
    data = _safe_post(APOLLO_BULK_URL, payload, f"bulk_enrich {domain}")
    if not data: return [], 0
    credits_used = data.get("credits_used", len(data.get("matches", [])))
    return data.get("matches", []), credits_used

# ---------------------------------------------------------------------------
# PARSER
# ---------------------------------------------------------------------------

def _parse_contact(raw: dict, validated: bool, fallback: bool) -> ApolloContact:
    """
    Surgical Parser: Bridges the gap between raw API dictionaries and 
    the ApolloContact object used throughout the pipeline.
    """
    linkedin = (raw.get("linkedin_url") or raw.get("linkedin") or "").strip()
    if linkedin and not linkedin.startswith("http"):
        linkedin = f"https://www.linkedin.com/in/{linkedin}"

    return ApolloContact(
        apollo_id=raw.get("id", ""),
        name=raw.get("name", ""),
        title=raw.get("title", ""),
        email=raw.get("email", ""),
        email_status=raw.get("email_status", ""),
        linkedin_url=linkedin,
        validated_grok_name=validated,
        was_fallback=fallback,
    )

# ---------------------------------------------------------------------------
# Public Entry Point
# ---------------------------------------------------------------------------

@track_performance("apollo")
def enrich_power_map(prospect: dict, usage_tracker=None) -> dict:
    if not config.APOLLO_ENABLED: return prospect

    if not config.APOLLO_MASTER_API_KEY:
        logger.error(
            "Apollo: APOLLO_MASTER_API_KEY not set — cannot run People Search. "
            "Generate a Master key at: app.apollo.io -> Settings -> Integrations -> API Keys"
        )
        return prospect

    if not config.APOLLO_API_KEY:
        logger.error(
            "Apollo: APOLLO_API_KEY not set — cannot run Bulk Enrichment. "
            "Add your standard Apollo key to .env as APOLLO_API_KEY"
        )
        return prospect

    company = prospect.get("name", "")
    domain  = prospect.get("domain", "")
    pm      = prospect.get("power_map", {})

    if not domain:
        logger.warning(f"Apollo: No domain for '{company}'")
        return prospect

    # --- DOMAIN PIVOT LOOP ---
    # Priority order: specific domain → parent domain from _get_parent_domain()
    # → parent ORG domain if Grok identified a parent organisation (e.g. MSE for MSN)
    parent_org  = prospect.get("parent_org", {}) or {}
    parent_domain = (parent_org.get("domain") or "").strip().lower()

    # Build ordered, deduplicated list of domains to try
    candidate_domains = [domain, _get_parent_domain(domain)]
    if parent_domain and parent_domain not in candidate_domains:
        candidate_domains.append(parent_domain)
        logger.info(
            f"Apollo: will also try parent org domain '{parent_domain}' "
            f"('{parent_org.get('name', '')}') for '{company}'"
        )

    domains_to_try = list(dict.fromkeys(candidate_domains))
    role_findings = {}

    for target_domain in domains_to_try:
        logger.info(f"Apollo: Searching '{company}' at {target_domain}...")
        found_names = []
        
        for role_key, label, titles, sens in [
            ("the_visionary", "Visionary", VISIONARY_TITLES, VISIONARY_SENIORITIES),
            ("the_operator",  "Operator",  OPERATOR_TITLES,  OPERATOR_SENIORITIES),
        ]:
            exec_data = pm.get(role_key, {})
            grok_name = exec_data.get("name", "").strip()
            raw_person, validated, fallback = None, False, False

            if grok_name:
                raw_person = _search_validate_name(grok_name, target_domain)
                if raw_person: validated = True

            if not raw_person:
                raw_person = _search_by_role(target_domain, titles, sens, exclude="|".join(found_names))
                if raw_person: fallback = True

            if raw_person:
                role_findings[role_key] = (raw_person, validated, fallback)
                found_names.append(raw_person.get("name", ""))

        # If we found any valid contacts, stop pivoting
        if role_findings:
            break
        logger.warning(f"  No contacts at {target_domain}, trying pivot...")

    # All domain pivots exhausted — try name-only as last resort.
    # Catches non-standard domains: stage.app, streaming.io, subsidiary product domains.
    if not role_findings:
        logger.info(
            f"Apollo: all domain pivots exhausted for '{company}' — "
            "trying name-only search as last resort"
        )
        for role_key, label, titles, sens in [
            ("the_visionary", "Visionary", VISIONARY_TITLES, VISIONARY_SENIORITIES),
            ("the_operator",  "Operator",  OPERATOR_TITLES,  OPERATOR_SENIORITIES),
        ]:
            exec_data = pm.get(role_key, {})
            grok_name = exec_data.get("name", "").strip()
            if not grok_name:
                continue
            raw_person = _search_by_name_no_domain(grok_name, company)
            if raw_person:
                role_findings[role_key] = (raw_person, True, False)
                logger.info(
                    f"Apollo name-only found '{raw_person.get('name')}' "
                    f"for {label} at '{company}'"
                )

    if not role_findings:
        logger.warning(f"Apollo: no contacts found for '{company}' after all fallbacks")
        return prospect

    apollo_credits_used = 0

    # --- ENRICHMENT & INJECTION ---
    enriched_list, apollo_credits_used = _bulk_enrich([r[0] for r in role_findings.values()], domain)

    # UPDATE 1: Index by the unique Apollo ID (as a string) instead of Name
    enriched_index = {str(e.get("id")): e for e in enriched_list}

    for role_key, (raw_p, val, fall) in role_findings.items():
        # UPDATE 2: Look up using the person_id from the search result
        person_id = str(raw_p.get("id", ""))
        final_raw = enriched_index.get(person_id, raw_p)
        
        # ... rest of the logic remains the same ...
        contact = _parse_contact(final_raw, val, fall)
        exec_data = pm.get(role_key, {})
        patch = contact.to_power_map_patch()
        
        # Surgical filter preserves Grok's name if Apollo's is empty
        clean_patch = {k: v for k, v in patch.items() if v}

        pm[role_key] = {**exec_data, **clean_patch, "_apollo_contact": contact}
        
        # Updated log to show if email was successfully captured
        email_stat = "✓" if contact.email else "—"
        final_name = pm[role_key].get("name", "Unknown")
        logger.info(f"    ✓ {role_key}: {final_name} ({'Verified' if val else 'Fallback'}) | email={email_stat}")

    # Record Apollo usage
    if usage_tracker is not None:
        search_calls = len(role_findings) * 2   # validate + fallback per role
        usage_tracker.record_apollo(
            search_calls=search_calls,
            enrich_credits=apollo_credits_used,
        )

    return prospect


def get_contacts_from_power_map(prospect: dict) -> List[ApolloContact]:
    contacts = []
    pm = prospect.get("power_map", {})
    for k in ("the_visionary", "the_operator"):
        if isinstance(pm.get(k), dict) and "_apollo_contact" in pm[k]:
            contacts.append(pm[k]["_apollo_contact"])
    return contacts
