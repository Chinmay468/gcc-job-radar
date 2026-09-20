"""Electronic Arts (EA) Careers ATS client."""

import logging
import re
from typing import Any
import httpx
from pydantic import ValidationError

from gcc_job_radar.clients.base import BaseATSClient
from gcc_job_radar.models import ATSProvider, CompanyConfig, JobPosting

logger = logging.getLogger(__name__)

DEFAULT_EA_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

ARTICLE_REGEX = re.compile(
    r'<article[^>]*class=["\'][^"\']*article--result[^"\']*["\'][^>]*>(.*?)</article>',
    re.DOTALL | re.IGNORECASE,
)
TITLE_URL_REGEX = re.compile(
    r'<a[^>]*class=["\'][^"\']*link_result[^"\']*["\'][^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
LOCATION_REGEX = re.compile(
    r'<span[^>]*class=["\'][^"\']*list-item-location[^"\']*["\'][^>]*>(.*?)</span>',
    re.DOTALL | re.IGNORECASE,
)
ROLE_ID_REGEX = re.compile(
    r'<span[^>]*class=["\'][^"\']*list-item-id[^"\']*["\'][^>]*>.*?(\d+).*?</span>',
    re.DOTALL | re.IGNORECASE,
)


class EAClient(BaseATSClient):
    """Electronic Arts Careers client (jobs.ea.com)."""

    BASE_URL = "https://jobs.ea.com/en_US/careers/SearchJobs/India"

    async def fetch_jobs(self, company: CompanyConfig) -> list[JobPosting]:
        """Fetch and filter open tech and engineering jobs from EA India Careers."""
        extra: dict[str, Any] = company.extra or {}
        records_per_page = int(extra.get("records_per_page", 50))
        max_offset = int(extra.get("max_offset", 200))

        headers = {
            "User-Agent": DEFAULT_EA_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

        postings: list[JobPosting] = []
        seen_ids: set[str] = set()
        offset = 0

        client = self.client
        close_client = False
        if client is None:
            client = httpx.AsyncClient(headers=headers, timeout=self.timeout, follow_redirects=True)
            close_client = True

        try:
            while offset <= max_offset:
                params = {
                    "jobRecordsPerPage": str(records_per_page),
                    "jobOffset": str(offset),
                }

                try:
                    resp = await client.get(self.BASE_URL, params=params, headers=headers)
                except httpx.HTTPError as exc:
                    logger.warning(f"Error fetching EA Careers at offset {offset}: {exc}")
                    break

                if resp.status_code != 200:
                    logger.warning(f"EA Careers returned HTTP {resp.status_code} at offset {offset}")
                    break

                html = resp.text
                articles = ARTICLE_REGEX.findall(html)
                if not articles:
                    break

                page_new_count = 0
                for article_html in articles:
                    m_title = TITLE_URL_REGEX.search(article_html)
                    if not m_title:
                        continue

                    raw_url = m_title.group(1).strip()
                    raw_title = re.sub(r"<[^>]+>", "", m_title.group(2)).strip()
                    if not raw_title or not raw_url:
                        continue

                    # Role ID extraction
                    m_role = ROLE_ID_REGEX.search(article_html)
                    if m_role:
                        role_id = m_role.group(1)
                    else:
                        m_url_id = re.search(r"/(\d+)(?:[/?#]|$)", raw_url)
                        role_id = m_url_id.group(1) if m_url_id else raw_url

                    job_key = f"ea_{role_id}"
                    if job_key in seen_ids:
                        continue
                    seen_ids.add(job_key)
                    page_new_count += 1

                    # Location
                    m_loc = LOCATION_REGEX.search(article_html)
                    location = (
                        re.sub(r"<[^>]+>", "", m_loc.group(1)).strip()
                        if m_loc
                        else "Hyderabad, India"
                    )

                    # Filters
                    if not self.is_target_role(raw_title, location, check_tech=False):
                        continue

                    try:
                        posting = JobPosting(
                            id=job_key,
                            company="Electronic Arts",
                            title=raw_title,
                            location=location,
                            apply_url=raw_url,
                            provider=ATSProvider.EA,
                            is_remote="remote" in location.lower() or "remote" in raw_title.lower(),
                        )
                        postings.append(posting)
                    except ValidationError as ve:
                        logger.debug(f"Pydantic validation error for EA job {job_key}: {ve}")

                # If no new jobs were seen or reached the last page
                if page_new_count == 0 or ("Next >>" not in html and "jobOffset=" not in html):
                    break

                offset += records_per_page
        finally:
            if close_client:
                await client.aclose()

        logger.info(f"Fetched {len(postings)} verified active tech roles from Electronic Arts India")
        return postings
