import hashlib
import json
import logging
import re
from typing import Optional, Union
import urllib.parse
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import httpx
from pydantic import ValidationError

from gcc_job_radar.clients.base import BaseATSClient, DEFAULT_TIMEOUT
from gcc_job_radar.db import canonicalize_url
from gcc_job_radar.filters import is_entry_level, is_remote_opening, matches_india_location, matches_target_title
from gcc_job_radar.models import ATSProvider, CompanyConfig, JobPosting

logger = logging.getLogger(__name__)

# Standard browser User-Agent header
DEFAULT_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Strict positive engineering and technical job keywords
TECH_KEYWORDS_PATTERN = re.compile(
    r"\b(software|developer|engineer|sde|frontend|front-end|backend|back-end|"
    r"fullstack|full-stack|devops|data\s+(?:engineer|analyst|scientist)|"
    r"qa|test\s+engineer|sdet|intern|trainee|architect|machine\s+learning|ai|cloud)\b",
    re.IGNORECASE,
)

# Exclusion keywords for non-tech/management noise
NON_TECH_EXCLUDE_PATTERN = re.compile(
    r"\b(sales|marketing|hr|human\s+resources|recruiter|recruiting|talent\s+acquisition|"
    r"account\s+executive|customer\s+support|customer\s+success|customer\s+service|"
    r"telecaller|operations\s+executive|back\s+office|legal|compliance|nurse|nursing|"
    r"finance|accountant|driver|technician|cook|chef)\b",
    re.IGNORECASE,
)

# Generic career portal directories, sections, and landing page URLs that are NOT individual jobs
DISQUALIFIED_PATH_PATTERN = re.compile(
    r"""(?ix)
    /(?:
        early-in-career |
        young-professionals |
        entry-level |
        life-at[\w-]* |
        our-culture |
        culture |
        programs? |
        students? |
        university-recruiting |
        campus |
        locations? |
        about[\w-]* |
        benefits |
        working-at[\w-]* |
        overview |
        en/(?:india|us|uk|de|apac) |
        india
    )(?:/|\.html?)?$
    """,
    re.VERBOSE,
)

# Call-to-action or navigational link text that indicates a portal/category link, not a job role
DISQUALIFIED_TEXT_PATTERN = re.compile(
    r"""(?ix)
    \b(?:
        explore(?:\s+our)? |
        search\s+(?:internships|jobs|roles|openings|careers) |
        find\s+(?:jobs|roles|careers) |
        view\s+all |
        see\s+all |
        our\s+programs? |
        early\s+career\s+programs? |
        programs?\s+overview |
        life\s+at |
        working\s+at |
        meet\s+the\s+team |
        who\s+we\s+are |
        why\s+join\s+us |
        join\s+our\s+talent\s+community |
        talent\s+network |
        talent\s+pool
    )\b
    """,
    re.VERBOSE,
)



def clean_company_slug(name: str) -> str:
    """Generate a clean alphanumeric slug from company name."""
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", name.lower()).strip("_")
    return cleaned or "company"


class CustomCareerClient(BaseATSClient):
    """Client for directly scraping HTML career pages for engineering job links."""

    async def fetch_jobs(
        self,
        company: Union[CompanyConfig, str],
        career_url: Optional[str] = None,
    ) -> list[JobPosting]:
        """Fetch and parse engineering job links from a custom career webpage.

        Accepts either a CompanyConfig instance or company_name + career_url strings.
        """
        if isinstance(company, CompanyConfig):
            company_name = company.name
            target_url = company.career_url or company.board_token
        else:
            company_name = company
            target_url = career_url or ""

        if not target_url:
            logger.debug("No career_url provided for custom company: %s", company_name)
            return []

        postings: list[JobPosting] = []
        clean_slug = clean_company_slug(company_name)

        if "turbohire.co" in target_url.lower():
            return await self._fetch_turbohire_jobs(target_url, company_name, clean_slug)

        try:
            resp = await self.client.get(
                target_url,
                headers=DEFAULT_BROWSER_HEADERS,
                timeout=self.timeout,
                follow_redirects=True,
            )
            if resp.status_code != 200:
                logger.debug("Custom career page %s returned HTTP %s", target_url, resp.status_code)
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            seen_urls: set[str] = set()

            for link in soup.find_all("a", href=True):
                href = (link.get("href") or "").strip()
                if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                    continue

                text = link.get_text(separator=" ", strip=True)
                if not text or len(text) < 4:
                    # Check title or aria-label attributes
                    text = link.get("title") or link.get("aria-label") or ""
                    text = text.strip()

                if not text or len(text) < 4:
                    continue

                # Filter text strictly: must match target entry-level tech title
                if not matches_target_title(text):
                    continue

                # Disqualify portal navigational links and search CTAs (e.g. "Explore our programs", "Search Internships")
                if DISQUALIFIED_TEXT_PATTERN.search(text):
                    continue

                full_url = urljoin(target_url, href)
                url_lower = full_url.lower()

                # Disqualify external app stores, map links, parked domain ads, and non-job CTAs
                disqualified_domains = (
                    "apple.com", "google.com", "play.google.com", "maps.google.com",
                    "hostinger.com", "porkbun.com", "godaddy.com", "namecheap.com",
                    "twitter.com", "x.com", "facebook.com", "instagram.com", "youtube.com",
                    "gartner.com", "forrester.com", "trustradius.com", "g2.com",
                    "strikinglycdn.com",
                )
                if any(d in url_lower for d in disqualified_domains):
                    continue

                # Disqualify static document downloads (.pdf flyers, word docs, presentations)
                if any(url_lower.split("?")[0].endswith(ext) for ext in (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".ppt", ".pptx")):
                    continue

                disqualified_paths = (
                    "/contact", "/contact-us", "/login", "/signup", "/sign-up", "/register",
                    "/schedule-a-demo", "/request-a-demo", "/demo", "/pricing", "/privacy",
                    "/terms", "/legal", "/cookie", "/about-us", "/early-access", "/download",
                )
                if any(p in url_lower for p in disqualified_paths):
                    continue

                parsed_path = urllib.parse.urlparse(full_url).path
                if DISQUALIFIED_PATH_PATTERN.search(parsed_path):
                    continue

                # Disqualify if title or enclosing card text explicitly contains foreign locations without Indian presence
                text_lower = text.lower()
                parent_text = ""
                if link.parent and link.parent.name in ("li", "div", "tr", "article", "p", "section"):
                    if len(link.parent.find_all("a")) == 1:
                        parent_text = link.parent.get_text(separator=" ", strip=True).lower()
                combined_text = f"{text_lower} {parent_text}".strip()
                foreign_indicators = (
                    "san francisco", "california", " ca", ", ca", "new york", " ny", ", ny",
                    "singapore", "london", "seattle", "austin", "chicago", "dublin",
                    "berlin", "tokyo", "sydney", "toronto", "vancouver", "paris",
                    "madrid", "munich", "amsterdam", "zurich", "united states", "usa",
                    "united kingdom", "canada", "germany", "australia",
                )
                indian_indicators = (
                    "india", "bangalore", "bengaluru", "hyderabad", "pune", "mumbai",
                    "chennai", "noida", "gurgaon", "gurugram", "delhi", "ahmedabad",
                    "kolkata", "remote"
                )
                if any(fi in combined_text for fi in foreign_indicators) and not any(ii in combined_text for ii in indian_indicators):
                    continue

                clean_url = canonicalize_url(full_url)
                if clean_url in seen_urls:
                    continue
                seen_urls.add(clean_url)

                # Deterministic unique ID derived from SHA256 of canonical URL
                job_hash = hashlib.sha256(clean_url.encode("utf-8")).hexdigest()[:10]
                job_id = f"custom_{clean_slug}_{job_hash}"

                detected_location = "India"
                for city in ("Bengaluru", "Bangalore", "Hyderabad", "Pune", "Mumbai", "Chennai", "Noida", "Gurgaon", "Gurugram", "Delhi"):
                    if city.lower() in combined_text:
                        detected_location = f"{city}, India"
                        break

                try:
                    posting = JobPosting(
                        id=job_id,
                        company=company_name,
                        title=text,
                        location=detected_location,
                        apply_url=clean_url,
                        published_date="Recent",
                        provider=ATSProvider.CUSTOM,
                        is_remote=False,
                        status="NEW",
                    )
                    postings.append(posting)
                except ValidationError as err:
                    logger.debug("Validation error creating custom JobPosting for %s: %s", text, err)

        except Exception as exc:
            logger.debug("Error fetching custom career page for %s (%s): %s", company_name, target_url, exc)

        return postings

    async def _fetch_turbohire_jobs(
        self,
        target_url: str,
        company_name: str,
        clean_slug: str,
    ) -> list[JobPosting]:
        """Fetch and parse engineering jobs from a Turbohire career portal."""
        postings: list[JobPosting] = []
        parsed = urllib.parse.urlparse(target_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"

        m = re.search(r"([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})", target_url, re.IGNORECASE)
        if not m:
            return []
        org_id = m.group(1)

        token_headers = {
            "Origin": origin,
            "Referer": target_url,
            "User-Agent": DEFAULT_BROWSER_HEADERS["User-Agent"],
            "Accept": "application/json, text/plain, */*",
        }

        try:
            token_resp = await self.client.get(
                "https://api.turbohire.co/api/token/noauth",
                headers=token_headers,
                timeout=10.0,
            )
            if token_resp.status_code != 200:
                return []
            token = token_resp.json().get("access_token")
            if not token:
                return []

            auth_headers = {
                **token_headers,
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }

            # Query the public career page (pageType=0).
            # Note: pageType=1 corresponds to TurboHire's INTERNAL_JOB_PAGE (internal employee mobility),
            # which are not public postings and return 404 for external candidates.
            jobs_url = f"https://api.turbohire.co/api/careerpagev2/filteredjobs?orgId={org_id}&pageType=0"
            resp = await self.client.post(jobs_url, json={}, headers=auth_headers, timeout=15.0)
            if resp.status_code == 200:
                data = resp.json()
                raw_jobs = data.get("Result", [])
                seen_job_ids: set[str] = {p.id for p in postings}

                for j in raw_jobs:
                    job_uuid = j.get("JobId") or ""
                    if not job_uuid:
                        continue
                    job_id = f"turbohire_{clean_slug}_{job_uuid[:8]}"
                    if job_id in seen_job_ids:
                        continue

                    title = (j.get("JobTitle") or "").strip()
                    if not title or not matches_target_title(title):
                        continue

                    loc_raw = j.get("Location")
                    addr = ""
                    if isinstance(loc_raw, str) and loc_raw.startswith("["):
                        try:
                            parsed_loc = json.loads(loc_raw)
                            if parsed_loc and isinstance(parsed_loc, list):
                                addr = parsed_loc[0].get("Address", "")
                        except Exception:
                            addr = loc_raw
                    elif isinstance(loc_raw, list) and loc_raw:
                        addr = loc_raw[0].get("Address", "")
                    elif isinstance(loc_raw, str):
                        addr = loc_raw

                    if addr and not matches_india_location(addr):
                        continue

                    exp = j.get("Experience") or {}
                    min_exp = exp.get("MinExp", 0) or 0
                    if min_exp > 2:
                        continue

                    # Extract job description if available
                    raw_desc = j.get("JobDescV2") or j.get("JobDescription") or ""
                    clean_desc = re.sub(r"<[^>]+>", " ", raw_desc).strip() if raw_desc else ""

                    # Canonical TurboHire public job route (/job/publicjobs/:id)
                    apply_url = f"{origin}/job/publicjobs/{job_uuid}"

                    try:
                        posting = JobPosting(
                            id=job_id,
                            company=company_name,
                            title=title,
                            location=addr or "India",
                            apply_url=apply_url,
                            published_date=str(j.get("UpdatedDate") or "Recent")[:10],
                            provider=ATSProvider.CUSTOM,
                            is_remote=is_remote_opening(title) or is_remote_opening(addr),
                            description=clean_desc or None,
                            status="NEW",
                        )
                        if is_entry_level(posting, content=clean_desc):
                            postings.append(posting)
                            seen_job_ids.add(job_id)
                    except ValidationError as err:
                        logger.debug("Validation error for Turbohire job %s: %s", job_id, err)

        except Exception as exc:
            logger.debug("Error in _fetch_turbohire_jobs for %s: %s", company_name, exc)

        return postings
