""""Apple Careers ATS client using Remix SSR hydration data."""

import logging
import re
from typing import Any
import httpx
import orjson
from pydantic import ValidationError

from gcc_job_radar.clients.base import BaseATSClient
from gcc_job_radar.models import ATSProvider, CompanyConfig, JobPosting

logger = logging.getLogger(__name__)

DEFAULT_APPLE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

HYDRATION_REGEX = re.compile(
    r'window\.__staticRouterHydrationData\s*=\s*JSON\.parse\("(.*?)"\);',
    re.DOTALL,
)


class AppleClient(BaseATSClient):
    """Apple Careers client (jobs.apple.com Remix SSR)."""

    BASE_URL = "https://jobs.apple.com/en-in/search"

    async def fetch_jobs(self, company: CompanyConfig) -> list[JobPosting]:
        """Fetch and filter open tech jobs from Apple Careers."""
        extra: dict[str, Any] = company.extra or {}
        location_code = extra.get("location", "india-INDC")
        sort_by = extra.get("sort", "newest")
        search_query = extra.get("query", "")
        max_pages = int(extra.get("max_pages", 3))

        headers = {
            "User-Agent": DEFAULT_APPLE_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

        postings: list[JobPosting] = []
        seen_ids: set[str] = set()

        for page in range(max_pages):
            page_num = page + 1
            params: dict[str, str] = {
                "location": location_code,
                "sort": sort_by,
                "page": str(page_num),
            }
            if search_query:
                params["search"] = search_query

            try:
                response = await self.client.get(
                    self.BASE_URL,
                    params=params,
                    headers=headers,
                    timeout=self.timeout,
                    follow_redirects=True,
                )
                if response.status_code != 200:
                    logger.debug(
                        "Apple Careers returned status %s for page %d",
                        response.status_code,
                        page_num,
                    )
                    break

                match = HYDRATION_REGEX.search(response.text)
                if not match:
                    logger.debug(
                        "Apple Careers page %d missing hydration data script",
                        page_num,
                    )
                    break

                try:
                    raw_json = match.group(1).encode("utf-8").decode("unicode_escape")
                    data = orjson.loads(raw_json)
                except Exception as exc:
                    logger.debug("Failed to decode Apple hydration JSON: %s", exc)
                    break

                search_data = (
                    data.get("loaderData", {}).get("search", {})
                    if isinstance(data, dict)
                    else {}
                )
                jobs = search_data.get("searchResults", [])
                if not jobs:
                    break

                for job in jobs:
                    position_id = str(job.get("positionId") or "").strip()
                    job_id = str(job.get("id") or position_id).strip()
                    if not job_id or job_id in seen_ids:
                        continue

                    title = (job.get("postingTitle") or "").strip()
                    raw_locs = job.get("locations") or []
                    if isinstance(raw_locs, list) and raw_locs:
                        names = [
                            loc.get("name")
                            for loc in raw_locs
                            if isinstance(loc, dict) and loc.get("name")
                        ]
                        location = ", ".join(names) if names else "India"
                    else:
                        location = "India"

                    team_info = job.get("team")
                    team_name = team_info.get("teamName") if isinstance(team_info, dict) else ""
                    content = f"{title} {team_name or ''}".strip()

                    if not self.is_target_role(title, location, content):
                        continue

                    apply_id = position_id or job_id
                    apply_url = f"https://jobs.apple.com/en-in/details/{apply_id}"

                    published_date = self.parse_date(job.get("postingDate"))

                    try:
                        postings.append(
                            JobPosting(
                                id=job_id,
                                company=company.name,
                                title=title,
                                location=location,
                                apply_url=apply_url,
                                published_date=published_date,
                                provider=ATSProvider.APPLE,
                                description=content or None,
                            )
                        )
                        seen_ids.add(job_id)
                    except ValidationError as e:
                        logger.debug("Validation error parsing Apple job %s: %s", job_id, e)

                total_records = search_data.get("totalRecords", 0)
                if total_records and (page_num * len(jobs) >= total_records):
                    break

            except (httpx.TimeoutException, httpx.HTTPError, Exception) as exc:
                logger.debug("Error fetching Apple jobs on page %d: %s", page_num, exc)
                break

        return postings
