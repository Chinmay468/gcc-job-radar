"""Dedicated batch company discovery, ATS probing, custom career scraper, and DB ingestion tool.

Usage:
    uv run python tools/probe_batch.py
    uv run python tools/probe_batch.py --append --ingest
    uv run python tools/probe_batch.py --file path/to/companies.txt
"""

import argparse
import asyncio
import logging
from pathlib import Path
import re
import sys
import unicodedata
from typing import Optional

import httpx

# Ensure Windows terminals handle UTF-8 cleanly
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from gcc_job_radar.clients.custom_career import CustomCareerClient
from gcc_job_radar.config import COMPANIES
from gcc_job_radar.db import init_db, record_jobs
from gcc_job_radar.filters import matches_target_title
from gcc_job_radar.models import ATSProvider, CompanyConfig, JobPosting

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("probe_batch")

CONFIG_PATH = Path(__file__).resolve().parent.parent / "gcc_job_radar" / "config.py"
DEFAULT_BATCH_FILE = Path(__file__).resolve().parent.parent / "batch_companies.txt"


from tools.discovery_utils import candidate_slugs, normalize_company_name as normalize


async def probe_ats_candidate(
    client: httpx.AsyncClient,
    company_name: str,
    slug: str,
) -> Optional[tuple[ATSProvider, str, list[JobPosting], int]]:
    """Probe Ashby, Greenhouse, Lever, SmartRecruiters for the candidate slug.
    Returns (provider, slug, matching_jobs, total_jobs) if active job board is found.
    """
    # 1. Ashby
    try:
        r = await client.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
        if r.status_code == 200:
            data = r.json()
            jobs = data.get("jobs", [])
            if isinstance(jobs, list) and len(jobs) > 0:
                matches = []
                for j in jobs:
                    title = j.get("title", "")
                    if matches_target_title(title):
                        job_url = j.get("jobUrl") or f"https://jobs.ashbyhq.com/{slug}"
                        matches.append(
                            JobPosting(
                                id=str(j.get("id", "")) or f"ashby_{slug}_{len(matches)}",
                                company=company_name,
                                title=title,
                                location=j.get("location", "") or "Remote",
                                apply_url=job_url,
                                provider=ATSProvider.ASHBY,
                            )
                        )
                return (ATSProvider.ASHBY, slug, matches, len(jobs))
    except Exception:
        pass

    # 2. Greenhouse
    try:
        r = await client.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
        if r.status_code == 200:
            data = r.json()
            jobs = data.get("jobs", [])
            if isinstance(jobs, list) and len(jobs) > 0:
                matches = []
                for j in jobs:
                    title = j.get("title", "")
                    if matches_target_title(title):
                        loc_obj = j.get("location")
                        loc_name = loc_obj.get("name", "") if isinstance(loc_obj, dict) else str(loc_obj or "")
                        apply_url = j.get("absolute_url") or f"https://boards.greenhouse.io/{slug}"
                        matches.append(
                            JobPosting(
                                id=str(j.get("id", "")) or f"gh_{slug}_{len(matches)}",
                                company=company_name,
                                title=title,
                                location=loc_name or "India",
                                apply_url=apply_url,
                                provider=ATSProvider.GREENHOUSE,
                            )
                        )
                return (ATSProvider.GREENHOUSE, slug, matches, len(jobs))
    except Exception:
        pass

    # 3. Lever
    try:
        r = await client.get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and len(data) > 0:
                matches = []
                for j in data:
                    title = j.get("text", "")
                    if matches_target_title(title):
                        cats = j.get("categories")
                        loc = cats.get("location", "") if isinstance(cats, dict) else ""
                        apply_url = j.get("hostedUrl") or f"https://jobs.lever.co/{slug}"
                        matches.append(
                            JobPosting(
                                id=str(j.get("id", "")) or f"lever_{slug}_{len(matches)}",
                                company=company_name,
                                title=title,
                                location=loc or "India",
                                apply_url=apply_url,
                                provider=ATSProvider.LEVER,
                            )
                        )
                return (ATSProvider.LEVER, slug, matches, len(data))
    except Exception:
        pass

    # 4. SmartRecruiters
    try:
        r = await client.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100")
        if r.status_code == 200:
            data = r.json()
            content = data.get("content", [])
            if isinstance(content, list) and len(content) > 0:
                matches = []
                for j in content:
                    title = j.get("name", "")
                    if matches_target_title(title):
                        loc_obj = j.get("location")
                        loc = loc_obj.get("city", "") if isinstance(loc_obj, dict) else ""
                        jid = str(j.get("id", ""))
                        apply_url = f"https://jobs.smartrecruiters.com/{slug}/{jid}" if jid else f"https://jobs.smartrecruiters.com/{slug}"
                        matches.append(
                            JobPosting(
                                id=jid or f"sr_{slug}_{len(matches)}",
                                company=company_name,
                                title=title,
                                location=loc or "India",
                                apply_url=apply_url,
                                provider=ATSProvider.SMARTRECRUITERS,
                            )
                        )
                return (ATSProvider.SMARTRECRUITERS, slug, matches, len(content))
    except Exception:
        pass

    return None


async def probe_custom_career_page(
    company_name: str,
    slug: str,
    custom_client: CustomCareerClient,
) -> Optional[tuple[str, list[JobPosting]]]:
    """If not on ATS, test standard career page URLs using CustomCareerClient."""
    candidate_urls = [
        f"https://{slug}.com/careers",
        f"https://careers.{slug}.com",
        f"https://www.{slug}.com/careers",
        f"https://{slug}.io/careers",
    ]
    for url in candidate_urls:
        try:
            cfg = CompanyConfig(name=company_name, provider=ATSProvider.CUSTOM, career_url=url)
            jobs = await custom_client.fetch_jobs(cfg)
            if jobs:
                valid = [j for j in jobs if matches_target_title(j.title)]
                if valid:
                    return (url, valid)
        except Exception:
            continue
    return None


async def process_company(
    comp: str,
    client: httpx.AsyncClient,
    custom_client: CustomCareerClient,
    existing_names: set[str],
    existing_tokens: set[str],
    semaphore: asyncio.Semaphore,
) -> dict:
    """Investigate a single company against ATS and custom career pages."""
    norm = normalize(comp)
    if norm in existing_names:
        return {"company": comp, "status": "ALREADY_MONITORED", "reason": "Already in registry"}

    slugs = candidate_slugs(comp)
    async with semaphore:
        # Step 1: Probe ATS
        for s in slugs:
            if s.lower() in existing_tokens:
                continue
            ats_res = await probe_ats_candidate(client, comp, s)
            if ats_res:
                prov, slug, matching_jobs, total_jobs = ats_res
                return {
                    "company": comp,
                    "status": "KEEP_ATS",
                    "provider": prov,
                    "token": slug,
                    "jobs": matching_jobs,
                    "total_jobs": total_jobs,
                }

        # Step 2: Probe Custom Career Page
        for s in slugs[:2]:
            custom_res = await probe_custom_career_page(comp, s, custom_client)
            if custom_res:
                career_url, matching_jobs = custom_res
                return {
                    "company": comp,
                    "status": "KEEP_CUSTOM",
                    "provider": ATSProvider.CUSTOM,
                    "career_url": career_url,
                    "jobs": matching_jobs,
                }

    return {"company": comp, "status": "SKIP_NOT_FOUND", "reason": "Not on open ATS / no direct career matches"}


def append_to_config(new_configs: list[CompanyConfig]) -> int:
    """Appends non-duplicate CompanyConfig entries to config.py."""
    if not new_configs:
        return 0
    text = CONFIG_PATH.read_text(encoding="utf-8")
    lines = []
    prov_map = {
        ATSProvider.ASHBY: "ATSProvider.ASHBY",
        ATSProvider.GREENHOUSE: "ATSProvider.GREENHOUSE",
        ATSProvider.LEVER: "ATSProvider.LEVER",
        ATSProvider.SMARTRECRUITERS: "ATSProvider.SMARTRECRUITERS",
        ATSProvider.CUSTOM: "ATSProvider.CUSTOM",
    }
    for c in new_configs:
        prov_code = prov_map.get(c.provider, f'"{c.provider}"')
        if c.career_url:
            line = f'    CompanyConfig(name="{c.name}", provider={prov_code}, board_token="{c.board_token}", career_url="{c.career_url}"),'
        else:
            line = f'    CompanyConfig(name="{c.name}", provider={prov_code}, board_token="{c.board_token}"),'
        lines.append(line)

    insertion_marker = "\n]"
    idx = text.rfind(insertion_marker)
    if idx == -1:
        print("Error: Could not locate closing bracket of COMPANIES in config.py")
        return 0

    new_text = text[:idx] + "\n" + "\n".join(lines) + text[idx:]
    CONFIG_PATH.write_text(new_text, encoding="utf-8")
    return len(lines)


async def main():
    parser = argparse.ArgumentParser(description="Probe a batch of companies, filter tech roles, and ingest/append.")
    parser.add_argument("--file", default=str(DEFAULT_BATCH_FILE), help="Path to batch companies text file")
    parser.add_argument("--append", action="store_true", help="Automatically append verified companies to config.py")
    parser.add_argument("--ingest", action="store_true", help="Automatically ingest verified active jobs into gcc_jobs.db")
    parser.add_argument("--concurrency", type=int, default=30, help="Max concurrency")
    args = parser.parse_args()

    file_path = Path(args.file)
    if not file_path.exists():
        print(f"Error: Batch file '{file_path}' does not exist.")
        sys.exit(1)

    companies = [line.strip() for line in file_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    companies = list(dict.fromkeys(companies))
    print(f"[*] Loaded {len(companies)} companies from '{file_path.name}'.")

    existing_names = {unicodedata.normalize("NFKD", c.name.strip().lower()).encode("ascii", "ignore").decode("utf-8") for c in COMPANIES}
    existing_tokens = {c.board_token.strip().lower() for c in COMPANIES if c.board_token}

    semaphore = asyncio.Semaphore(args.concurrency)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) gcc-job-radar/probe-batch/1.0"}

    print(f"[*] Scanning {len(companies)} companies across ATS boards & custom career pages...")
    async with httpx.AsyncClient(timeout=5.0, headers=headers, follow_redirects=True) as client:
        custom_client = CustomCareerClient(client=client)
        tasks = [
            process_company(comp, client, custom_client, existing_names, existing_tokens, semaphore)
            for comp in companies
        ]
        results = await asyncio.gather(*tasks)

    already_count = 0
    skipped_count = 0
    kept_ats = []
    kept_custom = []

    all_verified_jobs = []

    for r in results:
        status = r["status"]
        if status == "ALREADY_MONITORED":
            already_count += 1
        elif status.startswith("SKIP"):
            skipped_count += 1
        elif status == "KEEP_ATS":
            kept_ats.append(r)
            all_verified_jobs.extend(r["jobs"])
            prov_str = r['provider'].value if hasattr(r['provider'], 'value') else r['provider']
            tot = r.get("total_jobs", len(r["jobs"]))
            if r["jobs"]:
                print(f"  [+] KEPT ATS: {r['company']} ({prov_str}:{r['token']}) -> {len(r['jobs'])} entry-level opening(s) (out of {tot} total)")
                for j in r["jobs"]:
                    print(f"      * {j.title} | {j.location} | {j.apply_url}")
            else:
                print(f"  [+] KEPT ATS: {r['company']} ({prov_str}:{r['token']}) -> Monitored ({tot} total open positions)")
        elif status == "KEEP_CUSTOM":
            kept_custom.append(r)
            all_verified_jobs.extend(r["jobs"])
            print(f"  [+] KEPT CAREER PAGE: {r['company']} -> {r['career_url']} -> {len(r['jobs'])} entry-level opening(s)")
            for j in r["jobs"]:
                print(f"      * {j.title} | {j.location} | {j.apply_url}")

    print("\n" + "=" * 70)
    print("BATCH PROBE & FILTER SUMMARY")
    print("=" * 70)
    print(f"Total Targets:             {len(companies)}")
    print(f"Already Monitored:         {already_count}")
    print(f"Skipped / Not Found:       {skipped_count}")
    print(f"Kept ATS Boards:           {len(kept_ats)}")
    print(f"Kept Custom Career Pages:  {len(kept_custom)}")
    print(f"Total Entry-Level Roles:   {len(all_verified_jobs)}")
    print("=" * 70)

    # Ingest to DB
    if args.ingest and all_verified_jobs:
        init_db()
        record_jobs(all_verified_jobs)
        print(f"\n[*] DB Ingestion: Recorded {len(all_verified_jobs)} verified opening(s) into gcc_jobs.db!")

    # Append to config.py
    if args.append:
        new_configs = []
        for r in kept_ats:
            new_configs.append(CompanyConfig(name=r["company"], provider=r["provider"], board_token=r["token"]))
        for r in kept_custom:
            new_configs.append(
                CompanyConfig(
                    name=r["company"],
                    provider=ATSProvider.CUSTOM,
                    board_token=candidate_slugs(r["company"])[0],
                    career_url=r["career_url"],
                )
            )
        if new_configs:
            appended = append_to_config(new_configs)
            print(f"[*] Configuration: Appended {appended} new company entry/entries to config.py!")
        else:
            print("[*] Configuration: No new companies to append.")


if __name__ == "__main__":
    asyncio.run(main())
