"""Async ATS Company Discovery tool using DuckDuckGo search.

Discovers unindexed or newly active companies across Greenhouse, Ashby, Lever,
and SmartRecruiters using search-discovery heuristics, validates candidate
job boards via direct REST/JSON endpoints, dynamically excludes already-monitored
companies from gcc_job_radar/config.py, and optionally appends verified additions.
"""

import argparse
import asyncio
from dataclasses import asdict
from html.parser import HTMLParser
import json
import logging
import os
from pathlib import Path
import random
import re
import sys
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

import httpx

# Ensure project root is in sys.path when invoked directly
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Ensure standard streams use utf-8 encoding on headless/Windows loggers
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from dotenv import load_dotenv
load_dotenv(ROOT_DIR / ".env")

from gcc_job_radar.config import COMPANIES
from gcc_job_radar.models import ATSProvider
from tools.probe_ats import ProbeResult, append_to_config, deduplicate_results

try:
    from rich.console import Console

    console = Console()
    HAVE_RICH = True
except ImportError:
    HAVE_RICH = False

    class FallbackConsole:  # type: ignore[no-redef]
        def print(self, *args, **kwargs) -> None:
            clean_args = [
                re.sub(r"\[/?(?:bold|green|yellow|red|cyan|magenta|dim)[^\]]*\]", "", str(a))
                for a in args
            ]
            print(*clean_args)

    console = FallbackConsole()

logger = logging.getLogger(__name__)

import subprocess

PRIMARY_SOURCE = "https://raw.githubusercontent.com/akshaybhalotia/yc_companies/main/data/yc_companies.json"
FALLBACK_SOURCE = "https://raw.githubusercontent.com/alecf/yc-companies/master/data/companies.json"
CANONICAL_LIVE_SOURCE = "https://yc-oss.github.io/api/companies/all.json"
DEFAULT_SOURCES = [
    PRIMARY_SOURCE,
    FALLBACK_SOURCE,
    CANONICAL_LIVE_SOURCE,
]
DEFAULT_TIMEOUT = 60.0
DEFAULT_OUTPUT = Path("targets_yc.txt")
PROBE_SCRIPT_PATH = Path(__file__).resolve().parent / "probe_ats.py"

LEGAL_SUFFIXES = [
    "inc.",
    "inc",
    "llc.",
    "llc",
    "ltd.",
    "ltd",
    "corp.",
    "corp",
    "corporation",
    "limited",
    "technologies",
    "technology",
    "solutions",
    "software",
    "services",
    "holdings",
    "group",
    "co.",
    "co",
]


def normalize_company_name(name: str) -> str:
    """Normalize raw company name by stripping legal suffixes and punctuation."""
    if not name or not isinstance(name, str):
        return ""

    cleaned = name.strip().strip("\"'").strip()
    if not cleaned:
        return ""

    prev = ""
    while prev != cleaned:
        prev = cleaned
        for suffix in LEGAL_SUFFIXES:
            pattern = re.compile(rf"(?:,|\s)+\b{re.escape(suffix)}\b\.?$", re.IGNORECASE)
            cleaned = pattern.sub("", cleaned).strip()

    cleaned = re.sub(r"[,\-_.]+$", "", cleaned).strip()
    cleaned = re.sub(r"^[,\-_.]+", "", cleaned).strip()

    if cleaned.lower() in ("inc", "llc", "corp", "ltd", "co"):
        return ""

    return cleaned


def is_inactive_entry(company: dict) -> bool:
    """Determine if a YC company entry is inactive, dead, or defunct."""
    status = str(company.get("status", "")).strip().lower()
    if status in ("dead", "inactive", "closed", "defunct"):
        return True
    if company.get("dead") is True:
        return True
    if company.get("active") is False:
        return True
    return False


def fetch_yc_data(
    client: Optional[httpx.Client] = None,
    sources: Optional[list[str]] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[dict]:
    """Fetch YC company datasets with automatic failover."""
    source_list = sources or DEFAULT_SOURCES
    own_client = False
    if client is None:
        client = httpx.Client(timeout=timeout, follow_redirects=True)
        own_client = True

    try:
        for url in source_list:
            try:
                resp = client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list):
                        return data
                    elif isinstance(data, dict) and "companies" in data:
                        return data["companies"]
            except Exception as exc:
                logger.debug("Failed fetching YC data from %s: %s", url, exc)
                continue
    finally:
        if own_client:
            client.close()

    return []


def harvest_companies(
    raw_data: list[dict],
    existing_names: Optional[set[str]] = None,
    limit: Optional[int] = None,
) -> list[str]:
    """Extract normalized, active, deduplicated company names."""
    existing = {n.strip().lower() for n in (existing_names or set())}
    seen_in_batch: set[str] = set()
    harvested: list[str] = []

    for item in raw_data:
        if is_inactive_entry(item):
            continue

        raw_name = item.get("name") or ""
        clean_name = normalize_company_name(raw_name)
        if not clean_name:
            continue

        name_lower = clean_name.lower()
        if name_lower in existing or name_lower in seen_in_batch:
            continue

        seen_in_batch.add(name_lower)
        harvested.append(clean_name)

        if limit is not None and len(harvested) >= limit:
            break

    return harvested


def save_targets(targets: list[str], output_path: Path) -> int:
    """Save target list to output text file with one company per line."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [t.strip() for t in targets if t.strip()]
    output_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return len(lines)


def pipe_to_probe(
    target_file: Path,
    append: bool = False,
    workers: int = 15,
    timeout: float = 3.0,
) -> int:
    """Pipe target company names file into probe_ats.py."""
    if not target_file.exists():
        console.print(f"[bold red][X] Target file not found: {target_file}[/bold red]")
        return 1

    cmd = [
        sys.executable,
        str(PROBE_SCRIPT_PATH),
        "--file",
        str(target_file),
        "--workers",
        str(workers),
        "--timeout",
        str(timeout),
    ]
    if append:
        cmd.append("--append")

    console.print(f"[bold green][*] Piping {target_file} to probe_ats.py...[/bold green]")
    try:
        res = subprocess.run(cmd, check=False)
        return res.returncode
    except KeyboardInterrupt:
        console.print("\n[yellow][!] ATS probing interrupted by user.[/yellow]")
        return 130


def get_candidate_slugs(name: str) -> list[str]:
    """Generate candidate slug permutations (base, hyphenated, suffixes)."""
    base = re.sub(r"[^a-zA-Z0-9]+", "", name.lower())
    if not base or len(base) < 2:
        return []

    slugs = [base]
    hyphen = re.sub(r"[^a-zA-Z0-9]+", "-", name.lower()).strip("-")
    if hyphen != base and len(hyphen) >= 2:
        slugs.append(hyphen)

    if len(base) <= 12:
        for suffix in ("hq", "ai", "io", "tech"):
            slugs.append(f"{base}{suffix}")

    return list(dict.fromkeys(slugs))


def parse_yc_args(args: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse CLI arguments for YC harvesting."""
    parser = argparse.ArgumentParser(
        description="Harvester for Y Combinator directory datasets to discover high-growth tech companies.",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Target file destination path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--limit",
        "-l",
        type=int,
        default=None,
        help="Optional maximum number of candidate companies to harvest.",
    )
    parser.add_argument(
        "--pipe-to-probe",
        action="store_true",
        help="Seamlessly invoke tools/probe_ats.py with --file <output> --append after harvesting.",
    )
    return parser.parse_args(args)


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]

DDG_URL = "https://html.duckduckgo.com/html/"
SERPER_URL = "https://google.serper.dev/search"

SWE_QUERY_TERMS = [
    "software engineer",
    "backend engineer",
    "frontend engineer",
    "intern",
    "full stack engineer",
    "junior engineer",
    "sde",
    "associate engineer",
    "platform engineer",
    "infrastructure engineer",
]

PLATFORM_SPECS = {
    "greenhouse": {
        "provider": ATSProvider.GREENHOUSE,
        "site": "boards.greenhouse.io",
        "slug_re": re.compile(r"boards\.greenhouse\.io/([a-zA-Z0-9\-_]+)", re.IGNORECASE),
        "validate_url": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    },
    "ashby": {
        "provider": ATSProvider.ASHBY,
        "site": "jobs.ashbyhq.com",
        "slug_re": re.compile(r"jobs\.ashbyhq\.com/([a-zA-Z0-9\-_]+)", re.IGNORECASE),
        "validate_url": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
    },
    "lever": {
        "provider": ATSProvider.LEVER,
        "site": "jobs.lever.co",
        "slug_re": re.compile(r"jobs\.lever\.co/([a-zA-Z0-9\-_]+)", re.IGNORECASE),
        "validate_url": "https://api.lever.co/v0/postings/{slug}?mode=json",
    },
    "smartrecruiters": {
        "provider": ATSProvider.SMARTRECRUITERS,
        "site": "jobs.smartrecruiters.com",
        "slug_re": re.compile(r"jobs\.smartrecruiters\.com/([a-zA-Z0-9\-_]+)", re.IGNORECASE),
        "validate_url": "https://api.smartrecruiters.com/v1/companies/{slug}/postings",
    },
}

IGNORED_SLUGS = {
    "embed",
    "jobs",
    "search",
    "application",
    "applications",
    "careers",
    "openings",
    "all",
    "terms",
    "privacy",
    "index",
    "home",
}


class DDGLinkParser(HTMLParser):
    """Zero-dependency HTMLParser extracting result URLs from DuckDuckGo HTML search."""

    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag.lower() == "a":
            attr_dict = {k.lower(): v for k, v in attrs if v is not None}
            classes = attr_dict.get("class", "").split()
            # Match result links: class contains 'result__a' or 'result__url'
            if "result__a" in classes or "result__url" in classes:
                href = attr_dict.get("href", "")
                if href:
                    parsed = urlparse(href)
                    qs = parse_qs(parsed.query)
                    if "uddg" in qs and qs["uddg"]:
                        real_url = unquote(qs["uddg"][0])
                    else:
                        real_url = href
                    self.urls.append(real_url)


def slug_to_name(slug: str) -> str:
    """Turn a URL slug like 'my-cool-startup' or 'acme_labs' into a human-readable title."""
    cleaned = slug.replace("_", "-").replace(".", "-")
    words = [w for w in cleaned.split("-") if w]
    return " ".join(word.capitalize() for word in words) if words else slug.capitalize()


def extract_slugs_from_urls(urls: list[str], slug_re: re.Pattern) -> set[str]:
    """Extract and normalize unique company slugs from matched URLs using the platform regex."""
    slugs: set[str] = set()
    for u in urls:
        m = slug_re.search(u)
        if m:
            raw_slug = m.group(1).strip().lower()
            if raw_slug and raw_slug not in IGNORED_SLUGS:
                slugs.add(raw_slug)
    return slugs


def get_existing_exclusions() -> tuple[set[str], set[str]]:
    """Return set of existing board tokens and lowercased company names from config.COMPANIES."""
    existing_tokens = {c.board_token.strip().lower() for c in COMPANIES}
    existing_names = {c.name.strip().lower() for c in COMPANIES}
    return existing_tokens, existing_names


def is_excluded_slug(slug: str, existing_tokens: set[str], existing_names: set[str]) -> bool:
    """Check if a candidate slug or its derived name is already registered."""
    norm = slug.strip().lower()
    if norm in existing_tokens:
        return True
    derived = slug_to_name(norm).lower()
    if derived in existing_names:
        return True
    return False


async def polite_sleep_async(base: float = 2.5, jitter: float = 2.0) -> None:
    """Polite async delay with jitter to avoid triggering DDG rate-limits."""
    delay = base + random.random() * jitter
    await asyncio.sleep(delay)


async def serper_search_async(
    query: str,
    client: httpx.AsyncClient,
    api_key: Optional[str] = None,
    max_results: int = 30,
) -> list[str]:
    """Execute search via Serper.dev Google Search API and extract result URLs."""
    key = api_key or os.getenv("SERPER_API_KEY")
    if not key:
        return []

    headers = {
        "X-API-KEY": key,
        "Content-Type": "application/json",
    }
    payload = {
        "q": query,
        "num": min(max_results, 10),
    }

    try:
        resp = await client.post(SERPER_URL, headers=headers, json=payload, timeout=12.0)
        if resp.status_code == 200:
            data = resp.json()
            results: list[str] = []
            for item in data.get("organic", []):
                link = item.get("link")
                if link:
                    results.append(link)
            return results
        elif resp.status_code in (401, 403, 429):
            console.print(f"    [bold yellow][!][/bold yellow] Serper.dev returned {resp.status_code} (check API key / quota).")
            return []
        else:
            console.print(f"    [bold yellow][!][/bold yellow] Serper.dev returned status {resp.status_code}")
            return []
    except Exception as exc:
        console.print(f"    [bold red][!][/bold red] Serper request failed: {exc}")
        return []


async def ddg_search_async(
    query: str,
    client: httpx.AsyncClient,
    max_results: int = 30,
) -> list[str]:
    """Execute DuckDuckGo HTML search and extract result URLs."""
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    try:
        resp = await client.post(
            DDG_URL,
            data={"q": query},
            headers=headers,
            timeout=15.0,
        )
        if resp.status_code == 202:
            console.print(
                "    [bold yellow][!][/bold yellow] DuckDuckGo anti-bot challenge triggered (HTTP 202). "
                "DDG has temporarily rate-limited search queries from this IP."
            )
            return []
        elif resp.status_code != 200:
            console.print(f"    [bold yellow][!][/bold yellow] DDG returned status {resp.status_code} for query")
            return []
        parser = DDGLinkParser()
        parser.feed(resp.text)
        return parser.urls[:max_results]
    except Exception as exc:
        console.print(f"    [bold red][!][/bold red] DDG request failed: {exc}")
        return []


async def search_web_async(
    query: str,
    client: httpx.AsyncClient,
    max_results: int = 30,
) -> list[str]:
    """Execute search using Serper.dev (Google) if SERPER_API_KEY is available, falling back to DuckDuckGo."""
    serper_key = os.getenv("SERPER_API_KEY")
    if serper_key:
        urls = await serper_search_async(query, client, api_key=serper_key, max_results=max_results)
        if urls:
            return urls
        console.print("    [dim]Serper yielded 0 results; checking DuckDuckGo...[/dim]")

    return await ddg_search_async(query, client, max_results=max_results)


async def validate_greenhouse_slug(slug: str, client: httpx.AsyncClient) -> tuple[bool, int]:
    """Validate Greenhouse board via REST API."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    try:
        resp = await client.get(url, timeout=8.0)
        if resp.status_code != 200:
            return False, 0
        data = resp.json()
        if isinstance(data, dict) and isinstance(data.get("jobs"), list):
            jobs = data["jobs"]
            return len(jobs) > 0, len(jobs)
    except Exception:
        pass
    return False, 0


async def validate_ashby_slug(slug: str, client: httpx.AsyncClient) -> tuple[bool, int]:
    """Validate Ashby board via live JSON API (parses real active job postings)."""
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    try:
        resp = await client.get(url, timeout=8.0)
        if resp.status_code != 200:
            return False, 0
        data = resp.json()
        if isinstance(data, dict) and isinstance(data.get("jobs"), list):
            jobs = data["jobs"]
            return len(jobs) > 0, len(jobs)
    except Exception:
        pass
    return False, 0


async def validate_lever_slug(slug: str, client: httpx.AsyncClient) -> tuple[bool, int]:
    """Validate Lever board via JSON postings endpoint."""
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    try:
        resp = await client.get(url, timeout=8.0)
        if resp.status_code != 200:
            return False, 0
        data = resp.json()
        if isinstance(data, list):
            return len(data) > 0, len(data)
    except Exception:
        pass
    return False, 0


async def validate_smartrecruiters_slug(slug: str, client: httpx.AsyncClient) -> tuple[bool, int]:
    """Validate SmartRecruiters board via REST postings endpoint."""
    url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
    try:
        resp = await client.get(url, params={"limit": 5}, timeout=8.0)
        if resp.status_code != 200:
            return False, 0
        data = resp.json()
        if isinstance(data, dict):
            total = data.get("totalFound", 0)
            content = data.get("content", [])
            count = total if isinstance(total, int) and total > 0 else len(content)
            return count > 0, count
    except Exception:
        pass
    return False, 0


VALIDATORS = {
    "greenhouse": validate_greenhouse_slug,
    "ashby": validate_ashby_slug,
    "lever": validate_lever_slug,
    "smartrecruiters": validate_smartrecruiters_slug,
}


async def validate_candidate_slug(
    platform: str,
    slug: str,
    client: httpx.AsyncClient,
) -> Optional[ProbeResult]:
    """Validate a candidate slug against platform validator function."""
    validator = VALIDATORS.get(platform.lower())
    if not validator:
        return None

    ok, count = await validator(slug, client)
    if ok:
        spec = PLATFORM_SPECS[platform.lower()]
        company_name = slug_to_name(slug)
        return ProbeResult(
            company_name=company_name,
            provider=spec["provider"],
            board_token=slug,
            active_postings=count,
        )
    return None


async def discover_platform_candidates(
    platform: str,
    max_queries: int,
    client: httpx.AsyncClient,
    existing_tokens: set[str],
    existing_names: set[str],
    done_queries: set[str],
    state_file: Optional[Path] = None,
    company: Optional[str] = None,
) -> list[ProbeResult]:
    """Execute search discovery and validation for a single ATS platform."""
    spec = PLATFORM_SPECS.get(platform.lower())
    if not spec:
        console.print(f"[bold red]Unknown platform:[/bold red] {platform}")
        return []

    console.print(f"\n[bold magenta]=== DISCOVERING {platform.upper()} ({spec['site']}) ===[/bold magenta]")

    if company:
        queries = [f'site:{spec["site"]} "{company}"']
    else:
        queries = [f'site:{spec["site"]} "{term}"' for term in SWE_QUERY_TERMS][:max_queries]
    verified: list[ProbeResult] = []
    seen_in_run: set[str] = set()

    for q in queries:
        query_key = f"{platform}::{q}"
        if query_key in done_queries:
            console.print(f"  [dim](skip, already done)[/dim] {q}")
            continue

        console.print(f"  [cyan]Query:[/cyan] {q}")
        urls = await search_web_async(q, client, max_results=30)
        console.print(f"    -> [dim]{len(urls)} raw search URL(s)[/dim]")

        slugs = extract_slugs_from_urls(urls, spec["slug_re"])
        console.print(f"    -> [cyan]{len(slugs)} candidate slug(s)[/cyan]")

        # Filter against existing registry & already evaluated in this run
        new_candidates = [
            s for s in slugs
            if s not in seen_in_run and not is_excluded_slug(s, existing_tokens, existing_names)
        ]

        if new_candidates:
            console.print(f"    -> [yellow]Validating {len(new_candidates)} unrecorded candidate(s)...[/yellow]")
            for slug in new_candidates:
                seen_in_run.add(slug)
                res = await validate_candidate_slug(platform, slug, client)
                if res:
                    verified.append(res)
                    console.print(
                        f"      [bold green][+][/bold green] [bold white]{res.company_name}[/bold white] "
                        f"([cyan]{res.board_token}[/cyan] -> [green]{res.active_postings} jobs[/green])"
                    )
                # Brief politeness pause between direct API hits
                await asyncio.sleep(0.3)

        done_queries.add(query_key)
        if state_file:
            try:
                state_data = {
                    "done_queries": list(done_queries),
                    "verified": [asdict(v) for v in verified],
                }
                state_file.write_text(json.dumps(state_data, indent=2), encoding="utf-8")
            except Exception:
                pass

        if os.getenv("SERPER_API_KEY"):
            await asyncio.sleep(0.5)
        else:
            await polite_sleep_async(base=2.5, jitter=2.0)

    return verified


def parse_args(args: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse CLI options for discover_ats."""
    parser = argparse.ArgumentParser(
        description="Async ATS Company Discovery via Serper.dev / DuckDuckGo search.",
    )
    parser.add_argument(
        "--platforms", "-p",
        default="greenhouse,ashby,lever,smartrecruiters",
        help="Comma-separated list of ATS platforms to discover (default: greenhouse,ashby,lever,smartrecruiters)",
    )
    parser.add_argument(
        "--max-queries", "-m",
        type=int,
        default=10,
        help="Max search queries to execute per platform (default: 10)",
    )
    parser.add_argument(
        "--company", "-c",
        default=None,
        help="Optional company name to specifically search for across platforms",
    )
    parser.add_argument(
        "--append",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Automatically append newly verified companies to gcc_job_radar/config.py (default: True)",
    )
    parser.add_argument(
        "--state-file",
        default=None,
        help="Optional path to state JSON file to record/resume completed queries",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Optional path to export discovered JSON results",
    )
    return parser.parse_args(args)


async def main_async(args: argparse.Namespace) -> None:
    """Async coordinator for ATS discovery."""
    platforms = [p.strip().lower() for p in args.platforms.split(",") if p.strip()]
    existing_tokens, existing_names = get_existing_exclusions()
    engine_name = "Serper.dev (Google Search API)" if os.getenv("SERPER_API_KEY") else "DuckDuckGo HTML Search"
    console.print(
        f"[bold green][*][/bold green] Initialized dynamic exclusion: "
        f"[cyan]{len(existing_tokens)}[/cyan] tokens, [cyan]{len(existing_names)}[/cyan] company names from config.COMPANIES"
    )
    console.print(f"[bold green][*][/bold green] Search Engine: [bold cyan]{engine_name}[/bold cyan]")

    state_path = Path(args.state_file) if args.state_file else None
    done_queries: set[str] = set()
    if state_path and state_path.exists():
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            done_queries = set(data.get("done_queries", []))
            console.print(f"[dim]Loaded {len(done_queries)} completed queries from state file.[/dim]")
        except Exception:
            pass

    limits = httpx.Limits(max_connections=50, max_keepalive_connections=20)
    all_discovered: list[ProbeResult] = []

    async with httpx.AsyncClient(limits=limits, http2=True, timeout=12.0) as client:
        for p in platforms:
            if p not in PLATFORM_SPECS:
                console.print(f"[yellow]Skipping unsupported platform: '{p}'[/yellow]")
                continue
            res = await discover_platform_candidates(
                platform=p,
                max_queries=args.max_queries,
                client=client,
                existing_tokens=existing_tokens,
                existing_names=existing_names,
                done_queries=done_queries,
                state_file=state_path,
                company=args.company,
            )
            all_discovered.extend(res)

    # Deduplicate against current registry
    existing_token_pairs = {(c.provider, c.board_token.strip().lower()) for c in COMPANIES}
    to_add = deduplicate_results(all_discovered, existing_names, existing_token_pairs)

    console.print("\n" + "=" * 60)
    console.print(f"[bold green]DISCOVERY SUMMARY: {len(to_add)} NEW VERIFIED COMPANIES FOUND[/bold green]")
    console.print("=" * 60)

    if to_add:
        for r in to_add:
            console.print(
                f"  • [bold white]{r.company_name}[/bold white] "
                f"([cyan]{r.provider.value}[/cyan] -> [yellow]{r.board_token}[/yellow], [green]{r.active_postings} jobs[/green])"
            )

        if args.append:
            appended = append_to_config(to_add)
            console.print(
                f"\n[bold green][+][/bold green] Successfully appended [bold green]{appended}[/bold green] "
                "verified company entry/entries to gcc_job_radar/config.py!"
            )
        else:
            console.print("\n[dim]--no-append specified. Configuration file was not modified.[/dim]")

        if args.output:
            out_path = Path(args.output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            export_data = [
                {
                    "company_name": r.company_name,
                    "provider": r.provider.value,
                    "board_token": r.board_token,
                    "active_postings": r.active_postings,
                }
                for r in to_add
            ]
            out_path.write_text(json.dumps(export_data, indent=2), encoding="utf-8")
            console.print(f"[dim]Exported discoveries to {out_path}[/dim]")
    else:
        console.print("[dim]No new unrecorded companies discovered during this sweep.[/dim]")


def main() -> None:
    """CLI entry point for discover_ats."""
    args = parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
