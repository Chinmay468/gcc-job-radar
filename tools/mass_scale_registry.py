#!/usr/bin/env python3
"""Mass ATS Expansion Engine for GCC Job Radar.

Scales COMPANIES in gcc_job_radar/config.py to 10,000+ verified active ATS company boards by:
1. Streaming high-probability candidate pools:
   - Direct ATS links from OpenJobs and SimplifyJobs repositories.
   - Y Combinator active companies from official live directory.
   - Operating venture-backed tech companies from tools/50k_companies.csv.
   - Full permutations from tools/50k_clean_tech_names.txt:
     * Base slug (stripped lowercase alphanumeric)
     * Hyphenated slug (multi-word names)
     * Suffix variants: ${slug}hq, ${slug}ai, ${slug}io, ${slug}tech, ${slug}labs
2. Concurrently probing Ashby, Greenhouse, Lever, and SmartRecruiters endpoints (queue concurrency: 100).
3. Verifying HTTP 200 and >0 active job listings.
4. Strict deduplication against existing entries in config.py (case-insensitive name and (provider, slug)).
5. Periodically flushing discovered entries into gcc_job_radar/config.py until the target is reached.
"""

import argparse
import asyncio
import csv
import logging
import os
from pathlib import Path
import re
import sys
from typing import NamedTuple, Optional
import urllib.parse
import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

# Ensure standard streams use utf-8 on Windows
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from gcc_job_radar.config import COMPANIES
from gcc_job_radar.dormant_companies import DORMANT_COMPANIES
from gcc_job_radar.models import ATSProvider
from tools.discovery_utils import clean_company_name
from tools.harvest_mass_ats import extract_slug_from_url

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("mass_scale_registry")

DEFAULT_TARGET = 10000
DEFAULT_CONCURRENCY = 100
FLUSH_INTERVAL = 50

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 gcc-job-radar/mass-scaler"
    ),
    "Accept": "application/json, text/plain, */*",
}

RESERVED_SLUGS: set[str] = {
    "embed", "search", "jobs", "careers", "openings", "api", "apply", "assets",
    "static", "css", "js", "v1", "v2", "v0", "privacy", "terms", "about",
    "login", "help", "support", "dashboard", "widget", "iframe", "webhook",
    "internal", "feed", "rss", "app", "boards", "posting", "postings",
    "company", "companies", "job-board", "index", "home", "admin", "auth",
}


class VerifiedBoard(NamedTuple):
    company_name: str
    provider: str
    board_token: str
    active_jobs: int



def generate_permutations(name: str) -> list[str]:
    """Generate slug permutations:
    - Base slug (lowercase stripped alphanumeric)
    - Hyphenated slug (multi-word names)
    - Suffix variants: ${slug}hq, ${slug}ai, ${slug}io, ${slug}tech, ${slug}labs
    """
    clean = clean_company_name(name)
    base = re.sub(r"[^a-zA-Z0-9]+", "", clean.lower())
    if not base or len(base) < 2 or base in RESERVED_SLUGS:
        return []

    hyphen = re.sub(r"[^a-zA-Z0-9]+", "-", clean.lower()).strip("-")
    slugs = [base]
    if hyphen and hyphen != base and hyphen not in RESERVED_SLUGS:
        slugs.append(hyphen)

    if len(base) <= 14:
        for suffix in ("hq", "ai", "io", "tech", "labs"):
            slugs.append(f"{base}{suffix}")
            if hyphen and hyphen != base:
                slugs.append(f"{hyphen}-{suffix}")

    return list(dict.fromkeys(s for s in slugs if len(s) >= 2 and s not in RESERVED_SLUGS))


async def probe_ats_candidate(
    client: httpx.AsyncClient,
    company_name: str,
    provider: str,
    slug: str,
) -> Optional[VerifiedBoard]:
    """Verify an ATS endpoint returns HTTP 200 and has >0 active job listings."""
    endpoints = {
        "ashby": f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
        "greenhouse": f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
        "lever": f"https://api.lever.co/v0/postings/{slug}?mode=json",
        "smartrecruiters": f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=5",
    }

    url = endpoints.get(provider)
    if not url:
        return None

    try:
        res = await client.get(url, timeout=3.5)
        if res.status_code == 200:
            data = res.json()
            jobs: list = []
            if isinstance(data, list):
                jobs = data
            elif isinstance(data, dict):
                if provider == "smartrecruiters":
                    total = data.get("totalFound")
                    content = data.get("content") or []
                    if (isinstance(total, int) and total > 0) or len(content) > 0:
                        return VerifiedBoard(
                            company_name=company_name,
                            provider=provider,
                            board_token=slug,
                            active_jobs=total if isinstance(total, int) and total > 0 else len(content),
                        )
                    return None
                jobs = data.get("jobs") or data.get("content") or []

            if len(jobs) > 0:
                return VerifiedBoard(
                    company_name=company_name,
                    provider=provider,
                    board_token=slug,
                    active_jobs=len(jobs),
                )
    except Exception:
        pass

    return None


def flush_boards_to_config(boards: list[VerifiedBoard], config_path: Path) -> int:
    """Safely append newly verified boards to COMPANIES in config.py."""
    if not boards:
        return 0

    content = config_path.read_text(encoding="utf-8")
    marker = "\n]\n\n# Strict entry-level tech title positive pattern"

    if marker not in content:
        pattern = re.compile(r"(\n\]\s*\n\s*#\s*Strict entry-level)", re.MULTILINE)
        m = pattern.search(content)
        if not m:
            logger.error("Could not find insertion marker in %s", config_path)
            return 0
        insert_pos = m.start()
    else:
        insert_pos = content.index(marker)

    # Re-scan file to guarantee zero duplicate names or duplicate tokens per provider
    existing_in_file_names = {n.strip().lower() for n in re.findall(r'CompanyConfig\(name="([^"]+)"', content)}
    existing_in_file_tokens = {
        (p.strip().lower(), t.strip().lower())
        for p, t in re.findall(r'provider=ATSProvider\.([A-Z]+),\s*board_token="([^"]+)"', content)
    }

    lines_to_add: list[str] = []
    seen_in_flush_names = set()
    seen_in_flush_tokens = set()

    for b in boards:
        clean_name = clean_company_name(b.company_name).replace("\\", "").replace('"', "")
        if not clean_name:
            clean_name = b.board_token.title()
        n_low = clean_name.lower()
        tok_low = (b.provider.lower(), b.board_token.lower())

        if n_low in existing_in_file_names or n_low in seen_in_flush_names:
            continue
        if tok_low in existing_in_file_tokens or tok_low in seen_in_flush_tokens:
            continue

        seen_in_flush_names.add(n_low)
        seen_in_flush_tokens.add(tok_low)
        existing_in_file_names.add(n_low)
        existing_in_file_tokens.add(tok_low)

        provider_enum = f"ATSProvider.{b.provider.upper()}"
        lines_to_add.append(
            f'    CompanyConfig(name="{clean_name}", provider={provider_enum}, board_token="{b.board_token}"),'
        )

    if not lines_to_add:
        return 0

    block = "\n" + "\n".join(lines_to_add)
    updated = content[:insert_pos] + block + content[insert_pos:]
    config_path.write_text(updated, encoding="utf-8")
    return len(lines_to_add)


async def harvest_direct_candidates(client: httpx.AsyncClient) -> list[tuple[str, str, str]]:
    """Harvest high-probability ATS candidates from OpenJobs and SimplifyJobs."""
    candidates: list[tuple[str, str, str]] = []
    seen = set()

    sources = [
        ("OpenJobs", "https://raw.githubusercontent.com/outscal/OpenJobs/main/data/companies_v2.json"),
        ("SimplifyJobs Summer 2025", "https://raw.githubusercontent.com/SimplifyJobs/Summer2025-Internships/dev/.github/scripts/listings.json"),
        ("SimplifyJobs New Grad", "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json"),
        ("SimplifyJobs Summer 2024", "https://raw.githubusercontent.com/SimplifyJobs/Summer2024-Internships/dev/.github/scripts/listings.json"),
    ]

    for label, url in sources:
        try:
            print(f"[*] Fetching seed candidate pool from {label}...", flush=True)
            resp = await client.get(url, timeout=20.0)
            if resp.status_code == 200:
                data = resp.json()
                count = 0
                if isinstance(data, list):
                    for item in data:
                        if "ats_links" in item or "list_urls" in item:
                            c_name = item.get("name") or ""
                            urls = (item.get("ats_links") or []) + (item.get("list_urls") or [])
                            for u in urls:
                                ext = extract_slug_from_url(u)
                                if ext:
                                    prov, slug = ext
                                    prov_str = str(prov).split(".")[-1].lower()
                                    key = (prov_str, slug.lower())
                                    if key not in seen:
                                        seen.add(key)
                                        candidates.append((c_name or slug.title(), prov_str, slug))
                                        count += 1
                                    break
                        elif "url" in item:
                            c_name = item.get("company_name") or ""
                            u = item.get("url") or ""
                            ext = extract_slug_from_url(u)
                            if ext:
                                prov, slug = ext
                                prov_str = str(prov).split(".")[-1].lower()
                                key = (prov_str, slug.lower())
                                if key not in seen:
                                    seen.add(key)
                                    candidates.append((c_name or slug.title(), prov_str, slug))
                                    count += 1
                print(f"    -> Harvested {count} unique direct ATS candidates from {label}", flush=True)
        except Exception as exc:
            logger.warning("Could not fetch %s: %s", label, exc)

    return candidates


async def harvest_yc_candidates(client: httpx.AsyncClient) -> list[tuple[str, str, str]]:
    """Harvest active tech companies from Y Combinator directory."""
    candidates: list[tuple[str, str, str]] = []
    seen = set()
    try:
        print("[*] Fetching Y Combinator active directory...", flush=True)
        resp = await client.get("https://yc-oss.github.io/api/companies/all.json", timeout=20.0)
        if resp.status_code == 200:
            data = resp.json()
            for c in data:
                if c.get("status") != "Active":
                    continue
                name = c.get("name") or ""
                slug = c.get("slug") or ""
                if not slug:
                    continue
                for prov in ("ashby", "greenhouse", "lever"):
                    key = (prov, slug.lower())
                    if key not in seen:
                        seen.add(key)
                        candidates.append((name or slug.title(), prov, slug.lower()))
            print(f"    -> Extracted {len(candidates)} YC probe candidates across Ashby, Greenhouse, Lever", flush=True)
    except Exception as exc:
        logger.warning("Could not fetch YC companies: %s", exc)
    return candidates


def harvest_csv_candidates() -> list[tuple[str, str, str]]:
    """Harvest operating funded tech companies from 50k_companies.csv."""
    csv_file = PROJECT_ROOT / "tools" / "50k_companies.csv"
    if not csv_file.exists():
        return []

    print("[*] Harvesting operating tech companies from 50k_companies.csv...", flush=True)
    candidates: list[tuple[str, str, str]] = []
    seen = set()

    with open(csv_file, encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("status") != "operating":
                continue

            name = (row.get("name") or "").strip()
            permalink = (row.get("permalink") or "").replace("/organization/", "").strip().lower()
            homepage = (row.get("homepage_url") or "").strip()

            dom = ""
            if homepage:
                try:
                    parsed = urllib.parse.urlparse(homepage)
                    net = parsed.netloc.lower().replace("www.", "").split(".")[0]
                    if len(net) >= 2:
                        dom = net
                except Exception:
                    pass

            slugs = [s for s in [permalink, dom] if s and len(s) >= 2 and s not in RESERVED_SLUGS]
            slugs = list(dict.fromkeys(slugs))

            for s in slugs:
                for prov in ("ashby", "greenhouse", "lever"):
                    key = (prov, s)
                    if key not in seen:
                        seen.add(key)
                        candidates.append((name or s.title(), prov, s))

    print(f"    -> Extracted {len(candidates)} candidates from 50k_companies.csv", flush=True)
    return candidates


async def run_mass_scale(target: int = DEFAULT_TARGET, concurrency: int = DEFAULT_CONCURRENCY) -> None:
    """Orchestrate queue-driven probing until target verified companies are registered."""
    config_file = PROJECT_ROOT / "gcc_job_radar" / "config.py"
    names_file = PROJECT_ROOT / "tools" / "50k_clean_tech_names.txt"

    dormant_names = {c.name.strip().lower() for c in DORMANT_COMPANIES}
    dormant_tokens = {(str(c.provider).split(".")[-1].lower(), c.board_token.strip().lower()) for c in DORMANT_COMPANIES}

    existing_names: set[str] = {c.name.strip().lower() for c in COMPANIES} | dormant_names
    existing_tokens: set[tuple[str, str]] = {
        (str(c.provider).split(".")[-1].lower(), c.board_token.strip().lower())
        for c in COMPANIES
    } | dormant_tokens

    current_total = len({c.name.strip().lower() for c in COMPANIES})
    deficit = max(0, target - current_total)
    print(
        f"\n════════════════════════════════════════════════════════════\n"
        f"       GCC Job Radar — Mass ATS Expansion Engine (10K+)     \n"
        f"════════════════════════════════════════════════════════════\n"
        f"• Current Active Companies: {current_total}\n"
        f"• Target Goal:              {target}\n"
        f"• Deficit Needed:           {deficit}\n"
        f"• Concurrency Semaphore:    {concurrency}\n",
        flush=True,
    )

    if current_total >= target:
        print(f"✔ Target already achieved: {current_total} >= {target}")
        return

    limits = httpx.Limits(max_connections=concurrency * 2, max_keepalive_connections=concurrency)
    queue: asyncio.Queue[Optional[tuple[str, str, str]]] = asyncio.Queue(maxsize=2000)
    stop_event = asyncio.Event()
    lock = asyncio.Lock()

    pending_flush: list[VerifiedBoard] = []
    added_total = 0
    probed_count = 0

    async with httpx.AsyncClient(limits=limits, headers=DEFAULT_HEADERS, follow_redirects=True) as client:
        async def worker():
            nonlocal added_total, probed_count
            while not stop_event.is_set():
                item = await queue.get()
                if item is None:
                    queue.task_done()
                    break

                name, prov, slug = item
                clean_name_val = clean_company_name(name)
                n_key = clean_name_val.lower()
                tok_key = (prov.lower(), slug.lower())

                if n_key in existing_names or tok_key in existing_tokens:
                    queue.task_done()
                    continue

                res = await probe_ats_candidate(client, clean_name_val, prov, slug)

                async with lock:
                    probed_count += 1
                    if probed_count % 300 == 0:
                        print(
                            f"  [~] Probed {probed_count:,} candidate endpoints | Verified added: {added_total} "
                            f"| Registry: {current_total + added_total}/{target} | Queue: {queue.qsize():,}",
                            flush=True,
                        )

                if res and not stop_event.is_set():
                    async with lock:
                        v_n_key = clean_company_name(res.company_name).lower()
                        v_tok_key = (res.provider.lower(), res.board_token.lower())
                        if v_n_key not in existing_names and v_tok_key not in existing_tokens:
                            existing_names.add(v_n_key)
                            existing_tokens.add(v_tok_key)
                            pending_flush.append(res)
                            added_total += 1
                            total_now = current_total + added_total

                            print(
                                f"  [+] [{total_now}/{target}] {res.company_name} "
                                f"({res.provider.upper()}: {res.board_token}) -> {res.active_jobs} jobs",
                                flush=True,
                            )

                            if len(pending_flush) >= FLUSH_INTERVAL:
                                flushed = flush_boards_to_config(pending_flush, config_file)
                                print(f"  [FLUSH] Saved {flushed} verified boards to config.py (Total: {total_now})", flush=True)
                                pending_flush.clear()

                            if total_now >= target:
                                stop_event.set()

                queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(concurrency)]

        async def producer():
            # Pool 1: Direct candidates from OpenJobs / SimplifyJobs
            pool_direct = await harvest_direct_candidates(client)
            print(f"\n[*] Enqueuing {len(pool_direct)} direct ATS candidates...", flush=True)
            for c in pool_direct:
                if stop_event.is_set():
                    break
                await queue.put(c)

            # Pool 2: Y Combinator active tech companies
            if not stop_event.is_set():
                pool_yc = await harvest_yc_candidates(client)
                print(f"\n[*] Enqueuing {len(pool_yc)} YC candidates...", flush=True)
                for c in pool_yc:
                    if stop_event.is_set():
                        break
                    await queue.put(c)

            # Pool 3: 50k_companies.csv funded tech companies
            if not stop_event.is_set():
                pool_csv = harvest_csv_candidates()
                print(f"\n[*] Enqueuing {len(pool_csv)} CSV funded tech candidates...", flush=True)
                for c in pool_csv:
                    if stop_event.is_set():
                        break
                    await queue.put(c)

            # Pool 4: Permutations from 50k_clean_tech_names.txt
            if not stop_event.is_set() and names_file.exists():
                print(f"\n[*] Streaming permutations from {names_file.name}...", flush=True)
                names = [l.strip() for l in names_file.read_text(encoding="utf-8").splitlines() if l.strip()]
                providers = ["ashby", "greenhouse", "lever", "smartrecruiters"]
                for n in names:
                    if stop_event.is_set():
                        break
                    c_name = clean_company_name(n)
                    if not c_name or c_name.lower() in existing_names:
                        continue
                    slugs = generate_permutations(c_name)
                    for slug in slugs:
                        for prov in providers:
                            if stop_event.is_set():
                                break
                            if (prov, slug.lower()) in existing_tokens:
                                continue
                            await queue.put((c_name, prov, slug))

            for _ in range(concurrency):
                await queue.put(None)

        producer_task = asyncio.create_task(producer())

        while not stop_event.is_set():
            if producer_task.done():
                if producer_task.exception():
                    logger.error("Producer task failed: %s", producer_task.exception())
                if queue.empty():
                    break
            await asyncio.sleep(1.0)

        if stop_event.is_set():
            while not queue.empty():
                try:
                    queue.get_nowait()
                    queue.task_done()
                except Exception:
                    break
            producer_task.cancel()
            for _ in range(concurrency):
                await queue.put(None)

        await asyncio.gather(*workers, return_exceptions=True)

        if pending_flush:
            flushed = flush_boards_to_config(pending_flush, config_file)
            print(f"  [FLUSH] Final saved {flushed} verified boards to config.py", flush=True)
            pending_flush.clear()

    final_total = current_total + added_total
    print(
        f"\n────────────────────────────────────────────────────────────\n"
        f"✔ Expansion Complete!\n"
        f"• Total Active Companies: {final_total}\n"
        f"• Newly Added:            {added_total}\n"
        f"────────────────────────────────────────────────────────────\n",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Mass ATS Expansion Engine targeting 10,000+ verified boards.")
    parser.add_argument("--target", type=int, default=DEFAULT_TARGET, help="Target total verified companies (default: 10000)")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY, help="Worker concurrency limit (default: 100)")
    args = parser.parse_args()

    asyncio.run(run_mass_scale(target=args.target, concurrency=args.concurrency))


if __name__ == "__main__":
    main()
