"""High-throughput concurrent scanning engine for GCC Job Radar."""

import asyncio
import gc
import logging
import os
import random
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import httpx
import orjson

from gcc_job_radar.clients.amazon import AmazonClient
from gcc_job_radar.clients.apple import AppleClient
from gcc_job_radar.clients.ashby import AshbyClient
from gcc_job_radar.clients.base import DEFAULT_TIMEOUT
from gcc_job_radar.clients.ea import EAClient
from gcc_job_radar.clients.greenhouse import GreenhouseClient
from gcc_job_radar.clients.lever import LeverClient
from gcc_job_radar.clients.microsoft import MicrosoftClient
from gcc_job_radar.clients.phenom_successfactors import PhenomSuccessFactorsClient
from gcc_job_radar.clients.smartrecruiters import SmartRecruitersClient
from gcc_job_radar.clients.workday import WorkdayClient
from gcc_job_radar.clients.custom_career import CustomCareerClient
from gcc_job_radar.config import COMPANIES
from gcc_job_radar.filters import matches_target_title
from gcc_job_radar.models import ATSProvider, CompanyConfig, JobPosting

logger = logging.getLogger(__name__)

try:
    import h2

    HAS_HTTP2 = bool(h2)
except ImportError:
    HAS_HTTP2 = False

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 gcc-job-radar/0.1.0"
)

DEFAULT_GLOBAL_CONCURRENCY: int = int(os.getenv("SCAN_CONCURRENCY", "30"))


def fast_json_loads(data: str | bytes | httpx.Response) -> Any:
    """Ultra-fast JSON deserialization using orjson instead of standard json.loads."""
    if isinstance(data, httpx.Response):
        return orjson.loads(data.content)
    if isinstance(data, str):
        return orjson.loads(data.encode("utf-8"))
    return orjson.loads(data)
DEFAULT_HOST_LIMIT: int = 5

DEFAULT_DOMAIN_LIMITS: dict[str, int] = {
    "greenhouse.io": 5,
    "lever.co": 5,
    "ashbyhq.com": 5,
    "smartrecruiters.com": 5,
    "myworkdayjobs.com": 5,
    "amazon.jobs": 5,
    "microsoft.eightfold.ai": 5,
    "jobs.apple.com": 5,
    "jobs.ea.com": 5,
}


def get_company_domain(company: CompanyConfig) -> str:
    """Map company configuration to its primary ATS host/domain for concurrency throttling."""
    if company.provider == ATSProvider.GREENHOUSE:
        return "greenhouse.io"
    elif company.provider == ATSProvider.LEVER:
        return "lever.co"
    elif company.provider == ATSProvider.ASHBY:
        return "ashbyhq.com"
    elif company.provider == ATSProvider.SMARTRECRUITERS:
        return "smartrecruiters.com"
    elif company.provider == ATSProvider.WORKDAY:
        return "myworkdayjobs.com"
    elif company.provider == ATSProvider.AMAZON:
        return "amazon.jobs"
    elif company.provider == ATSProvider.MICROSOFT:
        return "microsoft.eightfold.ai"
    elif company.provider == ATSProvider.APPLE:
        return "jobs.apple.com"
    elif company.provider == ATSProvider.EA:
        return "jobs.ea.com"
    elif company.provider == ATSProvider.PHENOM_SUCCESSFACTORS:

        token = company.board_token.strip()
        if token.startswith("http://") or token.startswith("https://"):
            parsed = urlparse(token)
            return parsed.netloc or "phenom.com"
        return token.split("/")[0] or "phenom.com"
    elif company.provider == ATSProvider.CUSTOM or str(company.provider).lower() == "custom":
        target = company.career_url or company.board_token
        if target.startswith("http://") or target.startswith("https://"):
            parsed = urlparse(target)
            return parsed.netloc or "custom_career"
        return "custom_career"
    return "default"


class HostRateLimiter:
    """Manages global and domain-specific concurrency semaphores."""

    def __init__(
        self,
        global_concurrency: int = DEFAULT_GLOBAL_CONCURRENCY,
        domain_limits: Optional[dict[str, int]] = None,
        default_domain_limit: int = DEFAULT_HOST_LIMIT,
    ) -> None:
        self.global_semaphore = asyncio.Semaphore(global_concurrency)
        self.domain_limits: dict[str, int] = dict(DEFAULT_DOMAIN_LIMITS)
        if domain_limits:
            self.domain_limits.update(domain_limits)
        self.default_domain_limit = default_domain_limit
        self._domain_semaphores: dict[str, asyncio.Semaphore] = {}

    def get_domain_semaphore(self, domain: str) -> asyncio.Semaphore:
        """Retrieve or lazily instantiate an asyncio.Semaphore for a given host domain."""
        if domain not in self._domain_semaphores:
            limit = self.domain_limits.get(domain, self.default_domain_limit)
            self._domain_semaphores[domain] = asyncio.Semaphore(limit)
        return self._domain_semaphores[domain]


class RetryTransport(httpx.AsyncBaseTransport):
    """Async transport wrapper providing exponential backoff and jitter for HTTP 429 and connection errors."""

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport,
        max_retries: int = 3,
        base_delay: float = 0.5,
        max_delay: float = 10.0,
        jitter: bool = True,
    ) -> None:
        self._transport = transport
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter = jitter
        self.retry_count = 0  # Useful for inspection and unit testing

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        # Buffer request content so replayed retries can resend body if needed
        await request.aread()

        attempt = 0
        while True:
            try:
                response = await self._transport.handle_async_request(request)
                if response.status_code == 429 and attempt < self.max_retries:
                    attempt += 1
                    self.retry_count += 1
                    retry_after = response.headers.get("Retry-After")
                    delay = self._calculate_delay(attempt, retry_after)
                    logger.debug(
                        "Rate limited (HTTP 429) for %s. Retrying in %.2fs (attempt %d/%d)...",
                        request.url,
                        delay,
                        attempt,
                        self.max_retries,
                    )
                    await response.aclose()
                    await asyncio.sleep(delay)
                    continue
                return response
            except (
                httpx.ConnectError,
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                httpx.WriteTimeout,
                httpx.PoolTimeout,
                httpx.RemoteProtocolError,
            ) as exc:
                if attempt < self.max_retries:
                    attempt += 1
                    self.retry_count += 1
                    delay = self._calculate_delay(attempt, None)
                    logger.debug(
                        "Transient connection error (%s) for %s. Retrying in %.2fs (attempt %d/%d)...",
                        type(exc).__name__,
                        request.url,
                        delay,
                        attempt,
                        self.max_retries,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise

    def _calculate_delay(self, attempt: int, retry_after: Optional[str]) -> float:
        if retry_after:
            try:
                return min(float(retry_after), self.max_delay)
            except (ValueError, TypeError):
                pass
        backoff = self.base_delay * (2 ** (attempt - 1))
        if self.jitter:
            backoff += random.uniform(0.0, 0.25 * backoff)
        return min(backoff, self.max_delay)

    async def aclose(self) -> None:
        await self._transport.aclose()


async def fetch_single_company(company: CompanyConfig, client: httpx.AsyncClient) -> list[JobPosting]:
    """Route company to appropriate ATS client and fetch filtered postings."""
    if company.provider == ATSProvider.GREENHOUSE:
        ats_client = GreenhouseClient(client)
    elif company.provider == ATSProvider.LEVER:
        ats_client = LeverClient(client)
    elif company.provider == ATSProvider.ASHBY:
        ats_client = AshbyClient(client)
    elif company.provider == ATSProvider.SMARTRECRUITERS:
        ats_client = SmartRecruitersClient(client)
    elif company.provider == ATSProvider.WORKDAY:
        ats_client = WorkdayClient(client)
    elif company.provider == ATSProvider.PHENOM_SUCCESSFACTORS:
        ats_client = PhenomSuccessFactorsClient(client)
    elif company.provider == ATSProvider.AMAZON:
        ats_client = AmazonClient(client)
    elif company.provider == ATSProvider.MICROSOFT:
        ats_client = MicrosoftClient(client)
    elif company.provider == ATSProvider.APPLE:
        ats_client = AppleClient(client)
    elif company.provider == ATSProvider.EA:
        ats_client = EAClient(client)
    elif company.provider == ATSProvider.CUSTOM or str(company.provider).lower() == "custom":
        ats_client = CustomCareerClient(client)
    else:

        logger.warning("Unsupported ATS provider: %s", company.provider)
        return []

    try:
        return await ats_client.fetch_jobs(company)
    except Exception as exc:
        logger.debug("Error fetching jobs for %s: %s", company.name, exc)
        return []


async def scan_all_companies(
    companies: list[CompanyConfig] = COMPANIES,
    concurrency: int = DEFAULT_GLOBAL_CONCURRENCY,
    on_progress: Optional[Callable[[str, int, int], None]] = None,
    client: Optional[httpx.AsyncClient] = None,
    domain_limits: Optional[dict[str, int]] = None,
    max_retries: int = 3,
    base_retry_delay: float = 0.5,
) -> list[JobPosting]:
    """Scan configured companies with bounded global/domain concurrency and exponential backoff retries."""
    if not companies:
        return []

    rate_limiter = HostRateLimiter(
        global_concurrency=concurrency,
        domain_limits=domain_limits,
    )
    total_companies = len(companies)
    completed_count = 0

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
    }

    close_client = False
    if client is None:
        limits = httpx.Limits(
            max_connections=max(20, concurrency * 2),
            max_keepalive_connections=min(20, concurrency),
            keepalive_expiry=15.0,
        )
        base_transport = httpx.AsyncHTTPTransport(retries=0, http2=HAS_HTTP2, limits=limits)
        transport = RetryTransport(
            base_transport,
            max_retries=max_retries,
            base_delay=base_retry_delay,
        )
        client = httpx.AsyncClient(
            transport=transport,
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
            follow_redirects=True,
            http2=HAS_HTTP2,
        )
        close_client = True
    else:
        # Wrap provided client transport with RetryTransport if not already wrapped
        if not isinstance(client._transport, RetryTransport):
            client._transport = RetryTransport(
                client._transport,
                max_retries=max_retries,
                base_delay=base_retry_delay,
            )

    all_postings: list[JobPosting] = []
    seen_keys: set[tuple[str, str]] = set()
    lock = asyncio.Lock()

    queue: asyncio.Queue[CompanyConfig] = asyncio.Queue()
    for company in companies:
        queue.put_nowait(company)

    worker_count = min(concurrency, max(1, total_companies))

    async def worker() -> None:
        nonlocal completed_count
        while True:
            try:
                company = queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            domain = get_company_domain(company)
            domain_sem = rate_limiter.get_domain_semaphore(domain)
            results: list[JobPosting] = []

            try:
                async with rate_limiter.global_semaphore:
                    async with domain_sem:
                        try:
                            results = await fetch_single_company(company, client)
                        except Exception as exc:
                            logger.debug("Scan error for %s: %s", company.name, exc)
                            results = []
            finally:
                completed_count += 1
                if on_progress:
                    on_progress(company.name, completed_count, total_companies)
                queue.task_done()

            if results:
                valid_posts = []
                for post in results:
                    # Defense-in-depth: enforce strict entry-level tech title matching
                    if not matches_target_title(post.title):
                        continue
                    key = (post.company.lower(), str(post.id).lower())
                    valid_posts.append((key, post))

                if valid_posts:
                    async with lock:
                        for key, post in valid_posts:
                            if key not in seen_keys:
                                seen_keys.add(key)
                                all_postings.append(post)

            # Periodic garbage collection every 100 companies completed
            if completed_count % 100 == 0:
                gc.collect()

    try:
        workers = [asyncio.create_task(worker()) for _ in range(worker_count)]
        if workers:
            await asyncio.gather(*workers)
    finally:
        if close_client:
            await client.aclose()
        gc.collect()

    # Sort postings by published_date descending (newest first, 'Active' or unknown at the end)
    def sort_key(p: JobPosting) -> tuple[int, str]:
        date = p.published_date or ""
        if date and date != "Active" and date != "Recent":
            return (0, date)
        return (1, "")

    all_postings.sort(key=sort_key, reverse=True)
    return all_postings
