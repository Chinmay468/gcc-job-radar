"""Base class for canonical ATS API clients."""

from abc import ABC, abstractmethod
import asyncio
import logging
from typing import Any, Optional
import httpx

from gcc_job_radar.filters import (
    is_tech_role,
    matches_india_location,
    matches_target_title,
    normalize_date_str,
    requires_experienced_candidate,
)
from gcc_job_radar.models import CompanyConfig, JobPosting

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class BaseATSClient(ABC):
    """Abstract base class for querying canonical ATS job boards."""

    def __init__(self, client: httpx.AsyncClient, timeout: httpx.Timeout = DEFAULT_TIMEOUT) -> None:
        self.client = client
        self.timeout = timeout

    @abstractmethod
    async def fetch_jobs(self, company: CompanyConfig) -> list[JobPosting]:
        """Fetch and filter open jobs for a company from the ATS API."""
        raise NotImplementedError

    @staticmethod
    def is_target_role(
        title: str,
        location: str,
        content: str = "",
        check_tech: bool = True,
    ) -> bool:
        """Check if job title, location, and optional content match entry-level Indian tech criteria."""
        if not matches_target_title(title):
            return False
        if check_tech and not is_tech_role(title):
            return False
        if not matches_india_location(location):
            return False
        if content and requires_experienced_candidate(content):
            return False
        return True

    @staticmethod
    def parse_date(date_val: Any, default: str = "Recent") -> str:
        """Normalize a date string/timestamp into ISO format (YYYY-MM-DD) or default."""
        if not date_val:
            return default
        clean_val = str(date_val).strip()
        norm = normalize_date_str(clean_val)
        return norm if norm else default

    async def get_with_retry(
        self,
        url: str,
        params: Any = None,
        headers: Any = None,
        max_retries: int = 2,
        backoff_sec: float = 1.0,
    ) -> Optional[httpx.Response]:
        """Perform HTTP GET with exponential backoff on transient network or rate limit errors."""
        for attempt in range(max_retries + 1):
            try:
                resp = await self.client.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=self.timeout,
                )
                if resp.status_code == 429 or resp.status_code >= 500:
                    if attempt < max_retries:
                        await asyncio.sleep(backoff_sec * (2**attempt))
                        continue
                return resp
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                if attempt < max_retries:
                    await asyncio.sleep(backoff_sec * (2**attempt))
                else:
                    logger.debug("HTTP GET %s failed after %d attempts: %s", url, max_retries + 1, exc)
                    return None
        return None
