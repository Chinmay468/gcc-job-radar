"""Amazon Jobs ATS API client."""

import logging
from typing import Any
import httpx
import orjson
from pydantic import ValidationError

from gcc_job_radar.clients.base import BaseATSClient
from gcc_job_radar.models import ATSProvider, CompanyConfig, JobPosting

logger = logging.getLogger(__name__)

DEFAULT_AMAZON_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 gcc-job-radar/0.1.0"
)

DEFAULT_TECH_CATEGORIES = [
    "software-development",
    "data-science",
    "systems-quality-security-engineering",
    "solutions-architect",
    "machine-learning-science",
]


class AmazonClient(BaseATSClient):
    """Amazon Jobs search API client (amazon.jobs)."""

    BASE_URL = "https://www.amazon.jobs/en/search.json"

    async def fetch_jobs(self, company: CompanyConfig) -> list[JobPosting]:
        """Fetch and filter open tech jobs from Amazon Jobs API."""
        extra: dict[str, Any] = company.extra or {}
        country = extra.get("country", "IND")
        categories = extra.get("categories", DEFAULT_TECH_CATEGORIES)
        max_pages = int(extra.get("max_pages", 2))
        page_size = min(int(extra.get("result_limit", 100)), 100)

        headers = {
            "User-Agent": DEFAULT_AMAZON_USER_AGENT,
            "Accept": "application/json, text/plain, */*",
        }

        postings: list[JobPosting] = []
        seen_ids: set[str] = set()

        for page in range(max_pages):
            offset = page * page_size
            params: list[tuple[str, str]] = [
                ("country", country),
                ("sort", "recent"),
                ("result_limit", str(page_size)),
                ("offset", str(offset)),
            ]
            for cat in categories:
                params.append(("category[]", cat))

            try:
                response = await self.client.get(
                    self.BASE_URL,
                    params=params,
                    headers=headers,
                    timeout=self.timeout,
                )
                if response.status_code != 200:
                    logger.debug(
                        "Amazon Jobs returned status %s for page %d",
                        response.status_code,
                        page,
                    )
                    break

                data = orjson.loads(response.content)
                jobs = data.get("jobs", [])
                if not jobs:
                    break

                for job in jobs:
                    job_id = str(job.get("id_icims") or job.get("id") or "").strip()
                    if not job_id or job_id in seen_ids:
                        continue

                    title = (job.get("title") or "").strip()
                    location = (
                        job.get("normalized_location")
                        or job.get("location")
                        or job.get("city")
                        or "India"
                    ).strip()

                    desc = job.get("description") or ""
                    basic_qual = job.get("basic_qualifications") or ""
                    pref_qual = job.get("preferred_qualifications") or ""
                    content = f"{desc} {basic_qual} {pref_qual}".strip()

                    if not self.is_target_role(title, location, content):
                        continue

                    job_path = job.get("job_path") or f"/en/jobs/{job_id}"
                    if job_path.startswith("http://") or job_path.startswith("https://"):
                        apply_url = job_path
                    else:
                        apply_url = f"https://www.amazon.jobs{job_path}"

                    published_date = self.parse_date(job.get("posted_date"))

                    try:
                        postings.append(
                            JobPosting(
                                id=job_id,
                                company=company.name,
                                title=title,
                                location=location,
                                apply_url=apply_url,
                                published_date=published_date,
                                provider=ATSProvider.AMAZON,
                                description=content[:1500] if content else None,
                            )
                        )
                        seen_ids.add(job_id)
                    except ValidationError as e:
                        logger.debug("Validation error parsing Amazon job %s: %s", job_id, e)

                hits = data.get("hits", 0)
                if offset + page_size >= hits:
                    break

            except (httpx.TimeoutException, httpx.HTTPError, Exception) as exc:
                logger.debug("Error fetching Amazon jobs on page %d: %s", page, exc)
                break

        return postings
