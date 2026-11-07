"""Uber Careers ATS API client via Oracle Cloud HCM Recruiting CE API."""

import logging
from typing import Any
import httpx
import orjson
from pydantic import ValidationError

from gcc_job_radar.clients.base import BaseATSClient
from gcc_job_radar.models import ATSProvider, CompanyConfig, JobPosting

logger = logging.getLogger(__name__)

DEFAULT_UBER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 gcc-job-radar/0.1.0"
)

DEFAULT_ORACLE_CE_URL = (
    "https://iaziqy.fa.ocs.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
)


class UberClient(BaseATSClient):
    """Uber Careers ATS client backed by Oracle Cloud HCM Candidate Experience API."""

    BASE_URL = DEFAULT_ORACLE_CE_URL

    async def fetch_jobs(self, company: CompanyConfig) -> list[JobPosting]:
        """Fetch and filter open tech jobs from Uber's careers requisition API."""
        extra: dict[str, Any] = company.extra or {}
        site_number = extra.get("site_number", "UberCareers")
        location = extra.get("location", "India")
        max_pages = int(extra.get("max_pages", 2))
        page_size = min(int(extra.get("result_limit", 100)), 100)

        headers = {
            "User-Agent": DEFAULT_UBER_USER_AGENT,
            "Accept": "application/json",
        }

        postings: list[JobPosting] = []
        seen_ids: set[str] = set()

        for page in range(max_pages):
            offset = page * page_size
            finder_parts = [
                f"siteNumber={site_number}",
                f"offset={offset}",
                f"limit={page_size}",
            ]
            if location:
                finder_parts.append(f"location={location}")

            finder_val = f"findReqs;{','.join(finder_parts)}"
            params = {
                "finder": finder_val,
                "expand": "requisitionList",
            }

            try:
                response = await self.client.get(
                    self.BASE_URL,
                    params=params,
                    headers=headers,
                    timeout=self.timeout,
                )
                if response.status_code != 200:
                    logger.debug(
                        "Uber Careers API returned status %s for page %d",
                        response.status_code,
                        page,
                    )
                    break

                data = orjson.loads(response.content)
                items = data.get("items", [])
                if not items:
                    break

                first_item = items[0]
                jobs = first_item.get("requisitionList", [])
                if not jobs:
                    break

                for job in jobs:
                    job_id = str(job.get("Id") or "").strip()
                    if not job_id or job_id in seen_ids:
                        continue

                    title = (job.get("Title") or "").strip()
                    loc = (job.get("PrimaryLocation") or location or "India").strip()

                    desc = job.get("ShortDescriptionStr") or ""
                    qual = job.get("ExternalQualificationsStr") or ""
                    resp = job.get("ExternalResponsibilitiesStr") or ""
                    content = f"{desc} {qual} {resp}".strip()

                    if not self.is_target_role(title, loc, content):
                        continue

                    published_date = self.parse_date(job.get("PostedDate"))
                    workplace = str(
                        job.get("WorkplaceTypeCode")
                        or job.get("WorkplaceType")
                        or ""
                    ).lower()
                    is_remote = "remote" in workplace or "remote" in loc.lower()

                    numeric_id = int(job_id) if job_id.isdigit() else None
                    apply_url = f"https://www.uber.com/global/en/careers/list/{job_id}/"

                    try:
                        posting = JobPosting(
                            id=job_id,
                            numeric_id=numeric_id,
                            company=company.name,
                            title=title,
                            location=loc,
                            apply_url=apply_url,  # type: ignore[arg-type]
                            published_date=published_date,
                            provider=ATSProvider.UBER,
                            is_remote=is_remote,
                            status="NEW",
                            description=content[:2000] if content else None,
                        )
                        seen_ids.add(job_id)
                        postings.append(posting)
                    except ValidationError as exc:
                        logger.debug("Validation error parsing Uber job %s: %s", job_id, exc)

                total_jobs = first_item.get("TotalJobsCount", 0)
                if (offset + page_size) >= total_jobs:
                    break

            except (httpx.TransportError, httpx.TimeoutException) as exc:
                logger.debug("Network error fetching Uber jobs page %d: %s", page, exc)
                break
            except Exception as exc:
                logger.debug("Unexpected error fetching Uber jobs page %d: %s", page, exc)
                break

        return postings
