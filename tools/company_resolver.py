"""company_resolver.py — Resolves company ATS career portals and Radar DB tracking status.

Matches company names against GCC Job Radar's 5,766 curated companies (config.COMPANIES)
and checks local SQLite database (gcc_jobs.db) for application history and active tracked jobs.
"""

from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path
import re
from typing import Any, Dict, Optional, Tuple
import urllib.parse

from gcc_job_radar import config
from gcc_job_radar.db import get_db_path, is_company_dismissed
from gcc_job_radar.models import ATSProvider, CompanyConfig
from tools.discovery_utils import (
    clean_company_name,
    normalize_company_name,
    generate_slug_candidates,
    KNOWN_ABBREVIATIONS,
)

logger = logging.getLogger(__name__)

# Geographic, generic division, and business unit tokens to strip for core company matching
GEO_TOKENS = {
    "india", "bangalore", "bengaluru", "hyderabad", "pune", "gurgaon", "gurugram",
    "mumbai", "noida", "chennai", "delhi", "apac", "emea", "us", "usa", "uk",
    "global", "labs", "technologies", "technology", "solutions", "services", "digital",
}

# Pre-indexed caches for O(1) matching against config.COMPANIES
_EXACT_MAP: Dict[str, CompanyConfig] = {}
_NORM_MAP: Dict[str, CompanyConfig] = {}
_SLUG_MAP: Dict[str, CompanyConfig] = {}


def _init_caches() -> None:
    """Populate fast lookup dictionaries from config.COMPANIES once."""
    global _EXACT_MAP, _NORM_MAP, _SLUG_MAP
    if _EXACT_MAP:
        return
    for c in config.COMPANIES:
        name_lower = c.name.lower().strip()
        _EXACT_MAP[name_lower] = c
        norm = normalize_company_name(c.name)
        if norm:
            _NORM_MAP[norm] = c
        if c.board_token:
            _SLUG_MAP[c.board_token.lower().strip()] = c


def strip_geo_and_division_tokens(name: str) -> str:
    """Strip common geographical and business division modifiers (e.g. 'Databricks India' -> 'Databricks')."""
    cleaned = clean_company_name(name)
    words = [w for w in cleaned.split() if w.lower().strip(".,") not in GEO_TOKENS]
    return " ".join(words) if words else cleaned


def lookup_in_radar_registry(company_name: str) -> Optional[CompanyConfig]:
    """Look up a company in the 5,766 monitored COMPANIES list using multi-stage matching."""
    _init_caches()
    if not company_name or not company_name.strip():
        return None

    raw = company_name.strip()
    raw_lower = raw.lower()

    # 1. Exact case-insensitive match
    if raw_lower in _EXACT_MAP:
        return _EXACT_MAP[raw_lower]

    # 2. Cleaned corporate suffix match (Inc, Corp, Ltd, etc.)
    cleaned = clean_company_name(raw)
    cleaned_lower = cleaned.lower()
    if cleaned_lower in _EXACT_MAP:
        return _EXACT_MAP[cleaned_lower]

    # 3. Geo/division stripped match (e.g. 'Databricks India' -> 'Databricks')
    geo_cleaned = strip_geo_and_division_tokens(raw)
    geo_lower = geo_cleaned.lower()
    if geo_lower in _EXACT_MAP:
        return _EXACT_MAP[geo_lower]

    # 4. Normalized alphanumeric match
    norm = normalize_company_name(cleaned)
    if norm in _NORM_MAP:
        return _NORM_MAP[norm]
    norm_geo = normalize_company_name(geo_cleaned)
    if norm_geo in _NORM_MAP:
        return _NORM_MAP[norm_geo]

    # 5. Known abbreviation lookup (e.g. 'TI' -> 'Texas Instruments')
    for canonical_name, abbrevs in KNOWN_ABBREVIATIONS.items():
        if raw_lower in abbrevs or cleaned_lower in abbrevs:
            if canonical_name in _EXACT_MAP:
                return _EXACT_MAP[canonical_name]

    # 6. Slug candidate match against board_token
    slug_candidates = generate_slug_candidates(geo_cleaned or cleaned)
    for slug in slug_candidates:
        if slug in _SLUG_MAP:
            return _SLUG_MAP[slug]

    return None


def get_canonical_ats_url(comp: CompanyConfig) -> Tuple[str, str]:
    """Return canonical direct ATS URL and human-friendly action label for a company."""
    if comp.career_url:
        u_lower = comp.career_url.lower()
        if "turbohire" in u_lower:
            return comp.career_url, "Open TurboHire Portal ↗"
        elif "workday" in u_lower:
            return comp.career_url, "Open Workday Portal ↗"
        return comp.career_url, "Open Official Careers ↗"

    prov = comp.provider
    token = comp.board_token

    if prov == ATSProvider.GREENHOUSE or str(prov).lower() == "greenhouse":
        return f"https://boards.greenhouse.io/{token}", "Open Greenhouse Portal ↗"
    elif prov == ATSProvider.LEVER or str(prov).lower() == "lever":
        return f"https://jobs.lever.co/{token}", "Open Lever Portal ↗"
    elif prov == ATSProvider.ASHBY or str(prov).lower() == "ashby":
        return f"https://jobs.ashbyhq.com/{token}", "Open Ashby Portal ↗"
    elif prov == ATSProvider.SMARTRECRUITERS or str(prov).lower() == "smartrecruiters":
        return f"https://jobs.smartrecruiters.com/{token}", "Open SmartRecruiters ↗"
    elif prov == ATSProvider.AMAZON or str(prov).lower() == "amazon":
        return "https://www.amazon.jobs", "Open Amazon Jobs ↗"
    elif prov == ATSProvider.MICROSOFT or str(prov).lower() == "microsoft":
        return "https://careers.microsoft.com", "Open Microsoft Careers ↗"
    elif prov == ATSProvider.APPLE or str(prov).lower() == "apple":
        return "https://jobs.apple.com", "Open Apple Jobs ↗"
    elif prov == ATSProvider.EA or str(prov).lower() == "ea":
        return "https://ea.gr8people.com", "Open EA Careers ↗"
    elif prov == ATSProvider.WORKDAY or str(prov).lower() == "workday":
        if comp.career_url:
            return comp.career_url, "Open Workday Portal ↗"
        return (
            f"https://www.google.com/search?q={urllib.parse.quote_plus(comp.name + ' workday jobs careers')}",
            "Search Workday Portal ↗",
        )
    else:
        return (
            f"https://www.google.com/search?q={urllib.parse.quote_plus(comp.name + ' careers official portal')}",
            "Search Official Careers ↗",
        )


def get_radar_db_status(
    company_name: str,
    title: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Query seen_jobs and dismissals for application history, active roles, and suppression."""
    target_db = get_db_path(db_path)
    if not target_db.exists():
        return {
            "job_status": "NEW",
            "applied": False,
            "applied_date": None,
            "applied_role": None,
            "active_roles_count": 0,
            "is_dismissed": False,
            "last_seen_at": None,
        }

    import sqlite3

    comp_clean = strip_geo_and_division_tokens(company_name).lower().strip()
    is_dismissed = is_company_dismissed(company_name, db_path=target_db)

    with sqlite3.connect(target_db) as conn:
        cursor = conn.cursor()

        # 1. Check if user already applied to this company or role
        cursor.execute(
            """
            SELECT id, title, status, applied_at, last_seen_at
            FROM seen_jobs
            WHERE (lower(company) = ? OR lower(company) LIKE ?) AND status = 'APPLIED'
            ORDER BY applied_at DESC, last_seen_at DESC
            LIMIT 1
            """,
            (comp_clean, f"%{comp_clean}%"),
        )
        applied_row = cursor.fetchone()

        # 2. Check if interviewing or rejected
        interview_row = None
        if not applied_row:
            cursor.execute(
                """
                SELECT id, title, status, last_seen_at
                FROM seen_jobs
                WHERE (lower(company) = ? OR lower(company) LIKE ?) AND status IN ('INTERVIEWING', 'REJECTED')
                ORDER BY last_seen_at DESC
                LIMIT 1
                """,
                (comp_clean, f"%{comp_clean}%"),
            )
            interview_row = cursor.fetchone()

        # 3. Check if marked DISMISSED in seen_jobs
        dismissed_row = None
        if not applied_row and not interview_row:
            cursor.execute(
                """
                SELECT id, title, status, notes
                FROM seen_jobs
                WHERE (lower(company) = ? OR lower(company) LIKE ?) AND status = 'DISMISSED'
                LIMIT 1
                """,
                (comp_clean, f"%{comp_clean}%"),
            )
            dismissed_row = cursor.fetchone()

        # 4. Check active roles count and last seen
        cursor.execute(
            """
            SELECT COUNT(*), MAX(last_seen_at)
            FROM seen_jobs
            WHERE (lower(company) = ? OR lower(company) LIKE ?) AND is_active = 1
            """,
            (comp_clean, f"%{comp_clean}%"),
        )
        count_row = cursor.fetchone()
        active_count = count_row[0] if count_row else 0
        last_seen = count_row[1] if count_row else None

    if applied_row:
        app_date_raw = applied_row[3] or applied_row[4] or ""
        app_date_fmt = app_date_raw
        if app_date_raw:
            try:
                m = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(app_date_raw))
                if m:
                    dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                    app_date_fmt = dt.strftime("%b %d, %Y")
            except Exception:
                pass

        return {
            "job_status": "APPLIED",
            "applied": True,
            "applied_date": app_date_fmt,
            "applied_role": applied_row[1],
            "active_roles_count": active_count,
            "is_dismissed": False,
            "last_seen_at": applied_row[4],
        }

    if interview_row:
        return {
            "job_status": interview_row[2],
            "applied": True,
            "applied_date": None,
            "applied_role": interview_row[1],
            "active_roles_count": active_count,
            "is_dismissed": False,
            "last_seen_at": interview_row[3],
        }

    if is_dismissed or dismissed_row:
        return {
            "job_status": "DISMISSED",
            "applied": False,
            "applied_date": None,
            "applied_role": None,
            "active_roles_count": active_count,
            "is_dismissed": True,
            "last_seen_at": last_seen,
        }

    return {
        "job_status": "SEEN" if active_count > 0 else "NEW",
        "applied": False,
        "applied_date": None,
        "applied_role": None,
        "active_roles_count": active_count,
        "is_dismissed": False,
        "last_seen_at": last_seen,
    }


def resolve_company(
    company_name: str,
    title: Optional[str] = None,
    current_url: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Full company intelligence resolution: registry lookup + canonical ATS URL + Radar DB status badge."""
    _init_caches()

    if not company_name or not company_name.strip():
        return {
            "found": False,
            "monitored": False,
            "company_name": "",
            "badge_type": "unknown",
            "badge_label": "No Company Detected",
            "history_text": "Paste or extract a job posting to check status.",
            "direct_ats_url": "",
            "direct_ats_label": "",
            "is_3rd_party_aggregator": False,
        }

    raw_comp = company_name.strip()
    config_entry = lookup_in_radar_registry(raw_comp)

    # Detect if user is currently on a 3rd party aggregator
    is_aggregator = False
    if current_url:
        u_lower = current_url.lower()
        is_aggregator = any(agg in u_lower for agg in (
            "linkedin.com", "indeed.com", "naukri.com", "wellfound.com", "angel.co",
            "instahyre.com", "glassdoor.com", "unstop.com", "ziprecruiter.com",
        ))

    db_info = get_radar_db_status(raw_comp, title=title, db_path=db_path)

    # Synthesize badge and canonical URLs
    if config_entry:
        monitored = True
        canonical_name = config_entry.name
        provider_name = (
            config_entry.provider.value
            if hasattr(config_entry.provider, "value")
            else str(config_entry.provider)
        )
        ats_url, ats_label = get_canonical_ats_url(config_entry)
    else:
        monitored = False
        canonical_name = clean_company_name(raw_comp)
        provider_name = "unmonitored"
        ats_url = f"https://www.google.com/search?q={urllib.parse.quote_plus(canonical_name + ' careers official site')}"
        ats_label = "Search Official Careers ↗"

    # Determine badge label and type
    if db_info["applied"]:
        badge_type = "applied"
        date_str = f" on {db_info['applied_date']}" if db_info.get("applied_date") else ""
        badge_label = f"✅ Applied{date_str}"
        history_text = f"Previously applied for {db_info.get('applied_role') or 'a role'} at {canonical_name}."
    elif db_info["job_status"] == "INTERVIEWING":
        badge_type = "interviewing"
        badge_label = "🤝 Interviewing"
        history_text = f"Active interview process tracked for {canonical_name}."
    elif db_info["is_dismissed"]:
        badge_type = "dismissed"
        badge_label = "🚫 Dismissed"
        history_text = f"{canonical_name} is currently suppressed in Radar alerts."
    elif monitored:
        badge_type = "monitored"
        prov_display = provider_name.capitalize()
        badge_label = f"🎯 Monitored GCC ({prov_display})"
        active_cnt = db_info.get("active_roles_count", 0)
        if active_cnt > 0:
            role_word = "active role" if active_cnt == 1 else "active roles"
            history_text = f"{active_cnt} {role_word} tracked in Radar database."
        else:
            history_text = f"Curated GCC monitored by Radar scanner ({prov_display} ATS)."
    else:
        badge_type = "unmonitored"
        badge_label = "🏢 Unmonitored Company"
        history_text = f"Not currently in GCC Radar registry. You can evaluate and tailor freely."

    return {
        "found": True,
        "monitored": monitored,
        "company_name": canonical_name,
        "provider": provider_name,
        "badge_type": badge_type,
        "badge_label": badge_label,
        "history_text": history_text,
        "direct_ats_url": ats_url,
        "direct_ats_label": ats_label,
        "is_3rd_party_aggregator": is_aggregator,
        "db_info": db_info,
    }
