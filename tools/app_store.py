"""
tools/app_store.py — Deterministic App Store Velocity Tracker
=============================================================
Fetches live App Store and Google Play data to calculate the 
"Release Velocity Delta" (days since last update). 

Requires: pip install google-play-scraper
"""

import logging
from datetime import datetime, timezone
import requests

from utils.helpers import with_retries, track_performance

logger = logging.getLogger("ott_lead_gen.app_store")

# ---------------------------------------------------------------------------
# Apple App Store (iOS / tvOS)
# ---------------------------------------------------------------------------

@with_retries(max_attempts=2, delay=2.0)
def _get_ios_app_data(company_name: str) -> dict:
    """
    Hits Apple's free iTunes Search API. 
    """
    url = f"https://itunes.apple.com/search?term={requests.utils.quote(company_name)}&entity=software&limit=1"
    
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results", [])
            
            if results:
                app = results[0]
                release_date_str = app.get("currentVersionReleaseDate", "")
                
                days_stale = -1
                if release_date_str:
                    release_date = datetime.strptime(release_date_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                    days_stale = (datetime.now(timezone.utc) - release_date).days

                return {
                    "found": True,
                    "app_name": app.get("trackName", ""),
                    "version": app.get("version", ""),
                    "rating": round(app.get("averageUserRating", 0.0), 1),
                    "days_since_update": days_stale,
                    "url": app.get("trackViewUrl", "")
                }
    except Exception as exc:
        logger.warning(f"iOS App Store search failed for '{company_name}': {exc}")
        
    return {"found": False}

# ---------------------------------------------------------------------------
# Google Play Store (Android / Android TV)
# ---------------------------------------------------------------------------

@with_retries(max_attempts=2, delay=2.0)
def _get_android_app_data(company_name: str, domain: str) -> dict:
    """
    Uses the free google-play-scraper library.
    We guess the package name (com.domain.app) and fall back to search.
    """
    try:
        from google_play_scraper import search, app
    except ImportError:
        logger.error("google-play-scraper not installed. Run: pip install google-play-scraper")
        return {"found": False}

    try:
        # Step 1: Search Play Store by company name
        search_results = search(company_name, lang="en", country="us")
        if not search_results:
            return {"found": False}
            
        # Step 2: Grab the top result's app ID and fetch full details
        app_id = search_results[0]["appId"]
        app_details = app(app_id, lang="en", country="us")
        
        # Play scraper returns a unix timestamp for 'updated'
        updated_ts = app_details.get("updated", 0)
        days_stale = -1
        if updated_ts:
            release_date = datetime.fromtimestamp(updated_ts, tz=timezone.utc)
            days_stale = (datetime.now(timezone.utc) - release_date).days

        return {
            "found": True,
            "app_name": app_details