""""Microsoft Careers ATS client using Eightfold PCSX API."""

from datetime import datetime, timezone
import logging
from typing import Any
import httpx
import orjson
from pydantic import ValidationError

from gcc_job_radar.clients.base import BaseATSClient
from gcc_job_radar.models import ATSProvider, CompanyConfig, JobPosting

logger = logging.getLogger(__name__)

DEFAULT_MICROSOFT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


class MicrosoftClient(BaseATSClient):
    """Microsoft Careers API client (Eightfold.ai PCSX backend)."""

    BASE_URL = "https://microsoft.eightfold.ai/api/pcsx/search"

    async def fetch_jobs(self, company: CompanyConfig) -> list[JobPosting]:
        """Fetch and filter open tech jobs from Microsoft Careers."""
        extra: dict[str, Any] = company.extra or {}
        location_query = extra.get("location", "India")
        search_query = extra.get("query", "")
        max_pages = int(extra.get("max_pages", 3))
        page_size = min(int(extra.get("page_size", 20)), 50)

        headers = {
            "User-Agent": DEFAULT_MICROSOFT_USER_AGENT,
            "Accept": "application/json, text/plain, */*",
        }

        postings: list[JobPosting] = []
        seen_ids: set[str] = set()

        for page in range(max_pages):
            start = page * page_size
            params: dict[str, str] = {
                "domain": "microsoft.com",
                "location": location_query,
                "sort_by": "timestamp",
                "start": str(start),
                "num": str(page_size),
            }
            if search_query:
                params["query"] = search_query

            try:
                response = await self.client.get(
                    self.BASE_URL,
                    params=params,
                    headers=headers,
                    timeout=self.timeout,
                )
                if response.status_code != 200:
                    logger.debug(
                        "Microsoft Careers returned status %s for page %d",
                        response.status_code,
                        page,
                    )
                    break

                try:
                    payload = orjson.loads(response.content)
                except Exception:
                    payload = response.json()

                data = payload.get("data", {}) if isinstance(payload, dict) else {}
                jobs = data.get("positions", [])
                if not jobs:
                    break

                for job in jobs:
                    job_id = str(job.get("id") or job.get("atsJobId") or "").strip()
                    if not job_id or job_id in seen_ids:
                        continue

                    title = (job.get("name") or "").strip()
                    loc_list: list[str] = []
                    for key in ("locations", "standardizedLocations"):
                        val = job.get(key)
                        if isinstance(val, list):
                            loc_list.extend(str(item).strip() for item in val if item)
                        elif isinstance(val, str) and val.strip():
                            loc_list.append(val.strip())
                    location = ", ".join(dict.fromkeys(loc_list)) if loc_list else "India"

                    dept = job.get("department") or ""
                    content = f"{title} {dept}".strip()
                    if not self.is_target_role(title, location, content):
                        continue

                    pos_url = job.get("positionUrl") or f"/careers/job/{job_id}"
                    if pos_url.startswith("http://") or pos_url.startswith("https://"):
                        apply_url = pos_url
                    else:
                        if not pos_url.startswith("/"):
                            pos_url = "/" + pos_url
                        apply_url = f"https://apply.careers.microsoft.com{pos_url}"

                    posted_ts = job.get("postedTs") or job.get("creationTs")
                    published_date = "Recent"
                    if posted_ts:
                        try:
                            published_date = datetime.fromtimestamp(
                                float(posted_ts), tz=timezone.utc
                            ).strftime("%Y-%m-%d")
                        except (ValueError, TypeError, OSError):
                            published_date = "Recent"

                    try:
                        postings.append(
                            JobPosting(
                                id=job_id,
                                company=company.name,
                                title=title,
                                location=location,
                                apply_url=apply_url,
                                published_date=published_date,
                                provider=ATSProvider.MICROSOFT,
                                description=content or None,
                            )
                        )
                        seen_ids.add(job_id)
                    except ValidationError as e:
                        logger.debug("Validation error parsing Microsoft job %s: %s", job_id, e)

                total_count = data.get("count", 0)
                if total_count and (start + page_size >= total_count):
                    break

            except (httpx.TimeoutException, httpx.HTTPError, Exception) as exc:
                logger.debug("Error fetching Microsoft jobs on page %d: %s", page, exc)
                break

        return postings
