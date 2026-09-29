"""
gcc_job_radar/internet_search.py — Live Internet & Multi-Board Job Search Engine.

Searches the live web and top ATS job boards on-demand for specific tech professions,
locations, stacks, and experience levels using:
  1. Google Search API (via Serper) targeting direct ATS endpoints (Greenhouse, Lever, Ashby, Workday, LinkedIn)
  2. Remotive & Arbeitnow free developer job feeds for remote/tech openings
  3. Integrated candidate tech-stack relevance scoring (relevance.py)
  4. Automatic deduplication against applied/dismissed jobs and persistence into gcc_jobs.db
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Optional, Sequence
import urllib.parse
from dotenv import load_dotenv
import httpx

from gcc_job_radar.config import EXCLUDE_TITLE_PATTERN
from gcc_job_radar.db import (
    canonicalize_url,
    get_applied_and_dismissed_companies,
    get_db_path,
    init_db,
    record_jobs,
)
from gcc_job_radar.filters import _MTS_MASK_PATTERN, requires_experienced_candidate
from gcc_job_radar.models import ATSProvider, JobPosting
from gcc_job_radar.relevance import score_job_posting

logger = logging.getLogger(__name__)
load_dotenv()


def _extract_company_and_title(raw_title: str, link: str, snippet: str = "") -> tuple[str, str]:
    """Extract clean company name and standardized job title from search result metadata."""
    title = raw_title.strip()
    company = "Unknown"

    parsed = urllib.parse.urlparse(link)
    domain = parsed.netloc.lower()
    path_parts = [p for p in parsed.path.split("/") if p]

    # 1. Deduce from canonical ATS URL path
    if "lever.co" in domain and path_parts:
        company = path_parts[0].replace("-", " ").title()
    elif "greenhouse.io" in domain and path_parts:
        company = path_parts[0].replace("-", " ").title()
    elif "ashbyhq.com" in domain and path_parts:
        company = path_parts[0].replace("-", " ").title()
    elif "myworkdayjobs.com" in domain:
        company = domain.split(".")[0].replace("-", " ").title()
    elif "linkedin.com" in domain:
        m_li = re.search(r"-at-([a-z0-9\-]+)-[0-9]+", parsed.path.lower())
        if m_li:
            company = m_li.group(1).replace("-", " ").title()

    # 2. Check title formats: "Role at Company" or "Role @ Company"
    m_at = re.search(
        r"^(.*?)\s+(?:at|@)\s+([A-Za-z0-9&.,\s\-]+?)(?:\s*[\-\|\–]|\s+in\s+|$)",
        title,
        re.IGNORECASE,
    )
    if m_at:
        title = m_at.group(1).strip()
        if company == "Unknown":
            company = m_at.group(2).strip()

    # 3. Check title formats: "Company - Role" or "Company: Role"
    m_dash = re.search(r"^([A-Za-z0-9&.,\s]{2,30})\s*[:\-\|\–]\s*(.*?)$", title)
    if m_dash and company == "Unknown":
        company = m_dash.group(1).strip()
        title = m_dash.group(2).strip()

    # Clean company name
    company = re.sub(r"(?i)\s+(?:careers?|jobs?|ltd|pvt|inc|technologies|group|india)$", "", company).strip()
    if not company or len(company) < 2:
        company = "Tech Company"

    # Clean title from geographical suffixes and junk
    title = re.sub(
        r"(?i)\s*[\-\|\–]\s*(?:Bengaluru|Bangalore|Hyderabad|Pune|Gurgaon|Noida|Mumbai|India|Karnataka|Remote|Jobs|LinkedIn|Lever|Greenhouse|Ashby).*$",
        "",
        title,
    ).strip()
    title = re.sub(r"^(?:Hiring\s+For|Urgent\s+Requirement\s+For)\s+", "", title, flags=re.IGNORECASE).strip()

    # If title still starts with company name, strip it
    if title.lower().startswith(company.lower()):
        title = re.sub(rf"^{re.escape(company)}\s*[:\-\|\–]?\s*", "", title, flags=re.IGNORECASE).strip()

    if not title:
        title = raw_title.split("-")[0].strip()

    return company, title


async def _search_serper_ats(
    client: httpx.AsyncClient,
    query: str,
    location: str,
    experience: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search Google index via Serper.dev specifically for ATS job boards and LinkedIn postings."""
    serper_key = os.getenv("SERPER_API_KEY")
    if not serper_key:
        logger.debug("SERPER_API_KEY not configured. Skipping Serper search.")
        return []

    # Construct targeted search query focusing on direct ATS boards
    clean_query = query.strip()
    clean_loc = location.strip() if location else "India"

    # Support modern AI / vibe coder queries with semantic expansion
    if re.search(r"\bvibe\s*cod(?:er|ing)?\b", clean_query, re.IGNORECASE):
        dork_query = '("vibe coder" OR "AI Engineer" OR "Full Stack" "Cursor" OR "LLM")'
    else:
        dork_query = f'"{clean_query}"'

    is_fresher_req = any(term in experience.lower() for term in ("entry", "fresher", "junior", "intern", "0-2", "new grad"))
    exp_exclusion = " -senior -lead -staff -principal -director -manager -vp -architect -head" if is_fresher_req else ""

    # Dork 1: Direct ATS boards (Greenhouse, Lever, Ashby, Workday)
    dork1 = f'(site:boards.greenhouse.io OR site:jobs.lever.co OR site:jobs.ashbyhq.com OR site:myworkdayjobs.com) {dork_query} "{clean_loc}"{exp_exclusion}'
    # Dork 2: Direct LinkedIn job views
    dork2 = f'site:in.linkedin.com/jobs/view {dork_query} "{clean_loc}"{exp_exclusion}'

    headers = {
        "X-API-KEY": serper_key,
        "Content-Type": "application/json",
    }

    results: list[dict[str, Any]] = []

    for dork in (dork1, dork2):
        try:
            resp = await client.post(
                "https://google.serper.dev/search",
                headers=headers,
                json={"q": dork, "gl": "in", "num": limit},
                timeout=12.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("organic", []):
                    link = item.get("link", "")
                    # Ignore directory / search aggregator index pages
                    if any(
                        bad in link.lower()
                        for bad in (
                            "/jobs/search",
                            "/jobs/in-",
                            "/jobs/java-",
                            "linkedin.com/jobs/collections",
                            "linkedin.com/jobs/developer-jobs",
                        )
                    ):
                        continue

                    title = item.get("title", "")
                    snippet = item.get("snippet", "")
                    comp, clean_title = _extract_company_and_title(title, link, snippet)

                    # Exclude senior roles if searching for fresher/entry-level
                    sanitized_t = _MTS_MASK_PATTERN.sub("mts_role", clean_title)
                    if is_fresher_req and EXCLUDE_TITLE_PATTERN.search(sanitized_t):
                        continue
                    if is_fresher_req and (requires_experienced_candidate(snippet) or requires_experienced_candidate(clean_title)):
                        continue

                    results.append({
                        "title": clean_title,
                        "company": comp,
                        "apply_url": link,
                        "snippet": snippet,
                        "source": "google_ats",
                    })
        except Exception as exc:
            logger.warning("Error querying Serper for '%s': %s", dork, exc)

    return results


async def _search_remotive_jobs(
    client: httpx.AsyncClient,
    query: str,
    location: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Query Remotive's public remote jobs API."""
    try:
        url = f"https://remotive.com/api/remote-jobs?search={urllib.parse.quote_plus(query)}&limit={limit * 2}"
        resp = await client.get(url, timeout=10.0)
        if resp.status_code != 200:
            return []

        data = resp.json()
        results: list[dict[str, Any]] = []

        # Keywords to match
        stop_words = {"a", "an", "the", "for", "in", "at", "to", "and", "or", "of", "with", "job", "jobs", "role", "roles", "fresher", "freshers", "opening", "openings", "hiring"}
        query_words = [w.lower() for w in re.findall(r"\b[A-Za-z0-9+#.-]+\b", query) if w.lower() not in stop_words and len(w) > 1]

        for item in data.get("jobs", []):
            title = item.get("title", "Software Engineer")
            sanitized_t = _MTS_MASK_PATTERN.sub("mts_role", title)
            # Strictly reject senior / lead roles from Remotive
            if EXCLUDE_TITLE_PATTERN.search(sanitized_t):
                continue

            # Must have at least one keyword match if specific query words provided
            tags = [str(t).lower() for t in item.get("tags", [])]
            search_corpus = f"{title} {' '.join(tags)}".lower()
            if query_words and not any(qw in search_corpus for qw in query_words):
                continue

            req_loc = str(item.get("candidate_required_location", "")).lower()
            if any(term in req_loc for term in ("worldwide", "anywhere", "india", "apac", "remote")) or not location:
                results.append({
                    "title": title,
                    "company": item.get("company_name", "Tech Startup"),
                    "apply_url": item.get("url", ""),
                    "snippet": f"Remote ({item.get('candidate_required_location', 'Worldwide')}) • Tags: {', '.join(item.get('tags', [])[:4])}",
                    "source": "remotive",
                })
        return results[:limit]
    except Exception as exc:
        logger.debug("Remotive API query error: %s", exc)
        return []


async def _search_arbeitnow_jobs(
    client: httpx.AsyncClient,
    query: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Query Arbeitnow's public job board API."""
    try:
        url = f"https://www.arbeitnow.com/api/job-board-api?search={urllib.parse.quote_plus(query)}"
        resp = await client.get(url, timeout=10.0)
        if resp.status_code != 200:
            return []

        data = resp.json()
        results: list[dict[str, Any]] = []

        stop_words = {"a", "an", "the", "for", "in", "at", "to", "and", "or", "of", "with", "job", "jobs", "role", "roles", "fresher", "freshers", "opening", "openings", "hiring"}
        query_words = [w.lower() for w in re.findall(r"\b[A-Za-z0-9+#.-]+\b", query) if w.lower() not in stop_words and len(w) > 1]

        for item in data.get("data", []):
            title = item.get("title", "Developer")
            sanitized_t = _MTS_MASK_PATTERN.sub("mts_role", title)
            # Strictly reject senior / lead roles from Arbeitnow
            if EXCLUDE_TITLE_PATTERN.search(sanitized_t):
                continue

            tags = [str(t).lower() for t in item.get("tags", [])]
            search_corpus = f"{title} {' '.join(tags)}".lower()
            if query_words and not any(qw in search_corpus for qw in query_words):
                continue

            is_remote = bool(item.get("remote", False))
            loc = item.get("location", "")
            if is_remote or "india" in loc.lower() or "remote" in loc.lower():
                results.append({
                    "title": title,
                    "company": item.get("company_name", "Company"),
                    "apply_url": item.get("url", ""),
                    "snippet": f"Location: {loc} • Tags: {', '.join(item.get('tags', [])[:4])}",
                    "source": "arbeitnow",
                })
        return results[:limit]
    except Exception as exc:
        logger.debug("Arbeitnow API query error: %s", exc)
        return []


async def search_internet_jobs(
    query: str,
    location: str = "India",
    experience: str = "entry-level",
    limit: int = 8,
    record_to_db: bool = True,
    db_path: Optional[Path] = None,
) -> list[JobPosting]:
    """
    Search the live internet and job boards for openings matching a role/stack/location,
    score them against Chinmay's profile, and optionally record them into the local database.
    """
    if not query or len(query.strip()) < 2:
        return []

    clean_query = query.strip()
    clean_location = location.strip() if location else "India"

    logger.info("Executing live internet job search: query='%s', location='%s', exp='%s'", clean_query, clean_location, experience)

    raw_items: list[dict[str, Any]] = []

    async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}) as client:
        # Run search tasks concurrently
        tasks = [
            _search_serper_ats(client, clean_query, clean_location, experience, limit=limit),
            _search_remotive_jobs(client, clean_query, clean_location, limit=4),
            _search_arbeitnow_jobs(client, clean_query, limit=3),
        ]
        results_nested = await asyncio.gather(*tasks, return_exceptions=True)

        for res in results_nested:
            if isinstance(res, list):
                raw_items.extend(res)

    if not raw_items:
        logger.info("No raw internet search results found for '%s'", clean_query)
        return []

    # Get user's applied and dismissed companies to avoid spamming
    try:
        applied_comps, dismissed_comps = get_applied_and_dismissed_companies(db_path)
    except Exception:
        applied_comps, dismissed_comps = set(), set()

    normalized_jobs: list[JobPosting] = []
    seen_urls: set[str] = set()
    seen_combos: set[tuple[str, str]] = set()

    for item in raw_items:
        apply_url_str = item.get("apply_url", "").strip()
        if not apply_url_str.startswith(("http://", "https://")):
            continue

        clean_url = canonicalize_url(apply_url_str)
        if clean_url.lower() in seen_urls:
            continue

        company = str(item.get("company", "Unknown")).strip()
        title = str(item.get("title", "Role")).strip()

        # Suppress if user has applied or dismissed this company
        if company.lower() in applied_comps or company.lower() in dismissed_comps:
            continue

        # Suppress senior or experienced roles when searching for entry-level / fresher
        is_fresher_req = any(term in experience.lower() for term in ("entry", "fresher", "junior", "intern", "0-2", "new grad"))
        sanitized_title = _MTS_MASK_PATTERN.sub("mts_role", title)
        if is_fresher_req and EXCLUDE_TITLE_PATTERN.search(sanitized_title):
            continue

        snippet = item.get("snippet", "")
        if is_fresher_req and (requires_experienced_candidate(snippet) or requires_experienced_candidate(title)):
            continue

        combo_key = (company.lower(), title.lower())
        if combo_key in seen_combos:
            continue

        seen_urls.add(clean_url.lower())
        seen_combos.add(combo_key)

        # Generate a deterministic unique ID
        url_hash = hashlib.sha256(clean_url.encode("utf-8")).hexdigest()[:12]
        jid = f"net_{url_hash}"

        job = JobPosting(
            id=jid,
            company=company,
            title=title,
            location=clean_location,
            apply_url=clean_url,
            published_date="Just Found",
            provider=ATSProvider.INTERNET_SEARCH,
            is_remote=("remote" in clean_location.lower() or "remote" in title.lower()),
            status="NEW",
            description=item.get("snippet", ""),
        )

        # Score personal tech-stack relevance
        score_job_posting(job)
        normalized_jobs.append(job)

    # Sort by relevance score descending
    normalized_jobs.sort(key=lambda j: j.relevance_score or 0, reverse=True)
    final_jobs = normalized_jobs[:limit]

    # Save newly discovered jobs to local gcc_jobs.db
    if record_to_db and final_jobs:
        try:
            init_db(db_path)
            record_jobs(final_jobs, db_path)
            # Re-fetch row IDs to populate numeric_id
            _attach_numeric_ids(final_jobs, db_path)
        except Exception as exc:
            logger.warning("Error saving searched jobs to database: %s", exc)

    return final_jobs


def _attach_numeric_ids(jobs: list[JobPosting], db_path: Optional[Path] = None) -> None:
    """Attach the assigned integer numeric_id from seen_jobs to each job in memory."""
    target_path = get_db_path(db_path)
    if not target_path.exists() or not jobs:
        return

    import sqlite3
    try:
        with sqlite3.connect(target_path) as conn:
            cursor = conn.cursor()
            for j in jobs:
                clean_url = canonicalize_url(str(j.apply_url)).lower()
                cursor.execute(
                    "SELECT rowid FROM seen_jobs WHERE lower(apply_url) = ? OR id = ? LIMIT 1",
                    (clean_url, j.id),
                )
                row = cursor.fetchone()
                if row:
                    j.numeric_id = row[0]
    except Exception as exc:
        logger.debug("Could not attach numeric IDs: %s", exc)


def format_internet_search_html(
    query: str,
    location: str,
    jobs: list[JobPosting],
) -> str:
    """Format the internet search results as clean, ranked Telegram HTML cards."""
    import html as pyhtml

    if not jobs:
        return (
            f"🔍 <b>Internet Job Search</b>\n\n"
            f"No active postings found on the internet for <b>{pyhtml.escape(query)}</b> in <i>{pyhtml.escape(location)}</i>.\n"
            f"💡 <i>Tip: Try broader keywords like 'Java Backend', 'SDE 1', 'Frontend React', or 'Remote'.</i>"
        )

    strong_count = sum(1 for j in jobs if (j.relevance_score or 0) >= 30)
    lines: list[str] = [
        f"🌐 <b>Live Internet Search: {pyhtml.escape(query)}</b> ({pyhtml.escape(location)})\n"
        f"Found <b>{len(jobs)}</b> openings • <b>{strong_count}</b> strong stack fits:\n"
    ]

    for idx, j in enumerate(jobs, start=1):
        num_str = f"#{j.numeric_id}" if j.numeric_id else f"[{idx}]"
        star_prefix = "⭐ " if (j.relevance_score or 0) >= 40 else "⚡ " if (j.relevance_score or 0) >= 20 else "📋 "
        score_badge = f"[{j.relevance_score} pts]" if j.relevance_score is not None else ""

        lines.append(f"{star_prefix}<b>{num_str} {pyhtml.escape(j.company)}</b> — <b>{pyhtml.escape(j.title)}</b> {score_badge}")
        lines.append(f"📍 <i>{pyhtml.escape(j.location)}</i>")

        if j.why:
            lines.append(f"💡 <i>Why: {pyhtml.escape(j.why)}</i>")
        elif j.description:
            clean_desc = pyhtml.escape(j.description[:120]).replace("\n", " ")
            lines.append(f"📝 <i>{clean_desc}...</i>")

        lines.append(f"🔗 <a href=\"{j.apply_url}\">Open Application Page</a>")
        lines.append("")

    lines.append("───────────────────────")
    lines.append("💡 <i>Use <code>/tailor &lt;id&gt;</code> to compile an ATS resume or <code>/apply &lt;id&gt;</code> to log application.</i>")

    return "\n".join(lines)
