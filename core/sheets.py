"""
core/sheets.py — Google Sheets persistence (Multi-Tab Edition).

Updated to support routing leads to "Leads" (Hot/Warm) or "Cold Leads" 
tabs based on the Analyst's verdict. 
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional, Set

import gspread
from google.oauth2.service_account import Credentials

import config

logger = logging.getLogger("ott_lead_gen.sheets")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

LOG_COLUMNS = [
    "Timestamp",
    "Run ID",
    "Query",
    "Company",
    "Domain",
    "Step",
    "Status",
    "Detail",
    "Tokens In",
    "Tokens Out",
    "Credits",
    "Cost USD",
    "Error",
    "Duration ms",
]

class SheetsClient:
    """
    Writes lead intelligence to Google Sheets.
    Supports routing to separate 'Hot' and 'Cold' worksheets.
    """

    def __init__(self) -> None:
        self._ss: Optional[gspread.Spreadsheet] = None
        self._ws_hot: Optional[gspread.Worksheet] = None
        self._ws_cold: Optional[gspread.Worksheet] = None
        self._ws_logs: Optional[gspread.Worksheet] = None
        self._seen_domains: Set[str] = set()
        self._seen_pairs: Set[tuple] = set()   # (domain, contact_name)

    # ------------------------------------------------------------------
    # Connection logic
    # ------------------------------------------------------------------

    def _connect(self) -> None:
        """Initialize the spreadsheet connection and both worksheets."""
        if self._ss is not None:
            return

        import json, os
        sa_path = config.GOOGLE_SERVICE_ACCOUNT_JSON
        if os.path.exists(sa_path):
            creds = Credentials.from_service_account_file(sa_path, scopes=SCOPES)
        else:
            sa_info = None
            try:
                import streamlit as st
                if "GOOGLE_SERVICE_ACCOUNT_JSON" in st.secrets:
                    sa_info = dict(st.secrets["GOOGLE_SERVICE_ACCOUNT_JSON"])
            except Exception:
                pass
            if sa_info is None:
                sa_info = json.loads(os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "{}"))
            creds = Credentials.from_service_account_info(sa_info, scopes=SCOPES)
        gc = gspread.authorize(creds)
        try:
            self._ss = gc.open(config.GOOGLE_SHEET_NAME)
        except gspread.SpreadsheetNotFound:
            logger.info(f"Sheets: creating new spreadsheet '{config.GOOGLE_SHEET_NAME}'")
            self._ss = gc.create(config.GOOGLE_SHEET_NAME)

        # Connect/Create Hot Worksheet
        self._ws_hot = self._get_or_create_ws(config.GOOGLE_WORKSHEET_NAME)
        
        # Connect/Create Cold Worksheet
        self._ws_cold = self._get_or_create_ws(config.GOOGLE_COLD_WORKSHEET_NAME)

        #connect to logs worksheet
        self._ws_logs = self._get_or_create_ws(config.GOOGLE_LOGS_WORKSHEET_NAME, LOG_COLUMNS)

        self._load_dedup_cache()

    def _get_or_create_ws(self, title: str, columns: list = None) -> gspread.Worksheet:
        cols = columns or config.SHEET_COLUMNS
        try:
            ws = self._ss.worksheet(title)
        except gspread.WorksheetNotFound:
            ws = self._ss.add_worksheet(
                title=title,
                rows=10000,
                cols=len(cols),
            )
        if not ws.row_values(1) or ws.row_values(1) != cols:
            ws.update("A1", [cols])
            logger.info(f"Sheets: header row written for '{title}'")
        return ws

    # ------------------------------------------------------------------
    # Dedup cache (Checks BOTH sheets)
    # ------------------------------------------------------------------

    def _load_dedup_cache(self) -> None:
        """Load records from both tabs to prevent re-researching any lead."""
        for ws in [self._ws_hot, self._ws_cold]:
            try:
                records = ws.get_all_records()
                for r in records:
                    domain = r.get("Domain", "").strip().lower()
                    contact = r.get("Apollo Contact Name", "").strip().lower()
                    if domain:
                        self._seen_domains.add(domain)
                    if domain and contact:
                        self._seen_pairs.add((domain, contact))
            except Exception as exc:
                logger.warning(f"Sheets: could not load cache for '{ws.title}': {exc}")
        
        logger.info(f"Sheets: dedup cache loaded — {len(self._seen_domains)} total domains")

    def is_duplicate(self, domain: str, contact_name: str = "") -> bool:
        d = domain.strip().lower()
        if config.DEDUP_STRATEGY == "domain+contact" and contact_name:
            return (d, contact_name.strip().lower()) in self._seen_pairs
        return d in self._seen_domains

    def _mark_seen(self, domain: str, contact_name: str = "") -> None:
        d = domain.strip().lower()
        self._seen_domains.add(d)
        if contact_name:
            self._seen_pairs.add((d, contact_name.strip().lower()))

    # ------------------------------------------------------------------
    # Write Logic
    # ------------------------------------------------------------------

    def append_lead(
        self,
        prospect: dict,
        analyst: dict,
        emails: dict,
        contact=None,
        query: str = "",
        is_cold: bool = False  # NEW FLAG added here
    ) -> bool:
        """
        Write a single lead row to either the HOT or COLD worksheet.
        """
        domain = prospect.get("domain", "")
        company = prospect.get("name", "")
        contact_name = contact.name if contact else ""

        self._connect()

        if self.is_duplicate(domain, contact_name):
            logger.info(f"Sheets: SKIP duplicate — {company} / {contact_name or 'no contact'}")
            return False

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        # Unpack fields (same as your original logic)
        score = analyst.get("refined_score", prospect.get("opportunity_score", ""))
        priority = prospect.get("priority", "")
        opp_type = prospect.get("opportunity_type", "")
        gap = prospect.get("transition_gap_timer", "")
        inflection = prospect.get("causal_inflection", "")
        stack = prospect.get("tech_stack_fingerprint", {})
        app = prospect.get("app_intelligence", {})
        pm = prospect.get("power_map", {})
        visionary = pm.get("the_visionary", {})
        operator = pm.get("the_operator", {})
        outreach = prospect.get("outreach", {})

        signals = prospect.get("signals", [])
        top_sig = signals[0] if signals else {}

        obj = outreach.get("objection_stack", [{}, {}, {}])
        obj_inhouse   = obj[0].get("counter", "") if len(obj) > 0 else ""
        obj_incumbent = obj[1].get("counter", "") if len(obj) > 1 else ""
        obj_budget    = obj[2].get("counter", "") if len(obj) > 2 else ""

        vis_email = emails.get("visionary_email", {})
        ops_email = emails.get("operator_email", {})

        apollo_name     = contact.name if contact else ""
        apollo_title    = contact.title if contact else ""
        apollo_email    = contact.email if contact else ""
        apollo_linkedin = contact.linkedin_url if contact else ""

        # Exa columns — read from _exa_sheet_quote/_exa_sheet_pain injected by exa.py.
        # These fields merge LinkedIn post intel + JD tech debt into the two sheet cells.
        # Falls back gracefully to empty string when Exa is not active.
        vis_exa_quote = str(visionary.get("_exa_sheet_quote", "") or "")[:300]
        vis_exa_pain  = str(visionary.get("_exa_sheet_pain",  "") or "")[:300]
        ops_exa_quote = str(operator.get("_exa_sheet_quote",  "") or "")[:300]
        ops_exa_pain  = str(operator.get("_exa_sheet_pain",   "") or "")[:300]

        row = [
            ts, company, domain, priority, f"{score}/100" if score else "",
            opp_type, gap, inflection, _strip_citation(stack.get("incumbent_vendor", "") or ""),
            _fmt_stack(stack), str(app.get("ios_rating", "") or ""),
            str(app.get("android_rating", "") or ""),
            top_sig.get("evidence", "")[:300],
            top_sig.get("source_url", top_sig.get("source_type", "")),
            top_sig.get("confidence", ""), top_sig.get("against", ""),
            visionary.get("name", ""), visionary.get("title", ""),
            visionary.get("linkedin", ""), visionary.get("angle", ""),
            operator.get("name", ""), operator.get("title", ""),
            operator.get("linkedin", ""), operator.get("angle", ""),
            vis_email.get("subject_line", ""), vis_email.get("body", ""),
            ops_email.get("subject_line", ""), ops_email.get("body", ""),
            obj_inhouse, obj_incumbent, obj_budget,
            # Exa LinkedIn intelligence (cols 32-35)
            vis_exa_quote, vis_exa_pain, ops_exa_quote, ops_exa_pain,
            # Apollo enrichment (cols 36-39)
            apollo_name, apollo_title, apollo_email, apollo_linkedin,
            outreach.get("salesforce_note", ""), str(prospect.get("research_gaps", "")),
            query, "New"
        ]

        # Route to the correct worksheet object
        target_ws = self._ws_cold if is_cold else self._ws_hot

        if len(row) != len(config.SHEET_COLUMNS):
            logger.error(f"Row mismatch in {target_ws.title} for {company}.")
            return False

        target_ws.append_row(row, value_input_option="RAW")
        self._mark_seen(domain, contact_name)

        logger.info(
            f"Sheets: WRITTEN TO {'COLD' if is_cold else 'HOT'} — {company} | score={score}"
        )
        return True

    def write_log(
        self,
        run_id: str,
        query: str,
        company: str,
        domain: str,
        step: str,
        status: str,
        detail: str = "",
        tokens_in: int = 0,
        tokens_out: int = 0,
        credits: int = 0,
        cost_usd: float = 0.0,
        error: str = "",
        duration_ms: int = 0,
    ) -> None:
        """Write one API call audit row to the Logs worksheet."""
        self._connect()
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        row = [
            ts,
            run_id,
            query[:80],
            company,
            domain,
            step,
            status,
            detail[:300] if detail else "",
            tokens_in or "",
            tokens_out or "",
            credits or "",
            round(cost_usd, 4) if cost_usd else "",
            error[:300] if error else "",
            duration_ms or "",
        ]
        try:
            self._ws_logs.append_row(row, value_input_option="RAW")
        except Exception as exc:
            logger.warning(f"Sheets: log write failed for '{company}' / {step}: {exc}")

def _strip_citation(value: str) -> str:
    """
    Strip inline (Source: ...) citations Grok embeds in field values.
    Grok correctly cites sources inline but it pollutes identity fields
    like Incumbent Vendor where we want just the vendor name.
    Handles: 'Akta (Source: akta.tech/...)' → 'Akta'
    """
    import re
    if not value:
        return value
    cleaned = re.sub(
        r'\s*\((?:Source|source|via|Via|from|From|ref|Ref)[^)]*\)', '', value
    ).strip()
    cleaned = re.sub(r'\s*\(https?://[^)]+\)', '', cleaned).strip()
    return cleaned


def _fmt_stack(stack: dict) -> str:
    parts = []
    for key in ("video_player", "cdn", "ovp", "drm", "ssai"):
        val = _strip_citation(stack.get(key, "") or "")
        if val:
            parts.append(f"{key.replace('_', ' ').title()}: {val}")
    return " | ".join(parts)
