"""
tools/technographics.py — Deterministic OTT Tech Stack Scanner
==============================================================
Identifies video players, OVPs, CDNs, and white-label OTT vendors 
by scanning raw HTML and HTTP headers for specific OTT vendor fingerprints.
Zero-cost approach: No third-party APIs required.
"""

import logging
import re
import requests
from typing import Dict

from utils.helpers import with_retries, track_performance

logger = logging.getLogger("ott_lead_gen.technographics")

# ---------------------------------------------------------------------------
# OTT Infrastructure Fingerprints
# ---------------------------------------------------------------------------

OTT_FINGERPRINTS = {
    "incumbent_vendor": {
        "ViewLift": [r"viewlift\.com", r"snagfilms", r"viewlift-tools"],
        "24i": [r"24i\.com", r"smartx", r"24i-app"],
        "3SS": [r"3ss\.tv", r"3ready"],
        "Endeavor Streaming": [r"endeavorstreaming\.com", r"neulion"],
        "OTTera": [r"ottera\.tv"],
        "Quickplay": [r"quickplay\.com", r"firstlight"]
    },
    "video_player": {
        "JW Player": [r"jwplayer\.com", r"jwplayer\.js"],
        "Bitmovin": [r"bitmovin\.com", r"bitmovinplayer"],
        "Brightcove": [r"players\.brightcove\.net", r"brightcove"],
        "THEOplayer": [r"theoplayer\.com", r"theoplayer\.js"],
        "Kaltura": [r"kaltura\.com", r"mwEmbed"],
        "Video.js": [r"video-js", r"video\.js"]
    },
    "ovp": {
        "Vimeo OTT": [r"vhx\.tv", r"vimeo\.com/ott"],
        "Amagi": [r"amagi\.tv"],
        "Wurl": [r"wurl\.com"]
    }
}

# Free HTTP Header patterns for zero-cost CDN/Infrastructure detection
HEADER_FINGERPRINTS = {
    "x-amz-cf-id": ("cdn", "AWS CloudFront"),
    "x-fastly-request-id": ("cdn", "Fastly"),
    "cf-ray": ("cdn", "Cloudflare"),
    "server": {
        "akamai": ("cdn", "Akamai"),
        "cloudflare": ("cdn", "Cloudflare")
    }
}

# ---------------------------------------------------------------------------
# Fingerprint Scraper (HTML + HTTP Headers)
# ---------------------------------------------------------------------------

@with_retries(max_attempts=2, delay=2.0)
def _scrape_fingerprints(domain: str) -> Dict[str, str]:
    """
    Fetches the homepage and extracts both HTML script patterns and HTTP headers.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    urls_to_check = [
        f"https://www.{domain}",
        f"https://watch.{domain}",
        f"https://tv.{domain}"
    ]
    
    found_tech = {
        "incumbent_vendor": "unknown",
        "video_player": "unknown",
        "ovp": "unknown",
        "cdn": "unknown"
    }

    raw_html = ""
    response_headers = {}
    
    for url in urls_to_check:
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                raw_html += resp.text.lower()
                response_headers = {k.lower(): v.lower() for k, v in resp.headers.items()}
                break  # Stop if we hit a valid page
        except requests.RequestException:
            continue
            
    if not raw_html:
        logger.warning(f"Technographics: Could not fetch HTML/Headers for {domain}")
        return found_tech

    # 1. Scan HTTP Headers (Zero-cost CDN identification)
    for header_key, mapped_value in HEADER_FINGERPRINTS.items():
        if header_key in response_headers:
            if isinstance(mapped_value, tuple):
                cat, vendor = mapped_value
                found_tech[cat] = vendor
            elif isinstance(mapped_value, dict):
                for keyword, (cat, vendor) in mapped_value.items():
                    if keyword in response_headers[header_key]:
                        found_tech[cat] = vendor

    # 2. Scan HTML for specific OTT vendor scripts
    for category, vendors in OTT_FINGERPRINTS.items():
        if found_tech[category] != "unknown":
            continue # Skip if already found via headers
            
        for vendor_name, patterns in vendors.items():
            for pattern in patterns:
                if re.search(pattern, raw_html):
                    found_tech[category] = vendor_name
                    break 
            
            if found_tech[category] != "unknown":
                break

    return found_tech


# ---------------------------------------------------------------------------
# Public Entry Point
# ---------------------------------------------------------------------------

@track_performance("technographics")
def get_technographic_footprint(prospect: dict) -> dict:
    """
    Deterministically identifies the prospect's OTT tech stack.
    Injects data into prospect['tech_stack_fingerprint'].
    """
    domain = prospect.get("domain", "")
    company = prospect.get("name", "Unknown")
    
    if not domain:
        return prospect

    logger.info(f"Technographics: Scanning {domain} for OTT fingerprints (Headers + HTML)...")
    
    tech_stack = _scrape_fingerprints(domain)
    
    # Add displacement angle for Claude Opus to use in outreach
    displacement_angle = ""
    incumbent = tech_stack.get("incumbent_vendor", "unknown")
    
    if incumbent == "ViewLift":
        displacement_angle = "Target aging infrastructure, slow releases, and OEM certification delays."
    elif incumbent == "24i":
        displacement_angle = "Target slow release cycles, thin sports architecture, and recent parent company instability."
    elif incumbent == "3SS":
        displacement_angle = "Target rigid white-label constraints and limited SSAI customization."
    elif incumbent == "Endeavor Streaming":
        displacement_angle = "Target high TCO, enterprise lock-in, and slow post-WME restructuring."
        
    tech_stack["incumbent_displacement_angle"] = displacement_angle
    
    prospect["tech_stack_fingerprint"] = tech_stack
    
    if incumbent != "unknown":
        logger.info(f"  ✓ {company}: Found Incumbent Vendor -> {incumbent}")
    else:
        logger.info(f"  - {company}: No incumbent vendor fingerprinted (Likely in-house).")
        
    return prospect