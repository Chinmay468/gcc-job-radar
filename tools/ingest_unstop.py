"""Unstop Early-Career Tech Ingestion Tool for gcc-job-radar.

Ingests Class of 2027 internships and entry-level tech roles in India from Unstop's
public opportunity search API, applies strict tech-role and fresher filters,
and persists verified postings into gcc_jobs.db.
"""

import argparse
import asyncio
import logging
from pathlib import Path
import re
import sys
from typing import Any, Optional

import httpx

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from gcc_job_radar.db import get_db_path, init_db, record_jobs, save_job
from gcc_job_radar.filters import (
    extract_application_dates,
    is_date_expired,
    is_tech_role,
    is_valid_get_role,
    matches_india_location,
    normalize_date_str,
    requires_experienced_candidate,
)
from gcc_job_radar.models import ATSProvider, JobPosting
from gcc_job_radar.relevance import calculate_relevance_score

try:
    from rich.console import Console
    from rich.table import Table
    from rich import box

    console = Console()
    HAVE_RICH = True
except ImportError:
    HAVE_RICH = False

    class FallbackConsole:  # type: ignore[no-redef]
        def print(self, *args: Any, **kwargs: Any) -> None:
            clean_args = [
                re.sub(r"\[/?(?:bold|green|yellow|red|cyan|magenta|dim)[^\]]*\]", "", str(a))
                for a in args
            ]
            print(*clean_args)

    console = FallbackConsole()  # type: ignore[assignment]

logger = logging.getLogger("ingest_unstop")

DEFAULT_UNSTOP_API = "https://unstop.com/api/public/opportunity/search-result"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 gcc-job-radar/0.1.0"
)

# Non-tech filters: MBA, Case competitions, quizzes, business development, mechanical
_NON_TECH_CATEGORY_PATTERN = re.compile(
    r"""(?ix)
    \b(
        mba | b-school | business\s+case | case\s+competition |
        hackathon\s+non-tech | quiz | debate | article\s+writing |
        creative\s+writing | finance | marketing | hr | sales |
        recruiter | talent(?:\s+acquisition)? | back\s+office |
        field\s+executive | customer\s+support | telecaller |
        operations(?:\s+executive)? |
        mechanical | civil | chemical | electrical |
        biotech | pharma | medical | law
    )\b
    """
)

# Tech category and discipline markers
_TECH_CATEGORY_PATTERN = re.compile(
    r"""(?ix)
    \b(
        computer\s+science | software | it | information\s+technology |
        web | app | mobile | cloud | data | artificial\s+intelligence |
        machine\s+learning | ai | ml | devops | programming | coding
    )\b
    """
)

# Class of 2027 / fresher / internship indicators
_EARLY_CAREER_PATTERN = re.compile(
    r"""(?ix)
    \b(
        intern(?:ship)? | trainee | fresher | get |
        2026 | 2027 | 2028 |
        early\s+career | campus | student | college |
        entry[- ]level | associate | junior | 0\s*(?:-|to)\s*1\s*(?:year|yr)
    )\b
    """
)


def extract_unstop_location(locations: Any) -> str:
    """Extract clean string location from Unstop locations payload."""
    if not locations:
        return "India"
    if isinstance(locations, str):
        return locations.strip() or "India"
    if isinstance(locations, list):
        names = []
        for l in locations:
            if isinstance(l, dict):
                val = l.get("name") or l.get("city") or l.get("location") or ""
                if val:
                    names.append(str(val))
            elif isinstance(l, str) and l.strip():
                names.append(l.strip())
        return ", ".join(names) if names else "India"
    return "India"


def is_qualified_unstop_item(item: dict[str, Any], require_2027: bool = False) -> tuple[bool, str]:
    """Check if an Unstop opportunity is a qualified early-career tech role in India.

    Returns:
        tuple[bool, str]: (is_qualified, rejection_reason)
    """
    if not isinstance(item, dict):
        return False, "Invalid item payload"

    # 1. Registration status check
    reg_status = str(item.get("status") or "").upper()
    regn_req = item.get("regnRequirements") or {}
    inner_reg_status = str(regn_req.get("reg_status") or "").upper()
    regn_open = item.get("regn_open")

    if reg_status in ("FINISHED", "CLOSED", "EXPIRED") or inner_reg_status in ("FINISHED", "CLOSED", "EXPIRED"):
        return False, "Registration closed/finished"
    if regn_open == 0 and inner_reg_status != "OPEN":
        return False, "Registration not open"

    end_date_val = (
        regn_req.get("end_date")
        or regn_req.get("end_time")
        or item.get("end_date")
        or item.get("regn_end_date")
    )
    if end_date_val and is_date_expired(end_date_val):
        return False, f"Registration deadline passed ({end_date_val})"

    title = str(item.get("title") or "").strip()
    if not title:
        return False, "Empty title"

    # 2. Extract company name
    org = item.get("organisation") or {}
    company_name = org.get("name") if isinstance(org, dict) else str(org)
    company_name = (company_name or "").strip() or "Unstop Employer"

    # 3. Work function, tags, filters text
    tags = item.get("tags") or []
    filters = item.get("filters") or []
    filter_names = [f.get("name", "") for f in filters if isinstance(f, dict)]
    category_text = " ".join([
        str(item.get("workfunction") or ""),
        str(item.get("subtype") or ""),
        " ".join(tags) if isinstance(tags, list) else str(tags),
        " ".join(filter_names),
        str(item.get("details") or ""),
    ]).strip()

    full_text = f"{title} {category_text}".strip()

    # 4. Exclude non-tech categories
    if _NON_TECH_CATEGORY_PATTERN.search(title):
        return False, "Non-tech title discipline (sales/marketing/mechanical/MBA)"
    if _NON_TECH_CATEGORY_PATTERN.search(category_text) and not _TECH_CATEGORY_PATTERN.search(full_text):
        return False, "Non-tech category discipline"

    # 5. Must pass is_tech_role and GET check
    if not is_tech_role(title, department=category_text):
        return False, "Does not satisfy is_tech_role pattern"
    if not is_valid_get_role(title, description=category_text):
        return False, "Non-software GET role"

    # 6. Check experience / seniority
    if requires_experienced_candidate(full_text):
        return False, "Demands 2.5+ years experienced candidate"

    # 7. Check location (must be India or remote)
    loc_str = extract_unstop_location(item.get("locations"))

    if not (matches_india_location(loc_str) or "india" in loc_str.lower() or "remote" in loc_str.lower()):
        return False, f"Non-India location: {loc_str}"

    # 8. Class of 2027 / Early Career check
    is_internship = str(item.get("type") or "").lower() == "internship" or "intern" in title.lower()
    if require_2027 or not is_internship:
        if not _EARLY_CAREER_PATTERN.search(full_text):
            return False, "Not an early-career / internship role"

    return True, "Qualified"


def build_job_posting_from_unstop(item: dict[str, Any]) -> Optional[JobPosting]:
    """Convert a qualified Unstop opportunity payload into a normalized JobPosting."""
    opp_id = str(item.get("id") or "").strip()
    if not opp_id:
        return None

    title = str(item.get("title") or "").strip()
    org = item.get("organisation") or {}
    company_name = org.get("name") if isinstance(org, dict) else str(org)
    company_name = (company_name or "").strip() or "Unstop Partner"

    # Form apply URL
    seo_url = str(item.get("seo_url") or "").strip()
    public_url = str(item.get("public_url") or "").strip()
    if seo_url.startswith("http"):
        apply_url = seo_url
    elif public_url:
        apply_url = f"https://unstop.com/{public_url.lstrip('/')}"
    else:
        apply_url = f"https://unstop.com/opportunity/{opp_id}"

    location_str = extract_unstop_location(item.get("locations"))

    is_remote = "remote" in location_str.lower() or "virtual" in location_str.lower()

    updated_at = str(item.get("updated_at") or item.get("approved_date") or "Active")

    # Description enrichment from details, jobDetail, and required_skills
    details = str(item.get("details") or "").strip()
    job_detail = item.get("jobDetail") or {}
    job_desc = str(job_detail.get("description") or "").strip() if isinstance(job_detail, dict) else ""
    req_skills = item.get("required_skills") or []
    skills_str = ", ".join([s.get("name", "") if isinstance(s, dict) else str(s) for s in req_skills if s])
    tags = item.get("tags") or []
    tags_str = ", ".join([str(t) for t in tags if t]) if isinstance(tags, list) else str(tags)

    desc_parts = [p for p in (details, job_desc, f"Skills: {skills_str}" if skills_str else "", f"Tags: {tags_str}" if tags_str else "") if p]
    full_description = "\n\n".join(desc_parts).strip()

    # Calculate relevance score using stack keywords
    score = calculate_relevance_score(title, full_description)

    regn_req = item.get("regnRequirements") or {}
    raw_start = (
        regn_req.get("start_date")
        or regn_req.get("start_time")
        or item.get("start_date")
        or item.get("regn_start_date")
        or item.get("approved_date")
    )
    raw_end = (
        regn_req.get("end_date")
        or regn_req.get("end_time")
        or item.get("end_date")
        or item.get("regn_end_date")
    )

    start_date = normalize_date_str(raw_start)
    end_date = normalize_date_str(raw_end)

    if not start_date or not end_date:
        ext_s, ext_e = extract_application_dates(full_description, updated_at)
        if not start_date:
            start_date = ext_s
        if not end_date:
            end_date = ext_e

    is_expired = bool(end_date and is_date_expired(end_date))

    try:
        return JobPosting(
            id=f"unstop_{opp_id}",
            company=company_name,
            title=title,
            location=location_str,
            apply_url=apply_url,
            published_date=updated_at,
            provider=ATSProvider.UNSTOP,
            is_remote=is_remote,
            description=full_description or None,
            notes=full_description[:500] if full_description else None,
            relevance_score=score,
            status="EXPIRED" if is_expired else "NEW",
            application_start_date=start_date,
            application_end_date=end_date,
            is_expired=is_expired,
        )
    except Exception as exc:
        logger.debug("Error building JobPosting for Unstop item %s: %s", opp_id, exc)
        return None


async def fetch_unstop_page(
    client: httpx.AsyncClient,
    opportunity_type: str = "jobs",
    search_term: Optional[str] = None,
    page: int = 1,
    url: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Fetch a single page of opportunities from Unstop public API."""
    target_url = url or DEFAULT_UNSTOP_API
    params: dict[str, Any] = {
        "opportunity": opportunity_type,
        "page": page,
    }
    if search_term:
        params["searchTerm"] = search_term

    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "application/json",
    }

    try:
        resp = await client.get(target_url, params=params, headers=headers, timeout=12.0)
        if resp.status_code != 200:
            logger.warning("Unstop API HTTP %d for %s (page %d)", resp.status_code, opportunity_type, page)
            return []

        data = resp.json()
        if not isinstance(data, dict):
            return []

        data_field = data.get("data", {})
        if isinstance(data_field, dict):
            items = data_field.get("data", [])
            return items if isinstance(items, list) else []
        elif isinstance(data_field, list):
            return data_field

    except Exception as exc:
        logger.warning("Error querying Unstop API: %s", exc)

    return []


async def run_unstop_ingestion(
    opportunities: list[str] = ["jobs", "internships"],
    search_terms: list[str] = ["software", "intern", "developer", "engineering"],
    pages_per_term: int = 2,
    dry_run: bool = False,
    db_path: Optional[Path] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> list[JobPosting]:
    """Orchestrate multi-query Unstop early-career tech ingestion."""
    init_db(db_path)
    target_db = get_db_path(db_path)

    console.print("[bold cyan]=== UNSTOP EARLY-CAREER TECH INGESTION ENGINE ===[/bold cyan]")
    console.print(f"• [bold white]Opportunities:[/bold white] {', '.join(opportunities)}")
    console.print(f"• [bold white]Search Terms:[/bold white] {', '.join(search_terms)}")
    console.print(f"• [bold white]Pages per term:[/bold white] {pages_per_term}")
    console.print(f"• [bold white]Mode:[/bold white] {'DRY RUN (Preview Only)' if dry_run else 'PERSIST TO DB'}\n")

    owns_client = False
    if client is None:
        client = httpx.AsyncClient(headers={"User-Agent": DEFAULT_USER_AGENT}, timeout=15.0)
        owns_client = True

    qualified_postings: list[JobPosting] = []
    seen_ids: set[str] = set()

    try:
        for opp in opportunities:
            for term in search_terms:
                for page in range(1, pages_per_term + 1):
                    raw_items = await fetch_unstop_page(
                        client=client,
                        opportunity_type=opp,
                        search_term=term,
                        page=page,
                    )

                    for item in raw_items:
                        item_id = str(item.get("id") or "")
                        if not item_id or item_id in seen_ids:
                            continue

                        is_qual, reason = is_qualified_unstop_item(item)
                        if is_qual:
                            posting = build_job_posting_from_unstop(item)
                            if posting:
                                seen_ids.add(item_id)
                                qualified_postings.append(posting)

        # Display results table
        table = Table(
            title=f"Discovered Unstop Tech Roles ({len(qualified_postings)} qualified)",
            show_header=True,
            header_style="bold cyan",
            box=box.ASCII if not HAVE_RICH else box.ROUNDED,
        )
        table.add_column("#", justify="right", width=4)
        table.add_column("Company", style="bold white", width=24)
        table.add_column("Title", style="green", width=36)
        table.add_column("Location", style="yellow", width=18)
        table.add_column("Apply URL", style="cyan", width=40)

        for idx, p in enumerate(qualified_postings, start=1):
            table.add_row(
                str(idx),
                p.company[:22],
                p.title[:34],
                p.location[:16],
                str(p.apply_url)[:38],
            )

        console.print(table)
        console.print()

        if dry_run:
            console.print(
                f"[bold yellow][*] DRY RUN COMPLETE:[/bold yellow] Found "
                f"[bold white]{len(qualified_postings)}[/bold white] qualified roles. (0 written to DB)"
            )
        else:
            if qualified_postings:
                record_jobs(qualified_postings, db_path=target_db)
                console.print(
                    f"[bold green][+] INGESTION COMPLETE:[/bold green] Recorded "
                    f"[bold white]{len(qualified_postings)}[/bold white] verified Unstop tech roles into [cyan]{target_db.name}[/cyan]!"
                )
            else:
                console.print("[yellow]No new qualified roles found in this cycle.[/yellow]")

        return qualified_postings

    finally:
        if owns_client:
            await client.aclose()


def main() -> None:
    """CLI entrypoint for tools/ingest_unstop.py."""
    parser = argparse.ArgumentParser(
        description="Ingest Class of 2027 Indian tech & early-career roles from Unstop API."
    )
    parser.add_argument(
        "--opportunity",
        type=str,
        default="jobs,internships",
        help="Comma-separated opportunity types: 'jobs', 'internships' (default: jobs,internships)",
    )
    parser.add_argument(
        "--search-terms",
        type=str,
        default="software,intern,developer,engineering",
        help="Comma-separated search keywords (default: software,intern,developer,engineering)",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=2,
        help="Pages to query per keyword (default: 2)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Preview qualified postings without recording to gcc_jobs.db",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Custom path to SQLite database",
    )

    args = parser.parse_args()

    opp_list = [o.strip() for o in args.opportunity.split(",") if o.strip()]
    terms_list = [t.strip() for t in args.search_terms.split(",") if t.strip()]

    asyncio.run(
        run_unstop_ingestion(
            opportunities=opp_list,
            search_terms=terms_list,
            pages_per_term=args.pages,
            dry_run=args.dry_run,
            db_path=args.db_path,
        )
    )


if __name__ == "__main__":
    main()
