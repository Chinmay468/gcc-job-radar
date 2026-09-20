"""AccioJob Hiring Portal & Placement Client for gcc-job-radar.

Scrapes and extracts early-career tech job drives, fresher opportunities,
and hiring partner postings from AccioJob portal and digest feeds.
"""

import asyncio
import hashlib
import html
import logging
from pathlib import Path
import re
from typing import Any, Optional

import httpx

from gcc_job_radar.company_tracker import is_known_company, register_hiring_company
from gcc_job_radar.filters import (
    is_entry_level,
    is_potential_india_location,
    is_remote_opening,
    is_tech_role,
    requires_experienced_candidate,
)
from gcc_job_radar.link_resolver import (
    build_direct_careers_search_url,
    build_direct_search_url,
    unwrap_destination_url,
)
from gcc_job_radar.models import ATSProvider, JobPosting
from gcc_job_radar.relevance import calculate_relevance_score, evaluate_job_relevance

logger = logging.getLogger(__name__)

ACCIOJOB_JOBS_URL = "https://acciojob.com/jobs"
ACCIOJOB_PLACEMENT_URL = "https://placement.acciojob.com/"
ACCIOJOB_HIGHLIGHTS_URL = "https://acciojob.com/placement-highlights"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 gcc-job-radar/1.0"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Promotional phrases to discard
_PROMO_PATTERN = re.compile(
    r"""(?ix)
    \b(
        fast\s*track|placement\s*program|scholarship|course|bootcamp|
        bridge\s*your\s*skill|pay\s*after\s*placement|tuition
    )\b
    """
)

# Minimum relevance score or core stack keywords required to qualify as "good for me"
CORE_STACK_KEYWORDS = re.compile(
    r"""(?ix)
    \b(
        java|spring\s*boot|react|react\.js|node|node\.js|mern|express|
        mongodb|mysql|kafka|docker|full\s*stack|fullstack|backend|
        software\s*engineer|sde|graduate\s*software|swe|dsa|web\s*developer
    )\b
    """
)


def is_job_good_fit(
    title: str,
    skills_or_desc: str = "",
    company: str = "",
) -> tuple[bool, int, str]:
    """Check if a role matches the user's stack and early-career profile.

    Target Stack:
    - Java / Spring Boot 3
    - MERN (MongoDB, Express, React, Node.js)
    - MySQL, Relational DBs, Kafka, Docker
    - SDE / Full Stack / Backend / Frontend / Software Engineering entry-level roles

    Returns:
        tuple[bool, int, str]: (is_good_fit, score, reason)
    """
    clean_title = (title or "").strip()
    clean_desc = (skills_or_desc or "").strip()
    clean_comp = (company or "").strip()

    # 1. Reject promotions / ads
    if _PROMO_PATTERN.search(clean_title) or _PROMO_PATTERN.search(clean_comp):
        return False, 0, "Promotional placement ad or bootcamp"

    # 2. Reject explicit experienced hire requirements (2.5+ YOE)
    if requires_experienced_candidate(clean_desc):
        return False, 0, "Demands experienced candidate (2.5+ YOE)"

    # 3. Reject non-tech professions (e.g. Mis Executive, Sales, HR, Accounting)
    non_tech = re.compile(
        r"""(?ix)
        \b(
            mis\s+executive|excel\s+entry|telecaller|customer\s+support|
            sales\s+executive|bdr|sdr|marketing|accountant|audit|
            doctor|nurse|receptionist|back\s+office
        )\b
        """
    )
    if non_tech.search(clean_title):
        return False, 0, "Non-tech profession"

    # 4. Evaluate relevance against target stack
    score, reasons, why = evaluate_job_relevance(clean_title, clean_desc)

    # If title or skills contain core stack (Java, Spring, React, Node, Full Stack, SDE)
    combined = f"{clean_title} {clean_desc}".lower()
    has_core_match = bool(CORE_STACK_KEYWORDS.search(combined))

    # Data Analyst with only Excel / PowerBI without core programming is not target SDE stack
    if "data analyst" in clean_title.lower() and not any(k in combined for k in ("java", "spring", "react", "node", "django", "fastapi")):
        if score < 20:
            return False, score, "General Data Analyst role without target software stack"

    if has_core_match or score >= 20:
        return True, max(score, 20), why

    # Generic tech intern / trainee with tech skills
    if is_entry_level(clean_title, content=f"fresher {clean_desc}") and is_tech_role(clean_title, department=clean_desc):
        return True, max(score, 10), why

    return False, score, "Does not match target stack (Java/Spring/MERN/SWE)"


def parse_acciojob_digest_table(text: str) -> list[dict[str, Any]]:
    """Parse raw TSV or copied table text from AccioJob job feeds.

    Expected format:
    Company\\tRole\\t\\nSkills...\\nDate\\tSource\\t
    """
    records: list[dict[str, Any]] = []

    pattern = re.compile(
        r"([^\t\n\r]+)\t([^\t\n\r]+)\t\s*\n([\s\S]*?)\n([0-9]{1,2}\s+[A-Za-z]+\s+[0-9]{4})\t([^\t\n\r]+)\t?",
        re.MULTILINE,
    )

    for m in pattern.finditer(text):
        comp, role, skills, date_str, src = m.groups()
        comp_clean = comp.strip()
        role_clean = role.strip()
        skills_list = [s.strip() for s in skills.splitlines() if s.strip()]
        skills_str = ", ".join(skills_list)
        src_clean = src.strip()

        if _PROMO_PATTERN.search(comp_clean) or _PROMO_PATTERN.search(role_clean):
            continue

        records.append({
            "company": comp_clean,
            "title": role_clean,
            "skills": skills_str,
            "date": date_str.strip(),
            "source": src_clean,
        })

    return records


class AccioJobClient:
    """Harvester client for AccioJob hiring portal and job feeds."""

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout

    async def fetch_portal_jobs(self) -> list[JobPosting]:
        """Fetch and extract active job drives and hiring partner roles from AccioJob."""
        postings: list[JobPosting] = []

        async with httpx.AsyncClient(timeout=self.timeout, headers=DEFAULT_HEADERS, follow_redirects=True) as client:
            # 1. Fetch https://acciojob.com/jobs
            try:
                resp = await client.get(ACCIOJOB_JOBS_URL)
                if resp.status_code == 200:
                    html_text = resp.text
                    postings.extend(self._extract_jobs_from_html(html_text, source_url=ACCIOJOB_JOBS_URL))
            except Exception as exc:
                logger.debug("Failed fetching %s: %s", ACCIOJOB_JOBS_URL, exc)

            # 2. Fetch https://placement.acciojob.com/
            try:
                resp = await client.get(ACCIOJOB_PLACEMENT_URL)
                if resp.status_code == 200:
                    html_text = resp.text
                    postings.extend(self._extract_jobs_from_html(html_text, source_url=ACCIOJOB_PLACEMENT_URL))
            except Exception as exc:
                logger.debug("Failed fetching %s: %s", ACCIOJOB_PLACEMENT_URL, exc)

        return postings

    def _extract_jobs_from_html(self, html_content: str, source_url: str = ACCIOJOB_JOBS_URL) -> list[JobPosting]:
        """Extract job cards and hiring partner opportunities from AccioJob HTML markup."""
        results: list[JobPosting] = []
        seen_roles: set[tuple[str, str]] = set()

        # Look for table rows or opportunity cards
        # 1. Table rows: <tr>...<td>Company</td><td>Role</td>...</tr>
        row_pattern = re.compile(r"<tr[^>]*>([\s\S]*?)</tr>", re.I)
        for row_m in row_pattern.finditer(html_content):
            row_html = row_m.group(1)
            tds = re.findall(r"<td[^>]*>([\s\S]*?)</td>", row_html, re.I)
            if len(tds) >= 2:
                # Strip tags
                clean_td0 = re.sub(r"<[^>]+>", " ", tds[0]).strip()
                clean_td1 = re.sub(r"<[^>]+>", " ", tds[1]).strip()
                skills_td = re.sub(r"<[^>]+>", " ", " ".join(tds[2:])).strip() if len(tds) > 2 else ""

                if not clean_td0 or not clean_td1 or len(clean_td0) < 2 or len(clean_td1) < 2:
                    continue

                good_fit, score, reason = is_job_good_fit(clean_td1, skills_td, clean_td0)
                if not good_fit:
                    continue

                key = (clean_td0.lower(), clean_td1.lower())
                if key in seen_roles:
                    continue
                seen_roles.add(key)

                # Find apply link
                m_a = re.search(r"""href=["'](?P<url>https?://[^"']+)["']""", row_html, re.I)
                apply_url = m_a.group("url") if m_a else build_direct_careers_search_url(clean_td0, clean_td1)

                posting = self._create_job_posting(clean_td0, clean_td1, skills_td, apply_url)
                if posting:
                    results.append(posting)

        # 2. Look for structured JSON-LD or Next.js state
        m_scripts = re.findall(r"""<script[^>]+type=["']application/ld\+json["'][^>]*>(.*?)</script>""", html_content, re.I | re.DOTALL)
        for script_body in m_scripts:
            try:
                import json
                data = json.loads(script_body)
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if isinstance(item, dict) and item.get("@type") == "JobPosting":
                        t = str(item.get("title") or "").strip()
                        org = item.get("hiringOrganization")
                        c = org.get("name") if isinstance(org, dict) else str(org or "")
                        u = str(item.get("url") or "").strip() or build_direct_careers_search_url(c, t)
                        desc = str(item.get("description") or "").strip()

                        good_fit, score, reason = is_job_good_fit(t, desc, c)
                        if good_fit:
                            key = (c.lower(), t.lower())
                            if key not in seen_roles:
                                seen_roles.add(key)
                                posting = self._create_job_posting(c, t, desc, u)
                                if posting:
                                    results.append(posting)
            except Exception:
                continue

        return results

    def _create_job_posting(
        self,
        company: str,
        title: str,
        skills_or_desc: str,
        apply_url: str,
        date_str: str = "Recent",
    ) -> Optional[JobPosting]:
        """Build normalized JobPosting and auto-register company into tracked registry."""
        comp_clean = company.strip()
        title_clean = title.strip()
        url_clean = unwrap_destination_url(apply_url) or apply_url.strip()

        job_hash = hashlib.sha256(f"{comp_clean.lower()}_{title_clean.lower()}_{url_clean}".encode("utf-8")).hexdigest()[:16]
        job_id = f"acciojob_{job_hash}"

        # Auto-register company into config.COMPANIES if not already tracked
        try:
            if not is_known_company(comp_clean):
                register_hiring_company(
                    comp_clean,
                    career_url=url_clean if "careers" in url_clean.lower() else None,
                )
        except Exception as exc:
            logger.debug("Error auto-registering company %s: %s", comp_clean, exc)

        direct_search = build_direct_search_url(comp_clean, title_clean)

        try:
            return JobPosting(
                id=job_id,
                company=comp_clean,
                title=title_clean,
                location="India",
                apply_url=url_clean,
                provider=ATSProvider.ACCIOJOB,
                published_date=date_str,
                is_remote="remote" in f"{title_clean} {skills_or_desc}".lower(),
                status="NEW",
                direct_search_url=direct_search,
            )
        except Exception as exc:
            logger.debug("Failed creating JobPosting for %s @ %s: %s", title_clean, comp_clean, exc)
            return None


def ingest_acciojob_digest_text(text: str) -> list[JobPosting]:
    """Ingest, evaluate, and persist job entries from an AccioJob digest table or paste.

    Args:
        text: Raw copied table or digest string.

    Returns:
        List of qualified JobPosting objects that match the user's stack and were recorded.
    """
    records = parse_acciojob_digest_table(text)
    if not records:
        logger.warning("No records matched the AccioJob table format.")
        return []

    client = AccioJobClient()
    qualified: list[JobPosting] = []

    for rec in records:
        comp = rec["company"]
        role = rec["title"]
        skills = rec["skills"]
        date_str = rec.get("date", "Recent")
        source = rec.get("source", "Company Career Page")

        good_fit, score, reason = is_job_good_fit(role, skills, comp)
        if not good_fit:
            logger.debug("Skipped '%s' @ %s — %s", role, comp, reason)
            continue

        # Form direct career or apply URL
        apply_url = build_direct_careers_search_url(comp, role)

        posting = client._create_job_posting(
            company=comp,
            title=role,
            skills_or_desc=skills,
            apply_url=apply_url,
            date_str=date_str,
        )
        if posting:
            qualified.append(posting)

    return qualified
