"""Automated ATS candidate token discovery and verification CLI tool.

Probes target companies across Greenhouse, Lever, Ashby, and SmartRecruiters REST APIs,
validates live job boards via HTTP 200 responses with valid JSON payloads,
and outputs formatted ASCII tables, Python CompanyConfig snippets, or directly appends
to gcc_job_radar/config.py.
"""

import argparse
import asyncio
from dataclasses import dataclass
import logging
from pathlib import Path
import re
import sys
from typing import Callable, Optional

import httpx

from gcc_job_radar.config import COMPANIES
from gcc_job_radar.models import ATSProvider

try:
    from rich.console import Console
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
        TimeRemainingColumn,
    )

    HAVE_RICH = True
except ImportError:
    HAVE_RICH = False

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent.parent / "gcc_job_radar" / "config.py"

DEFAULT_CONCURRENCY = 50
DEFAULT_LIMITS = httpx.Limits(max_connections=250, max_keepalive_connections=100)
DEFAULT_TIMEOUT = httpx.Timeout(2.0, connect=1.0)
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) gcc-job-radar/probe-ats/1.0",
    "Accept": "application/json, text/plain, */*",
}

from tools.discovery_utils import (
    CORPORATE_SUFFIXES,
    KNOWN_ABBREVIATIONS,
    clean_company_name,
    generate_slug_candidates,
)


@dataclass
class ProbeResult:
    """Represents a successfully discovered and verified ATS job board."""

    company_name: str
    provider: ATSProvider
    board_token: str
    active_postings: int


async def check_platform(
    provider: ATSProvider,
    slug: str,
    client: httpx.AsyncClient,
) -> Optional[int]:
    """Test a single slug against a specific ATS platform.

    Issues an HTTP HEAD pre-flight request first. Discards immediately if status != 200.
    Only executes HTTP GET when HEAD returns 200 to validate non-empty payload / active postings.
    """
    try:
        url: str
        params: Optional[dict[str, int]] = None

        if provider == ATSProvider.ASHBY:
            url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
        elif provider == ATSProvider.GREENHOUSE:
            url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
        elif provider == ATSProvider.LEVER:
            url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
        elif provider == ATSProvider.SMARTRECRUITERS:
            url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
            params = {"limit": 5}
        else:
            return None

        # 1. HTTP HEAD pre-flight: discard immediately if not 200
        head_resp = await client.head(url, params=params)
        if head_resp.status_code != 200:
            return None

        # 2. HTTP GET only when HEAD returns 200 to validate non-empty payload
        resp = await client.get(url, params=params)
        if resp.status_code != 200:
            return None

        # 3. Validate non-empty payload / active postings
        if provider in (ATSProvider.ASHBY, ATSProvider.GREENHOUSE):
            data = resp.json()
            if isinstance(data, dict) and isinstance(data.get("jobs"), list):
                jobs = data["jobs"]
                if len(jobs) > 0:
                    return len(jobs)

        elif provider == ATSProvider.LEVER:
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                return len(data)

        elif provider == ATSProvider.SMARTRECRUITERS:
            data = resp.json()
            if isinstance(data, dict):
                total_found = data.get("totalFound", 0)
                content = data.get("content", [])
                if isinstance(total_found, int) and total_found > 0:
                    return total_found
                if isinstance(content, list) and len(content) > 0:
                    return len(content)

    except Exception as exc:
        logger.debug("Probe error for %s on %s: %s", slug, provider, exc)

    return None


async def probe_company(
    company_name: str,
    client: httpx.AsyncClient,
    existing_names: set[str],
    semaphore: Optional[asyncio.Semaphore] = None,
) -> Optional[ProbeResult]:
    """Probe a single company across platforms. Stops immediately when a live board is found."""
    name_clean = company_name.strip()
    if not name_clean:
        return None

    if name_clean.lower() in existing_names:
        logger.debug("Skipping '%s': already registered in config.py", name_clean)
        return None

    slugs = generate_slug_candidates(name_clean)
    # Prioritized ATS sequence based on hit probability
    platforms = [
        ATSProvider.ASHBY,
        ATSProvider.GREENHOUSE,
        ATSProvider.LEVER,
        ATSProvider.SMARTRECRUITERS,
    ]

    for slug in slugs:
        for provider in platforms:
            if semaphore is not None:
                async with semaphore:
                    active_count = await check_platform(provider, slug, client)
            else:
                active_count = await check_platform(provider, slug, client)

            if active_count is not None:
                return ProbeResult(
                    company_name=name_clean,
                    provider=provider,
                    board_token=slug,
                    active_postings=active_count,
                )

    return None


async def probe_companies(
    target_names: list[str],
    client: httpx.AsyncClient,
    existing_names: set[str],
    concurrency: int = DEFAULT_CONCURRENCY,
    show_progress: bool = True,
    verbose: bool = False,
) -> list[ProbeResult]:
    """Probe multiple target companies concurrently using an asyncio.Semaphore worker pool."""
    cleaned_targets: list[str] = []
    for t in target_names:
        c = t.strip()
        if c and c not in cleaned_targets:
            cleaned_targets.append(c)

    if not cleaned_targets:
        return []

    semaphore = asyncio.Semaphore(concurrency)
    results: list[ProbeResult] = []
    results_lock = asyncio.Lock()

    async def worker(
        name: str,
        advance_cb: Optional[Callable[[], None]] = None,
        log_cb: Optional[Callable[[str, Optional[ProbeResult], bool], None]] = None,
    ) -> Optional[ProbeResult]:
        name_clean = name.strip()
        if not name_clean:
            if advance_cb:
                advance_cb()
            return None

        if name_clean.lower() in existing_names:
            if log_cb:
                log_cb(name_clean, None, True)
            if advance_cb:
                advance_cb()
            return None

        async with semaphore:
            res = await probe_company(name_clean, client, existing_names)

        if log_cb:
            log_cb(name_clean, res, False)

        if advance_cb:
            advance_cb()

        if res is not None:
            async with results_lock:
                results.append(res)
        return res

    if show_progress and HAVE_RICH:
        console = Console()
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TextColumn("•"),
            TimeElapsedColumn(),
            TextColumn("•"),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task_id = progress.add_task("Probing candidate companies", total=len(cleaned_targets))

            def advance_cb() -> None:
                progress.advance(task_id)

            def log_cb(target: str, res: Optional[ProbeResult], is_skip: bool) -> None:
                if is_skip:
                    progress.console.print(f"[yellow][SKIP][/yellow] '{target}' is already registered in config.py")
                elif res is not None:
                    prov = res.provider.name.capitalize() if hasattr(res.provider, "name") else str(res.provider).capitalize()
                    progress.console.print(
                        f"[bold green][FOUND][/bold green] '{res.company_name}' on [cyan]{prov}[/cyan] "
                        f"(token: '[bold]{res.board_token}[/bold]', active postings: [bold]{res.active_postings}[/bold])"
                    )
                elif verbose:
                    progress.console.print(f"[dim][NOT FOUND][/dim] '{target}'")

            tasks = [worker(target, advance_cb, log_cb) for target in cleaned_targets]
            await asyncio.gather(*tasks)

    elif show_progress:
        completed = 0
        total = len(cleaned_targets)

        def advance_cb() -> None:
            nonlocal completed
            completed += 1

        def log_cb(target: str, res: Optional[ProbeResult], is_skip: bool) -> None:
            if is_skip:
                print(f"[SKIP] '{target}' is already registered in config.py")
            elif res is not None:
                prov = res.provider.name.capitalize() if hasattr(res.provider, "name") else str(res.provider).capitalize()
                print(f"[{completed + 1}/{total}] [FOUND] '{res.company_name}' on {prov} (token: '{res.board_token}', active postings: {res.active_postings})")
            elif verbose:
                print(f"[{completed + 1}/{total}] [NOT FOUND] '{target}'")

        tasks = [worker(target, advance_cb, log_cb) for target in cleaned_targets]
        await asyncio.gather(*tasks)

    else:
        tasks = [worker(target) for target in cleaned_targets]
        await asyncio.gather(*tasks)

    return results


def format_results_table(results: list[ProbeResult]) -> str:
    """Format verified probe results into a clean ASCII table."""
    if not results:
        return "No verified ATS job boards discovered."

    headers = ["Company", "Platform", "Verified Board Slug", "Total Active Postings"]
    rows: list[list[str]] = [
        [
            r.company_name,
            r.provider.name.capitalize() if hasattr(r.provider, "name") else str(r.provider).capitalize(),
            r.board_token,
            str(r.active_postings),
        ]
        for r in results
    ]

    # Calculate column widths
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            col_widths[i] = max(col_widths[i], len(val))

    sep_border = "+" + "+".join("-" * (w + 2) for w in col_widths) + "+"
    header_str = (
        "| " + " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers)) + " |"
    )

    lines = [sep_border, header_str, sep_border]
    for row in rows:
        line = "| " + " | ".join(row[i].ljust(col_widths[i]) for i in range(len(row))) + " |"
        lines.append(line)
    lines.append(sep_border)

    return "\n".join(lines)


def format_python_snippets(results: list[ProbeResult]) -> str:
    """Format verified probe results into copy-pasteable CompanyConfig Python code."""
    lines = ["# Verified CompanyConfig entries:"]
    for r in results:
        provider_name = r.provider.name if hasattr(r.provider, "name") else str(r.provider).upper()
        lines.append(
            f'    CompanyConfig(name="{r.company_name}", provider=ATSProvider.{provider_name}, board_token="{r.board_token}"),'
        )
    return "\n".join(lines)


def append_to_config(
    results: list[ProbeResult],
    config_path: Path = CONFIG_PATH,
    cluster: Optional[str] = None,
) -> int:
    """Append verified entries to gcc_job_radar/config.py COMPANIES list."""
    if not results:
        return 0

    content = config_path.read_text(encoding="utf-8")

    # Match the end of the COMPANIES list: right before line `]\n`
    pattern = re.compile(r"(\n    CompanyConfig\([^\n]+\),\n)(\])")
    match = pattern.search(content)

    new_entries: list[str] = ["\n    # Newly probed and verified companies\n"]
    for r in results:
        provider_name = r.provider.name if hasattr(r.provider, "name") else str(r.provider).upper()
        if cluster:
            new_entries.append(
                f'    CompanyConfig(name="{r.company_name}", provider=ATSProvider.{provider_name}, board_token="{r.board_token}", cluster="{cluster}"),\n'
            )
        else:
            new_entries.append(
                f'    CompanyConfig(name="{r.company_name}", provider=ATSProvider.{provider_name}, board_token="{r.board_token}"),\n'
            )

    insert_text = "".join(new_entries)
    if match:
        # Insert before closing ]
        new_content = content[: match.start(2)] + insert_text + content[match.start(2) :]
    else:
        # Fallback: search for first ']\n\n# Strict entry-level'
        idx = content.find("]\n\n# Strict entry-level")
        if idx != -1:
            new_content = content[:idx] + insert_text + content[idx:]
        else:
            raise ValueError(f"Could not locate COMPANIES list closing bracket in {config_path}")

    config_path.write_text(new_content, encoding="utf-8")
    return len(results)


def deduplicate_results(
    results: list[ProbeResult],
    existing_names: set[str],
    existing_tokens: set[tuple[ATSProvider, str]],
) -> list[ProbeResult]:
    """Deduplicate verified probe results against existing registry and within the batch."""
    deduped: list[ProbeResult] = []
    seen_names = set(existing_names)
    seen_tokens = set(existing_tokens)

    for r in results:
        name_key = r.company_name.strip().lower()
        token_key = (r.provider, r.board_token.strip().lower())
        if name_key in seen_names or token_key in seen_tokens:
            continue
        seen_names.add(name_key)
        seen_tokens.add(token_key)
        deduped.append(r)

    return deduped


def parse_args(args: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments for probe_ats tool."""
    parser = argparse.ArgumentParser(
        description="Automated ATS candidate token discovery and verification CLI tool.",
    )
    parser.add_argument("--name", help="Single company name to probe (e.g. 'Texas Instruments')")
    parser.add_argument("--names", help="Comma-separated company names to probe (e.g. 'TI, Western Digital')")
    parser.add_argument("--file", help="Path to text file containing company names, one per line")
    parser.add_argument("--append", action="store_true", help="Automatically append verified entries to config.py")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"Max concurrent company probes (default: {DEFAULT_CONCURRENCY})",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Display failed / not found attempts")
    return parser.parse_args(args)


async def main_async(args: argparse.Namespace) -> None:
    """Async entrypoint for probe_ats."""
    target_names: list[str] = []

    if args.name:
        target_names.append(args.name.strip())

    if args.names:
        for n in args.names.split(","):
            if n.strip():
                target_names.append(n.strip())

    if args.file:
        file_path = Path(args.file)
        if not file_path.exists():
            print(f"Error: Target file '{args.file}' not found.", file=sys.stderr)
            sys.exit(1)
        for line in file_path.read_text(encoding="utf-8").splitlines():
            line_clean = line.strip()
            if line_clean and not line_clean.startswith("#"):
                target_names.append(line_clean)

    if not target_names:
        print("Error: No company names specified. Use --name, --names, or --file.", file=sys.stderr)
        sys.exit(1)

    # Deduplicate targets preserving order
    target_names = list(dict.fromkeys(target_names))

    existing_names = {c.name.strip().lower() for c in COMPANIES}
    existing_tokens = {(c.provider, c.board_token.strip().lower()) for c in COMPANIES}

    concurrency = getattr(args, "concurrency", DEFAULT_CONCURRENCY)
    verbose = getattr(args, "verbose", False)

    async with httpx.AsyncClient(
        headers=DEFAULT_HEADERS,
        limits=DEFAULT_LIMITS,
        timeout=DEFAULT_TIMEOUT,
    ) as client:
        print(
            f"Probing {len(target_names)} company target(s) across Greenhouse, Lever, Ashby, SmartRecruiters "
            f"(concurrency={concurrency}, timeout={DEFAULT_TIMEOUT.read}s)..."
        )

        verified_results = await probe_companies(
            target_names=target_names,
            client=client,
            existing_names=existing_names,
            concurrency=concurrency,
            show_progress=True,
            verbose=verbose,
        )

    # Deduplicate verified results against registry and within the batch
    to_append = deduplicate_results(verified_results, existing_names, existing_tokens)

    print("\n" + "=" * 60)
    print("PROBE RESULTS SUMMARY")
    print("=" * 60)
    print(format_results_table(verified_results))
    print("\n" + format_python_snippets(verified_results))

    if args.append and to_append:
        count = append_to_config(to_append)
        print(f"\nSuccessfully appended {count} verified company entry/entries to {CONFIG_PATH}!")
    elif args.append and not to_append and verified_results:
        print("\nAll verified companies are already present in the registry. Nothing appended.")


def main() -> None:
    """CLI script entry point."""
    args = parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
