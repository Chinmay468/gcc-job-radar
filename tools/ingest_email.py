"""Automated Email Job Alert Ingestion prototype for gcc-job-radar.

Reads user-configured job alert emails (LinkedIn, Naukri, Indeed) delivered to an inbox
via IMAP SSL, extracts job cards (Title, Company, Location, Canonical Apply URL),
applies strict entry-level & India location filters, and records qualified jobs
into gcc_jobs.db with numeric IDs for application tracking.
"""

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import email
from email import policy
from email.message import EmailMessage
import hashlib
import html
import imaplib
import json
import logging
import os
from pathlib import Path
import re
import sys
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from dotenv import load_dotenv

# Automatically load environment variables from .env in project root
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=ENV_PATH)

# Ensure project root is in sys.path when invoked directly
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from gcc_job_radar.db import (
    filter_unseen_email_uids,
    get_applied_and_dismissed_companies,
    init_db,
    is_company_excluded,
    record_jobs,
    record_seen_email_uids,
)
from gcc_job_radar.display import console, render_results
from gcc_job_radar.filters import (
    is_entry_level,
    is_potential_india_location,
    extract_application_dates,
    is_date_expired,
    is_remote_opening,
    requires_experienced_candidate,
)
from gcc_job_radar.link_resolver import (
    build_direct_careers_search_url,
    build_direct_search_url,
    is_direct_ats_url,
    is_glassdoor_url,
    is_job_legitimate,
    resolve_company_career_portal,
    unwrap_destination_url,
)
from gcc_job_radar.models import ATSProvider, JobPosting

logger = logging.getLogger(__name__)

# Default IMAP configuration
DEFAULT_IMAP_SERVER = os.getenv("EMAIL_IMAP_SERVER", "imap.gmail.com")
DEFAULT_IMAP_PORT = 993
DEFAULT_IMAP_FOLDER = os.getenv("EMAIL_FOLDER", "INBOX")

# Known job alert senders
KNOWN_ALERT_SENDERS = [
    "jobalerts-noreply@linkedin.com",
    "alerts@naukri.com",
    "alert@naukri.com",
    "jobalerts@naukri.com",
    "alert@indeed.com",
    "alerts@indeed.com",
    "noreply@glassdoor.com",
    "jobalerts@glassdoor.com",
    "alerts@glassdoor.com",
    "donotreply@acciojob.com",
    "alerts@acciojob.com",
    "jobs@acciojob.com",
    "team@acciojob.com",
    "updates@acciojob.com",
    "no-reply@acciojob.com",
]

ALERT_IMAP_FILTER = '(OR (FROM "jobalerts-noreply@linkedin.com") (OR (FROM "naukri.com") (OR (FROM "indeed.com") (OR (FROM "glassdoor.com") (FROM "acciojob.com")))))'

TRACKING_QUERY_PARAMS = {
    "trk",
    "trackingid",
    "refid",
    "midtoken",
    "midsig",
    "utmsource",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "alertaction",
    "from",
    "ref",
}

IMAP_MONTHS = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


def format_imap_date(dt: datetime) -> str:
    """Format a datetime into IMAP-compliant dd-Mmm-yyyy date string regardless of system locale."""
    day = dt.strftime("%d")
    month = IMAP_MONTHS[dt.month - 1]
    year = dt.strftime("%Y")
    return f"{day}-{month}-{year}"


def clean_text_punctuation(text: str) -> str:
    """Normalize non-standard Unicode punctuation and whitespace.

    Converts non-breaking hyphens (\\u2011), en-dashes, em-dashes to standard ASCII '-',
    non-breaking spaces to standard ' ', and unescapes HTML entities.
    """
    if not text:
        return ""
    t = html.unescape(text)
    t = re.sub(r"[\u2010\u2011\u2012\u2013\u2014\u2015]", "-", t)
    t = re.sub(r"[\u00a0\u200b\u200c\u200d]", " ", t)
    t = re.sub(r"[\u2018\u2019]", "'", t)
    t = re.sub(r"[\u201c\u201d]", '"', t)
    return " ".join(t.split())


def extract_text_lines(html_str: str) -> list[str]:
    """Convert HTML snippet to clean text lines, preserving block breaks while stripping inline markup."""
    if not html_str:
        return []
    # Replace block-level tags and linebreaks with \n
    s = re.sub(r"(?i)<(?:br|/br|div|/div|p|/p|tr|/tr|td|/td|li|/li|h[1-6]|/h[1-6])[^>]*>", "\n", html_str)
    # Replace remaining inline tags with space
    s = re.sub(r"<[^>]+>", " ", s)
    lines = [clean_text_punctuation(line) for line in s.splitlines()]
    return [line for line in lines if line and line not in ("·", "-", "•", "|", "–", "—")]


@dataclass
class RawParsedJob:
    """Represents a job card extracted from an email message before filtering."""

    title: str
    company: str
    location: str
    url: str
    source_platform: str = "email_alert"
    date_str: Optional[str] = "Recent"
    snippet: Optional[str] = None
    direct_search_url: Optional[str] = None


class MissingEmailCredentialsError(Exception):
    """Raised when required IMAP credentials are not found in the environment."""

    pass


def check_credentials(
    user: Optional[str] = None,
    password: Optional[str] = None,
) -> tuple[str, str]:
    """Validate and return IMAP credentials from arguments or environment."""
    email_user = user or os.getenv("EMAIL_USER")
    email_pass = password or os.getenv("EMAIL_PASSWORD")

    if not email_user or not email_pass:
        msg = (
            "[bold red]Email Alert Ingestion: Missing Credentials[/bold red]\n\n"
            "To read job alert emails directly from your inbox, set your credentials in `.env`:\n\n"
            "  [bold cyan]EMAIL_IMAP_SERVER[/bold cyan]=imap.gmail.com\n"
            "  [bold cyan]EMAIL_USER[/bold cyan]=your_email@gmail.com\n"
            "  [bold cyan]EMAIL_PASSWORD[/bold cyan]=xxxx xxxx xxxx xxxx\n\n"
            "[dim]How to create a Google App Password:\n"
            "1. Visit your Google Account (myaccount.google.com) -> Security.\n"
            "2. Under 'How you sign in to Google', ensure 2-Step Verification is turned ON.\n"
            "3. Search for 'App Passwords' and create a new password named 'GCC Job Radar'.\n"
            "4. Copy the generated 16-character code into your .env as EMAIL_PASSWORD.[/dim]"
        )
        raise MissingEmailCredentialsError(msg)

    return email_user.strip(), email_pass.strip()


def get_configured_email_accounts(
    user: Optional[str] = None,
    password: Optional[str] = None,
    env_path: Optional[Path] = None,
) -> list[tuple[str, str]]:
    """Discover all configured email accounts from CLI arguments, environment, or .env file."""
    # 1. Explicit arguments take absolute precedence
    if user and password:
        return [(user.strip(), password.strip())]

    # If caller provided only one of user/password, validate via check_credentials
    if (user and not password) or (password and not user):
        u, p = check_credentials(user, password)
        return [(u, p)]

    # If environment explicitly lacks both EMAIL_USER and EMAIL_PASSWORD
    # (e.g. In unit tests using monkeypatch.delenv), fail fast with standard error.
    if os.getenv("EMAIL_USER") is None and os.getenv("EMAIL_PASSWORD") is None and not user and not password:
        check_credentials(user, password)

    accounts: list[tuple[str, str]] = []
    target_env = env_path or ENV_PATH

    # 2. Parse .env file line by line to collect all sequential or indexed EMAIL_USER / EMAIL_PASSWORD blocks
    if target_env.exists():
        current_user: Optional[str] = None
        current_pass: Optional[str] = None
        indexed_users: dict[str, str] = {}
        indexed_passes: dict[str, str] = {}

        try:
            for line in target_env.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                m_user = re.match(r"^EMAIL_USER(?:_?(\d+))?\s*=\s*(.*)$", line)
                if m_user:
                    idx, val = m_user.group(1), m_user.group(2).strip("\"' ")
                    if idx:
                        indexed_users[idx] = val
                    else:
                        if current_user and current_pass:
                            accounts.append((current_user, current_pass))
                            current_user = None
                            current_pass = None
                        current_user = val
                    continue
                m_pass = re.match(r"^EMAIL_PASSWORD(?:_?(\d+))?\s*=\s*(.*)$", line)
                if m_pass:
                    idx, val = m_pass.group(1), m_pass.group(2).strip("\"' ")
                    if idx:
                        indexed_passes[idx] = val
                    else:
                        current_pass = val
                        if current_user and current_pass:
                            accounts.append((current_user, current_pass))
                            current_user = None
                            current_pass = None
                    continue

            if current_user and current_pass:
                accounts.append((current_user, current_pass))

            for idx in sorted(indexed_users.keys(), key=lambda x: int(x)):
                if idx in indexed_passes:
                    accounts.append((indexed_users[idx], indexed_passes[idx]))
        except Exception as exc:
            logger.debug("Failed reading .env for multiple accounts: %s", exc)

    # 3. Discover accounts from environment variables (EMAIL_USER, EMAIL_USER_2, etc.) if .env had none
    if not accounts:
        env_user = os.getenv("EMAIL_USER")
        env_pass = os.getenv("EMAIL_PASSWORD")
        if env_user and env_pass:
            accounts.append((env_user.strip(), env_pass.strip()))

        for key, val in os.environ.items():
            m_user = re.match(r"^EMAIL_USER(?:_?(\d+))$", key, re.IGNORECASE)
            if m_user:
                suffix = m_user.group(1)
                pass_key1 = f"EMAIL_PASSWORD_{suffix}"
                pass_key2 = f"EMAIL_PASSWORD{suffix}"
                pass_val = os.getenv(pass_key1) or os.getenv(pass_key2)
                if val and pass_val:
                    accounts.append((val.strip(), pass_val.strip()))

    # Deduplicate by username case-insensitively while preserving insertion order
    seen: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for u, p in accounts:
        clean_u = u.strip()
        clean_p = p.strip()
        if clean_u.lower() not in seen and clean_u and clean_p:
            seen.add(clean_u.lower())
            deduped.append((clean_u, clean_p))

    if not deduped:
        check_credentials(user, password)

    return deduped



def strip_tracking_params(url: str) -> str:
    """Remove tracking queries (trk, refId, utm_*, etc.) from job URLs, unwrapping nested direct ATS links."""
    try:
        # Check if URL contains nested direct ATS link
        unwrapped = unwrap_destination_url(url)
        if unwrapped and is_direct_ats_url(unwrapped):
            return unwrapped

        parsed = urlparse(url)

        # Canonicalize LinkedIn job URLs: https://www.linkedin.com/jobs/view/<id>/
        linkedin_match = re.search(r"linkedin\.com/(?:comm/)?jobs/view/([0-9]+)", url)
        if linkedin_match:
            job_id = linkedin_match.group(1)
            return f"https://www.linkedin.com/jobs/view/{job_id}/"

        # Canonicalize Indeed job URLs: https://www.indeed.com/viewjob?jk=<jk>
        if "indeed." in parsed.netloc:
            query_dict = parse_qs(parsed.query, keep_blank_values=False)
            jk_val = query_dict.get("jk", [None])[0]
            if jk_val:
                return f"https://www.indeed.com/viewjob?jk={jk_val}"

        # Canonicalize Naukri job URLs: strip query string completely
        if "naukri.com" in parsed.netloc:
            return urlunparse(parsed._replace(query="", fragment=""))

        # Canonicalize Glassdoor job URLs: https://www.glassdoor.com/job-listing/?jl=<id>
        if "glassdoor." in parsed.netloc:
            unescaped_u = html.unescape(url)
            jl_match = re.search(r"(?:[?&]|[-_])(?:jobListingId|jl)=(\d+)", unescaped_u)
            if jl_match:
                return f"https://www.glassdoor.com/job-listing/?jl={jl_match.group(1)}"
            return urlunparse(parsed._replace(query="", fragment=""))

        if not parsed.query:
            return url

        query_dict = parse_qs(parsed.query, keep_blank_values=False)
        clean_dict = {
            k: v for k, v in query_dict.items() if k.lower() not in TRACKING_QUERY_PARAMS
        }
        clean_query = urlencode(clean_dict, doseq=True)
        return urlunparse(parsed._replace(query=clean_query))
    except Exception:
        return url


def parse_linkedin_alert_html(html_content: str) -> list[RawParsedJob]:
    """Extract job cards from LinkedIn Job Alert emails."""
    jobs: list[RawParsedJob] = []
    seen_ids: set[str] = set()

    # Pattern: <a ... href="...linkedin.com/(comm/)?jobs/view/(?P<id>[0-9]+)...">(?P<title>.*?)</a>
    card_pattern = re.compile(
        r"""<a[^>]+href=["'](?P<url>https?://[^"']*linkedin\.com/(?:comm/)?jobs/view/(?P<id>\d+)[^"']*)["'][^>]*>(?P<title>.*?)</a>""",
        re.IGNORECASE | re.DOTALL,
    )

    for m in card_pattern.finditer(html_content):
        job_id = m.group("id")
        if job_id in seen_ids:
            continue

        raw_title = m.group("title")
        clean_title = clean_text_punctuation(re.sub(r"<[^>]+>", "", raw_title))

        # Skip non-title anchor links (e.g. logos, company cards, icons)
        if not clean_title or len(clean_title) < 2 or any(term in clean_title.lower() for term in ("view job", "apply", "see all", "unsubscribe", "help")):
            continue

        clean_url = f"https://www.linkedin.com/jobs/view/{job_id}/"
        seen_ids.add(job_id)

        # Inspect next 800 characters following the title link for Company & Location
        start_pos = m.end()
        vicinity = html_content[start_pos : start_pos + 800]
        lines = extract_text_lines(vicinity)

        company = "LinkedIn Employer"
        location = "India"

        for line in lines[:4]:
            if "·" in line or "&middot;" in line:
                parts = [clean_text_punctuation(p) for p in re.split(r"·|&middot;", line) if clean_text_punctuation(p)]
                if len(parts) >= 2:
                    company = parts[0]
                    location = parts[1]
                    break
            elif any(c in line.lower() for c in ("india", "bengaluru", "bangalore", "hyderabad", "pune", "delhi", "gurgaon", "noida", "mumbai", "chennai", "remote")):
                location = line
            elif company == "LinkedIn Employer" and len(line) < 50 and not any(term in line.lower() for term in ("view job", "apply", "easy apply", "see more")):
                company = line

        snippet = " | ".join(lines[1:8]) if len(lines) > 1 else ""

        jobs.append(
            RawParsedJob(
                title=clean_title,
                company=company,
                location=location,
                url=clean_url,
                source_platform="linkedin",
                snippet=snippet,
            )
        )

    return jobs


def parse_naukri_alert_html(html_content: str) -> list[RawParsedJob]:
    """Extract job cards from Naukri Job Alert emails."""
    jobs: list[RawParsedJob] = []
    seen_urls: set[str] = set()

    # Naukri job link pattern: href="...naukri.com/job-listings-...<id>..."
    card_pattern = re.compile(
        r"""<a[^>]+href=["'](?P<url>https?://[^"']*naukri\.com/job-listings-[^"']+)["'][^>]*>(?P<title>.*?)</a>""",
        re.IGNORECASE | re.DOTALL,
    )

    for m in card_pattern.finditer(html_content):
        raw_title = m.group("title")
        clean_title = clean_text_punctuation(re.sub(r"<[^>]+>", "", raw_title))

        if not clean_title or len(clean_title) < 2 or any(term in clean_title.lower() for term in ("apply", "view", "unsubscribe")):
            continue

        raw_url = html.unescape(m.group("url"))
        clean_url = strip_tracking_params(raw_url)
        if clean_url in seen_urls:
            continue
        seen_urls.add(clean_url)

        # Look in vicinity for company and location
        start_pos = m.end()
        vicinity = html_content[start_pos : start_pos + 600]
        lines = extract_text_lines(vicinity)

        company = "Naukri Employer"
        location = "India"

        for line in lines[:3]:
            if company == "Naukri Employer" and len(line) < 60 and not any(w in line.lower() for w in ("yrs", "lpa", "apply", "keyskills", "posted")):
                company = line
            elif any(c in line.lower() for c in ("india", "bengaluru", "bangalore", "hyderabad", "pune", "delhi", "gurgaon", "noida", "mumbai", "chennai")):
                location = line

        snippet = " | ".join(lines[:8])

        jobs.append(
            RawParsedJob(
                title=clean_title,
                company=company,
                location=location,
                url=clean_url,
                source_platform="naukri",
                snippet=snippet,
            )
        )

    return jobs


def parse_indeed_alert_html(html_content: str) -> list[RawParsedJob]:
    """Extract job cards from Indeed Job Alert emails."""
    jobs: list[RawParsedJob] = []
    seen_urls: set[str] = set()

    # Indeed job link pattern: /rc/clk?jk=... or /viewjob?jk=...
    card_pattern = re.compile(
        r"""<a[^>]+href=["'](?P<url>https?://[^"']*indeed\.[^"']+(?:/rc/clk\?jk=|/viewjob\?jk=)(?P<jk>[a-zA-Z0-9]+)[^"']*)["'][^>]*>(?P<title>.*?)</a>""",
        re.IGNORECASE | re.DOTALL,
    )

    for m in card_pattern.finditer(html_content):
        raw_title = m.group("title")
        clean_title = clean_text_punctuation(re.sub(r"<[^>]+>", "", raw_title))
        jk = m.group("jk")

        if not clean_title or len(clean_title) < 2 or any(term in clean_title.lower() for term in ("apply", "view", "unsubscribe")):
            continue

        clean_url = f"https://www.indeed.com/viewjob?jk={jk}"
        if clean_url in seen_urls:
            continue
        seen_urls.add(clean_url)

        start_pos = m.end()
        vicinity = html_content[start_pos : start_pos + 600]
        lines = extract_text_lines(vicinity)

        company = "Indeed Employer"
        location = "India"

        for line in lines[:3]:
            if company == "Indeed Employer" and len(line) < 50 and not any(w in line.lower() for w in ("apply", "rating", "reviews")):
                company = line
            elif any(c in line.lower() for c in ("india", "bengaluru", "bangalore", "hyderabad", "pune", "delhi", "remote", "gurgaon", "noida", "mumbai", "chennai")):
                location = line

        snippet = " | ".join(lines[1:8]) if len(lines) > 1 else ""

        jobs.append(
            RawParsedJob(
                title=clean_title,
                company=company,
                location=location,
                url=clean_url,
                source_platform="indeed",
                snippet=snippet,
            )
        )

    return jobs


def strip_glassdoor_rating(company_str: str) -> str:
    """Remove star ratings and review metadata appended to company names in Glassdoor alerts."""
    cleaned = re.sub(r"\s+\d(?:\.\d)?\s*[\u2605\u2606\?]?.*$", "", company_str).strip()
    return clean_text_punctuation(cleaned)


def parse_glassdoor_alert_html(html_content: str) -> list[RawParsedJob]:
    """Extract job cards from Glassdoor Job Alert emails."""
    jobs: list[RawParsedJob] = []
    seen_ids: set[str] = set()

    card_pattern = re.compile(
        r"""<a[^>]+href=["'](?P<url>[^"']*glassdoor\.[^"']*(?:partner/jobListing\.htm|job-listing)[^"']*)["'][^>]*>(?P<inner>.*?)</a>""",
        re.DOTALL | re.IGNORECASE,
    )

    for m in card_pattern.finditer(html_content):
        raw_url = html.unescape(m.group("url"))
        jl_match = re.search(r"(?:[?&]|[-_])(?:jobListingId|jl)=(\d+)", raw_url)
        jl_id = jl_match.group(1) if jl_match else None

        if jl_id and jl_id in seen_ids:
            continue
        if jl_id:
            seen_ids.add(jl_id)
            clean_url = f"https://www.glassdoor.com/job-listing/?jl={jl_id}"
        else:
            clean_url = strip_tracking_params(raw_url)
            if clean_url in seen_ids:
                continue
            seen_ids.add(clean_url)

        inner_html = m.group("inner")
        lines = extract_text_lines(inner_html)
        if not lines:
            continue

        company = strip_glassdoor_rating(lines[0])
        title = lines[1] if len(lines) > 1 else ""
        location = lines[2] if len(lines) > 2 else "India"
        snippet = " | ".join(lines[3:10]) if len(lines) > 3 else None

        if (
            not title
            or len(title) < 2
            or any(term in title.lower() for term in ("view job", "apply", "glassdoor", "see all", "unsubscribe"))
        ):
            continue

        jobs.append(
            RawParsedJob(
                title=title,
                company=company or "Glassdoor Employer",
                location=location,
                url=clean_url,
                source_platform="glassdoor",
                snippet=snippet,
            )
        )

    return jobs


def parse_schema_org_json_ld(html_content: str) -> list[RawParsedJob]:
    """Extract JobPosting entities defined in Schema.org JSON-LD scripts if present."""
    jobs: list[RawParsedJob] = []
    scripts = re.findall(
        r"""<script[^>]+type=["']application/ld\+json["'][^>]*>(.*?)</script>""",
        html_content,
        re.DOTALL | re.IGNORECASE,
    )
    for script_text in scripts:
        try:
            data = json.loads(script_text.strip())
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict) and item.get("@type") == "JobPosting":
                    title = clean_text_punctuation(item.get("title") or "")
                    url = item.get("url") or ""
                    company = ""
                    org = item.get("hiringOrganization")
                    if isinstance(org, dict):
                        company = clean_text_punctuation(org.get("name") or "")
                    elif isinstance(org, str):
                        company = clean_text_punctuation(org)

                    location = "India"
                    job_loc = item.get("jobLocation")
                    if isinstance(job_loc, dict):
                        addr = job_loc.get("address")
                        if isinstance(addr, dict):
                            location = clean_text_punctuation(addr.get("addressLocality") or addr.get("addressCountry") or "India")
                        elif isinstance(addr, str):
                            location = clean_text_punctuation(addr)

                    if title and url:
                        jobs.append(
                            RawParsedJob(
                                title=title,
                                company=company or "Employer",
                                location=location,
                                url=strip_tracking_params(url),
                                source_platform="schema_jsonld",
                            )
                        )
        except Exception:
            continue
    return jobs


def parse_acciojob_alert_html(
    html_content: str,
    sender: str = "",
    subject: str = "",
) -> list[RawParsedJob]:
    """Extract job cards, assessment invitations, and curated partner drives from AccioJob emails."""
    jobs: list[RawParsedJob] = []
    seen_keys: set[tuple[str, str]] = set()

    subj_clean = clean_text_punctuation(subject)
    sender_l = (sender or "").lower()
    subj_l = (subj_clean or "").lower()

    is_accio = any(k in sender_l for k in ("acciojob", "acciomatrix")) or any(k in subj_l for k in ("acciojob", "acciomatrix"))
    is_assessment = "assessment" in subj_l or "assessment link" in html_content[:2000].lower()

    # Fast guard: AccioJob parser should only execute on AccioJob emails or explicit assessment links
    if not (is_accio or is_assessment):
        return []

    # 1. Skip non-job messages (OTP codes, password resets, verification)
    if re.search(r"\b(otp|verification\s*code|reset\s*password|login\s*code)\b", subj_clean, re.I):
        return []

    # 2. Case A: Assessment Invitation Email
    # e.g. Subject: "Assessment Link for GoComet | Full Stack Developer Intern | Slot 1st"
    # Or: "GoComet | Full Stack Developer Intern | Slot 1 Assessment Link"
    m_assess = None
    if is_assessment or "slot" in subj_l or is_accio:
        m_assess = re.search(
            r"(?:Assessment\s+Link\s+for\s+)?(?P<comp>[^|]+?)\s*\|\s*(?P<role>[^|]+?)(?:\s*\|\s*(?P<extra>.*))?$",
            subj_clean,
            re.I,
        )
        if not m_assess:
            # Check inside HTML <title>
            m_title = re.search(
                r"<title>(?:Assessment\s+Link\s+for\s+)?(?P<comp>[^|]+?)\s*\|\s*(?P<role>[^|<]+?)(?:\s*\|\s*[^<]*)?</title>",
                html_content[:3000],
                re.I,
            )
            if m_title and (is_accio or "assessment" in m_title.group(0).lower()):
                m_assess = m_title

    if m_assess:
        comp_candidate = clean_text_punctuation(m_assess.group("comp"))
        role_candidate = clean_text_punctuation(m_assess.group("role"))

        # Clean off trailing words like "Assessment Link" from role or company
        role_candidate = re.sub(r"(?i)\s+assessment\s+link.*$", "", role_candidate).strip()
        comp_candidate = re.sub(r"(?i)^assessment\s+link\s+for\s+", "", comp_candidate).strip()

        if (
            comp_candidate
            and role_candidate
            and not re.search(r"\b(otp|matrix|login|acciojob)\b", comp_candidate, re.I)
        ):
            # Find assessment link in body
            apply_url = ""
            m_link = re.search(
                r"""<a[^>]+href=["'](?P<url>[^"']*(?:azurecomm\.net|acciomatrix\.com|acciojob\.com|interviewbit|hackerearth|unstop)[^"']*)["'][^>]*>(?P<text>.*?)</a>""",
                html_content,
                re.I | re.DOTALL,
            )
            if m_link:
                raw_url = html.unescape(m_link.group("url"))
                unwrapped = unwrap_destination_url(raw_url)
                apply_url = unwrapped or strip_tracking_params(raw_url)
            else:
                for m_a in re.finditer(r"""<a[^>]+href=["'](?P<url>https?://[^"']+)["'][^>]*>""", html_content, re.I):
                    candidate_u = html.unescape(m_a.group("url"))
                    if "contact" not in candidate_u and "support" not in candidate_u:
                        unwrapped = unwrap_destination_url(candidate_u)
                        apply_url = unwrapped or strip_tracking_params(candidate_u)
                        break

            if not apply_url:
                apply_url = build_direct_search_url(comp_candidate, role_candidate)

            key = (comp_candidate.lower(), role_candidate.lower())
            if key not in seen_keys:
                seen_keys.add(key)
                jobs.append(
                    RawParsedJob(
                        title=role_candidate,
                        company=comp_candidate,
                        location="India",
                        url=apply_url,
                        source_platform="acciojob",
                        snippet=f"Assessment Link: {comp_candidate} | {role_candidate}",
                        direct_search_url=build_direct_search_url(comp_candidate, role_candidate),
                    )
                )

    # 3. Case B: Curated Partner Listings / Digest Tables
    # Only run table parsing if the email is actually an AccioJob partner digest email
    if not is_accio and "acciojob" not in html_content[:3000].lower():
        return jobs
    # Pattern 1: TSV / Text table (Company\tRole\t\nSkills\nDate\tSource)
    table_pattern = re.compile(
        r"([^\t\n\r]+)\t([^\t\n\r]+)\t\s*\n([\s\S]*?)\n([0-9]{1,2}\s+[A-Za-z]+\s+[0-9]{4})\t([^\t\n\r]+)\t?",
        re.MULTILINE,
    )
    for m in table_pattern.finditer(html_content):
        comp, role, skills, date_str, src = m.groups()
        comp_clean = clean_text_punctuation(comp)
        role_clean = clean_text_punctuation(role)
        skills_clean = clean_text_punctuation(skills).replace("\n", ", ")
        src_clean = clean_text_punctuation(src)

        # Skip promotional ads (e.g. Fast Track Placement Program)
        if any(w in comp_clean.lower() for w in ("fast track", "placement program", "scholarship", "acciojob")):
            continue

        key = (comp_clean.lower(), role_clean.lower())
        if key in seen_keys:
            continue
        seen_keys.add(key)

        apply_url = build_direct_careers_search_url(comp_clean, role_clean)
        jobs.append(
            RawParsedJob(
                title=role_clean,
                company=comp_clean,
                location="India",
                url=apply_url,
                source_platform="acciojob",
                date_str=date_str.strip(),
                snippet=f"Skills: {skills_clean} | Source: {src_clean}",
                direct_search_url=build_direct_search_url(comp_clean, role_clean),
            )
        )

    # Pattern 2: HTML Table Rows: <tr>...<td>Company</td><td>Role</td>...</tr>
    row_pattern = re.compile(r"<tr[^>]*>([\s\S]*?)</tr>", re.I)
    for row_m in row_pattern.finditer(html_content):
        row_html = row_m.group(1)
        tds = re.findall(r"<td[^>]*>([\s\S]*?)</td>", row_html, re.I)
        if len(tds) >= 2:
            lines0 = extract_text_lines(tds[0])
            lines1 = extract_text_lines(tds[1])
            comp_name = lines0[0] if lines0 else ""
            role_title = lines1[0] if lines1 else ""

            if not comp_name or not role_title or len(comp_name) < 2 or len(role_title) < 2:
                continue
            if any(w in comp_name.lower() for w in ("company", "fast track", "placement", "acciojob")):
                continue

            row_url = ""
            m_a = re.search(r"""href=["'](?P<url>https?://[^"']+)["']""", row_html, re.I)
            if m_a:
                row_url = strip_tracking_params(html.unescape(m_a.group("url")))
            else:
                row_url = build_direct_careers_search_url(comp_name, role_title)

            skills_text = " | ".join(extract_text_lines(" ".join(tds[2:]))) if len(tds) > 2 else ""

            key = (comp_name.lower(), role_title.lower())
            if key in seen_keys:
                continue
            seen_keys.add(key)

            jobs.append(
                RawParsedJob(
                    title=role_title,
                    company=comp_name,
                    location="India",
                    url=row_url,
                    source_platform="acciojob",
                    snippet=skills_text,
                    direct_search_url=build_direct_search_url(comp_name, role_title),
                )
            )

    return jobs


def parse_email_alert_html(
    html_content: str,
    sender: str = "",
    subject: str = "",
) -> list[RawParsedJob]:
    """Route email HTML to appropriate parser based on sender/subject and combine findings."""
    sender_lower = (sender or "").lower()
    subject_lower = (subject or "").lower()
    jobs: list[RawParsedJob] = []

    # 1. Platform-specific parser based on sender
    is_known_platform = False
    if "linkedin" in sender_lower:
        is_known_platform = True
        jobs.extend(parse_linkedin_alert_html(html_content))
    elif "naukri" in sender_lower:
        is_known_platform = True
        jobs.extend(parse_naukri_alert_html(html_content))
    elif "indeed" in sender_lower:
        is_known_platform = True
        jobs.extend(parse_indeed_alert_html(html_content))
    elif "glassdoor" in sender_lower:
        is_known_platform = True
        jobs.extend(parse_glassdoor_alert_html(html_content))
    elif "acciojob" in sender_lower or "acciomatrix" in sender_lower or "acciojob" in subject_lower:
        is_known_platform = True
        jobs.extend(parse_acciojob_alert_html(html_content, sender=sender, subject=subject))

    # 2. If no jobs found, attempt schema.org json-ld (standard across all platforms)
    if not jobs:
        jobs.extend(parse_schema_org_json_ld(html_content))

    # 3. Fallback: only try other parsers if sender was NOT a recognized platform
    # (If sender is clearly Indeed or LinkedIn, running other parsers is wasteful and risks false positives/hangs)
    if not jobs and not is_known_platform:
        jobs.extend(parse_linkedin_alert_html(html_content))
        if not jobs:
            jobs.extend(parse_naukri_alert_html(html_content))
        if not jobs:
            jobs.extend(parse_indeed_alert_html(html_content))
        if not jobs:
            jobs.extend(parse_glassdoor_alert_html(html_content))
        if not jobs and ("accio" in sender_lower or "assessment" in subject_lower):
            jobs.extend(parse_acciojob_alert_html(html_content, sender=sender, subject=subject))

    return jobs


def filter_and_convert_jobs(
    raw_jobs: list[RawParsedJob],
    date_header: Optional[str] = None,
) -> list[JobPosting]:
    """Filter raw jobs against strict entry-level & India/Remote rules, returning valid JobPostings."""
    qualified: list[JobPosting] = []
    seen_urls: set[str] = set()
    seen_roles: set[tuple[str, str, str]] = set()

    for item in raw_jobs:
        clean_title = clean_text_punctuation(item.title)
        clean_company = clean_text_punctuation(item.company)
        clean_loc = clean_text_punctuation(item.location)
        clean_url = strip_tracking_params(item.url)

        # In-batch deduplication: avoid duplicate jobs extracted from multiple alert emails
        role_key = (clean_company.lower().strip(), clean_title.lower().strip(), clean_loc.lower().strip())
        url_key = clean_url.lower().strip().rstrip("/")
        if url_key in seen_urls or role_key in seen_roles:
            continue

        snippet = item.snippet or ""

        # Pre-check: reject if snippet explicitly signals an experienced hire requirement
        if snippet and requires_experienced_candidate(snippet):
            logger.debug(
                "Skipped '%s' @ %s — snippet requires experienced candidate: %.80s",
                clean_title,
                clean_company,
                snippet,
            )
            continue

        # Check job legitimacy (spam filtering, title & company sanity, entry-level criteria)
        is_legit, legit_reason = is_job_legitimate(clean_company, clean_title, clean_loc)
        if not is_legit and not is_entry_level(clean_title, content=snippet or None):
            logger.debug("Filtered out non-entry-level or invalid role '%s' at '%s': %s", clean_title, clean_company, legit_reason)
            continue

        # Check entry-level qualification; pass snippet as content for generic-title fallback
        is_acciojob = item.source_platform == "acciojob"
        eval_content = f"fresher entry-level 0-2 years {snippet}" if is_acciojob else (snippet or None)

        if not is_entry_level(clean_title, content=eval_content):
            logger.debug("Filtered out non-entry-level role: '%s'", clean_title)
            continue

        # Check location qualification (India location or Remote opening)
        is_india = is_potential_india_location(clean_loc)
        is_remote = is_remote_opening(
            {"location": clean_loc, "title": clean_title, "is_remote": False}
        )

        if not (is_india or is_remote):
            logger.debug("Filtered out non-India/non-remote role: '%s' in '%s'", clean_title, clean_loc)
            continue

        seen_urls.add(url_key)
        platform_id = None
        if item.source_platform == "linkedin":
            m_id = re.search(r"jobs/view/(\d+)", clean_url)
            if m_id:
                platform_id = m_id.group(1)
        elif item.source_platform == "glassdoor":
            m_id = re.search(r"jl=(\d+)", clean_url)
            if m_id:
                platform_id = m_id.group(1)
        elif item.source_platform == "indeed":
            m_id = re.search(r"jk=([a-zA-Z0-9]+)", clean_url)
            if m_id:
                platform_id = m_id.group(1)

        if not platform_id:
            platform_id = hashlib.sha256(clean_url.encode("utf-8")).hexdigest()[:16]

        job_id = f"email_{item.source_platform}_{platform_id}"

        # Detect Glassdoor aggregator URLs to bypass Cloudflare bot challenges
        is_glassdoor = is_glassdoor_url(clean_url) or item.source_platform == "glassdoor"

        # Unwrap any direct ATS destination link embedded in the URL
        direct_url = unwrap_destination_url(clean_url)
        if direct_url and is_direct_ats_url(direct_url):
            clean_url = direct_url
        elif is_glassdoor:
            portal = resolve_company_career_portal(clean_company)
            if portal:
                clean_url = portal

        direct_search = item.direct_search_url or (
            build_direct_careers_search_url(clean_company, clean_title)
            if is_glassdoor
            else build_direct_search_url(clean_company, clean_title)
        )

        pub_d = date_header or item.date_str or "Recent"
        app_start_d, app_end_d = extract_application_dates(item.snippet or "", pub_d)
        is_expired = bool(app_end_d and is_date_expired(app_end_d))

        try:
            posting = JobPosting(
                id=job_id,
                company=clean_company,
                title=clean_title,
                location=clean_loc,
                apply_url=clean_url,
                provider=ATSProvider.EMAIL_ALERT,
                published_date=pub_d,
                application_start_date=app_start_d,
                application_end_date=app_end_d,
                is_remote=is_remote,
                status="EXPIRED" if is_expired else "NEW",
                is_expired=is_expired,
                direct_search_url=direct_search,
            )
            qualified.append(posting)

            # Auto-register qualified new company into config.COMPANIES registry if not already tracked
            try:
                from gcc_job_radar.company_tracker import is_known_company, register_hiring_company
                if not is_known_company(clean_company):
                    register_hiring_company(
                        clean_company,
                        career_url=clean_url if "careers" in clean_url else None,
                        probe=False,
                    )
            except Exception as exc:
                logger.debug("Error auto-registering company %s: %s", clean_company, exc)

        except Exception as exc:
            logger.debug("Error creating JobPosting for %s: %s", clean_title, exc)

    return qualified


def extract_html_from_email_message(msg: EmailMessage) -> str:
    """Extract clean HTML content from an email.message.EmailMessage."""
    html_parts: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))
            if content_type == "text/html" and "attachment" not in content_disposition:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        html_parts.append(payload.decode(charset, errors="replace"))
                    except Exception:
                        html_parts.append(payload.decode("utf-8", errors="replace"))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                html_parts.append(payload.decode(charset, errors="replace"))
            except Exception:
                html_parts.append(payload.decode("utf-8", errors="replace"))

    return "\n".join(html_parts)


def fetch_unread_alert_emails(
    imap_client: Optional[imaplib.IMAP4_SSL] = None,
    server: str = DEFAULT_IMAP_SERVER,
    port: int = DEFAULT_IMAP_PORT,
    user: Optional[str] = None,
    password: Optional[str] = None,
    folder: str = DEFAULT_IMAP_FOLDER,
    limit: int = 10,
    days: int = 7,
    mark_read: bool = False,
    unread_only: bool = True,
    search_query: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> list[tuple[str, EmailMessage]]:
    """Fetch alert emails from IMAP SSL mailbox with date windowing and batch fetching."""
    owns_client = False
    account_key = user
    if imap_client is None:
        email_user, email_pass = check_credentials(user, password)
        account_key = email_user
        console.print(
            f"[*] Connecting to [bold cyan]{server}:{port}[/bold cyan] ({email_user}) via IMAP SSL..."
        )
        imap_client = imaplib.IMAP4_SSL(server, port=port, timeout=15.0)
        imap_client.login(email_user, email_pass)
        owns_client = True

    results: list[tuple[str, EmailMessage]] = []

    try:
        status, _ = imap_client.select(folder, readonly=not mark_read)
        if status != "OK":
            console.print(f"[bold red]Error:[/bold red] Could not select mailbox folder '{folder}'")
            return []

        # Server-Side Date Windowing: SINCE <dd-Mmm-yyyy>
        date_criteria = ""
        if days > 0:
            since_dt = datetime.now(timezone.utc) - timedelta(days=days)
            date_criteria = f"SINCE {format_imap_date(since_dt)}"

        # Target job alert senders specifically:
        if search_query:
            criteria = search_query
        elif unread_only:
            criteria = f"UNSEEN {date_criteria} {ALERT_IMAP_FILTER}".strip() if date_criteria else f"UNSEEN {ALERT_IMAP_FILTER}"
        else:
            criteria = f"{date_criteria} {ALERT_IMAP_FILTER}".strip() if date_criteria else ALERT_IMAP_FILTER

        search_status, data = imap_client.uid("SEARCH", None, criteria)

        # Fallback 1: If searching UNSEEN alerts returned 0, try searching general UNSEEN within window
        if (search_status != "OK" or not data or not data[0]) and unread_only and not search_query:
            fb1_criteria = f"UNSEEN {date_criteria}".strip() if date_criteria else "UNSEEN"
            search_status, data = imap_client.uid("SEARCH", None, fb1_criteria)

        # Fallback 2: If still 0, search recent alert emails across window
        if (search_status != "OK" or not data or not data[0]) and unread_only and not search_query:
            console.print("[dim]No unread alert emails found. Checking recent alert emails...[/dim]")
            fb2_criteria = f"{date_criteria} {ALERT_IMAP_FILTER}".strip() if date_criteria else ALERT_IMAP_FILTER
            search_status, data = imap_client.uid("SEARCH", None, fb2_criteria)

        if search_status != "OK" or not data or not data[0]:
            console.print(f"[dim]No matching job alert emails found in {account_key or 'mailbox'}.[/dim]")
            return []

        all_uids = [u.decode() if isinstance(u, bytes) else str(u) for u in data[0].split()]
        all_uids = [u.strip() for u in all_uids if u.strip()]

        if not all_uids:
            console.print(f"[dim]No matching job alert emails found in {account_key or 'mailbox'}.[/dim]")
            return []

        # Filter out already-seen UIDs from SQLite to avoid duplicate network fetching
        unseen_uids = filter_unseen_email_uids(all_uids, db_path=db_path, account=account_key)
        skipped_count = len(all_uids) - len(unseen_uids)
        if skipped_count > 0:
            console.print(f"[dim]Skipped {skipped_count} already-processed alert email(s) in {account_key or 'mailbox'}.[/dim]")

        if not unseen_uids:
            console.print(f"[dim]All matching emails in window have already been processed for {account_key or 'mailbox'}.[/dim]")
            return []

        console.print(f"[*] Found [bold green]{len(unseen_uids)}[/bold green] new alert email(s) in {folder} ({account_key or 'mailbox'}).")

        # Take newest emails up to limit
        target_uids = unseen_uids[-limit:] if limit > 0 else unseen_uids

        # Batch fetch in chunks of 25 using BODY.PEEK[] so emails aren't marked read automatically
        chunk_size = 25
        fetched_uids: list[str] = []

        for i in range(0, len(target_uids), chunk_size):
            chunk = target_uids[i : i + chunk_size]
            seq_set = ",".join(chunk)

            fetch_status, fetch_data = imap_client.uid("FETCH", seq_set, "(BODY.PEEK[])")
            if fetch_status != "OK" or not fetch_data:
                continue

            item_idx = 0
            for item in fetch_data:
                if not isinstance(item, tuple) or len(item) < 2:
                    continue
                meta_bytes, raw_email_bytes = item[0], item[1]
                if not raw_email_bytes or not isinstance(raw_email_bytes, (bytes, bytearray)):
                    continue

                meta_str = (
                    meta_bytes.decode("latin1", errors="ignore")
                    if isinstance(meta_bytes, (bytes, bytearray))
                    else str(meta_bytes)
                )
                uid_match = re.search(r"\bUID\s+(\d+)\b", meta_str, re.IGNORECASE)
                if uid_match:
                    msg_uid = uid_match.group(1)
                elif item_idx < len(chunk):
                    msg_uid = chunk[item_idx]
                else:
                    msg_uid = str(item_idx)
                item_idx += 1

                try:
                    msg = email.message_from_bytes(bytes(raw_email_bytes), policy=policy.default)
                    results.append((msg_uid, msg))
                    fetched_uids.append(msg_uid)
                except Exception as exc:
                    logger.debug("Failed to parse email UID %s: %s", msg_uid, exc)

            if mark_read and chunk:
                try:
                    imap_client.uid("STORE", seq_set, "+FLAGS", "(\\Seen)")
                except Exception as exc:
                    logger.debug("Failed to mark UIDs %s as read: %s", seq_set, exc)

        if fetched_uids:
            record_seen_email_uids(fetched_uids, db_path=db_path, account=account_key)

    finally:
        if owns_client and imap_client:
            try:
                imap_client.close()
            except Exception:
                pass
            try:
                imap_client.logout()
            except Exception:
                pass

    return results


def sync_email_alerts(
    server: str = DEFAULT_IMAP_SERVER,
    port: int = DEFAULT_IMAP_PORT,
    user: Optional[str] = None,
    password: Optional[str] = None,
    folder: str = DEFAULT_IMAP_FOLDER,
    limit: int = 10,
    days: int = 7,
    mark_read: bool = False,
    unread_only: bool = True,
    search_query: Optional[str] = None,
    db_path: Optional[Path] = None,
    imap_client: Optional[imaplib.IMAP4_SSL] = None,
    notify: bool = False,
) -> list[JobPosting]:
    """Execute complete email alert synchronization pipeline across all configured mailboxes."""
    console.print("[bold cyan]=== EMAIL JOB ALERT INGESTION (IMAP SSL) ===[/bold cyan]")

    init_db(db_path)

    all_raw_jobs: list[RawParsedJob] = []

    # 1. Fetch alert email messages
    if imap_client is not None:
        messages = fetch_unread_alert_emails(
            imap_client=imap_client,
            server=server,
            port=port,
            user=user,
            password=password,
            folder=folder,
            limit=limit,
            days=days,
            mark_read=mark_read,
            unread_only=unread_only,
            search_query=search_query,
            db_path=db_path,
        )
        if messages:
            console.print(f"[*] Processing {len(messages)} alert message(s)...")
            for msg_id, msg in messages:
                sender = clean_text_punctuation(str(msg.get("From", "")))
                subject = clean_text_punctuation(str(msg.get("Subject", "")))
                date_hdr = clean_text_punctuation(str(msg.get("Date", "")))
                html_content = extract_html_from_email_message(msg)
                if not html_content:
                    continue
                raw_jobs = parse_email_alert_html(html_content, sender=sender, subject=subject)
                for rj in raw_jobs:
                    if date_hdr:
                        rj.date_str = date_hdr
                all_raw_jobs.extend(raw_jobs)
    else:
        accounts = get_configured_email_accounts(user=user, password=password)
        account_names = [u for u, _ in accounts]
        console.print(
            f"[*] Monitoring [bold green]{len(accounts)}[/bold green] email account(s): {', '.join(account_names)}"
        )

        for acc_user, acc_pass in accounts:
            console.print(f"\n[bold cyan]== Checking mailbox: {acc_user} ==[/bold cyan]")
            try:
                messages = fetch_unread_alert_emails(
                    server=server,
                    port=port,
                    user=acc_user,
                    password=acc_pass,
                    folder=folder,
                    limit=limit,
                    days=days,
                    mark_read=mark_read,
                    unread_only=unread_only,
                    search_query=search_query,
                    db_path=db_path,
                )
            except Exception as exc:
                console.print(f"[bold red]Failed to fetch emails from {acc_user}:[/bold red] {exc}")
                continue

            if not messages:
                continue

            console.print(f"[*] Processing {len(messages)} alert message(s) from {acc_user}...")
            acc_raw = 0
            for msg_id, msg in messages:
                sender = clean_text_punctuation(str(msg.get("From", "")))
                subject = clean_text_punctuation(str(msg.get("Subject", "")))
                date_hdr = clean_text_punctuation(str(msg.get("Date", "")))
                html_content = extract_html_from_email_message(msg)
                if not html_content:
                    continue
                raw_jobs = parse_email_alert_html(html_content, sender=sender, subject=subject)
                for rj in raw_jobs:
                    if date_hdr:
                        rj.date_str = date_hdr
                all_raw_jobs.extend(raw_jobs)
                acc_raw += len(raw_jobs)

            console.print(f"[*] Extracted [bold green]{acc_raw}[/bold green] raw job card(s) from {acc_user}.")

    if not all_raw_jobs:
        console.print("[dim]No job cards extracted from any mailbox.[/dim]")
        return []

    console.print(f"\n[*] Total raw job card(s) extracted across all mailboxes: [bold green]{len(all_raw_jobs)}[/bold green].")

    # 2. Filter through entry-level & India/Remote rules
    qualified_postings = filter_and_convert_jobs(all_raw_jobs)
    console.print(
        f"[*] Qualified [bold green]{len(qualified_postings)}[/bold green] entry-level / fresher roles in India/Remote."
    )

    # 3. Store qualified postings into database
    if qualified_postings:
        record_jobs(qualified_postings, db_path=db_path)
        if notify:
            import asyncio
            from gcc_job_radar.notifier import dispatch_notifications
            try:
                asyncio.run(
                    dispatch_notifications(
                        new_jobs=qualified_postings,
                        db_path=db_path,
                        digest=True,
                    )
                )
            except Exception as exc:
                logger.warning("Failed to dispatch notifications for email jobs: %s", exc)

    return qualified_postings


def build_parser() -> argparse.ArgumentParser:
    """Build CLI parser for tools/ingest_email.py."""
    parser = argparse.ArgumentParser(
        description="Ingest job alert emails from inbox over IMAP SSL into gcc_jobs.db."
    )
    parser.add_argument(
        "--server",
        default=DEFAULT_IMAP_SERVER,
        help=f"IMAP server address (default: {DEFAULT_IMAP_SERVER}).",
    )
    parser.add_argument(
        "--user",
        default=None,
        help="Email address / IMAP username (or set EMAIL_USER in .env).",
    )
    parser.add_argument(
        "--password",
        default=None,
        help="Email password / Google App Password (or set EMAIL_PASSWORD in .env).",
    )
    parser.add_argument(
        "--folder",
        default=DEFAULT_IMAP_FOLDER,
        help=f"IMAP mailbox folder (default: {DEFAULT_IMAP_FOLDER}).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum emails to process (default: 10).",
    )
    parser.add_argument(
        "--days",
        "-d",
        type=int,
        default=7,
        help="Search emails received in the last N days (default: 7). Use 0 for all time.",
    )
    parser.add_argument(
        "--mark-read",
        action="store_true",
        default=False,
        help="Mark processed alert emails as READ in inbox (default: keep unread).",
    )
    parser.add_argument(
        "--all",
        action="store_false",
        dest="unread_only",
        default=True,
        help="Process recent alert emails even if already read (default: unread only).",
    )
    parser.add_argument(
        "--query",
        default=None,
        help="Custom IMAP search query (overrides default alert sender search).",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Custom path to SQLite database file.",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        default=False,
        help="Dispatch notification (Telegram/Discord digest) for qualified jobs.",
    )
    return parser


def main() -> None:
    """Main CLI entrypoint for tools/ingest_email.py."""
    parser = build_parser()
    args = parser.parse_args()

    try:
        jobs = sync_email_alerts(
            server=args.server,
            user=args.user,
            password=args.password,
            folder=args.folder,
            limit=args.limit,
            days=args.days,
            mark_read=args.mark_read,
            unread_only=args.unread_only,
            search_query=args.query,
            db_path=args.db,
            notify=args.notify,
        )
        render_results(jobs, is_new_only=False)
    except MissingEmailCredentialsError as e:
        console.print(str(e))
        sys.exit(1)
    except Exception as exc:
        console.print(f"[bold red]Ingestion failed:[/bold red] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
