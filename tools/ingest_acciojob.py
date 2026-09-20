"""AccioJob Early-Career Tech & Hiring Drive Ingestion Tool for gcc-job-radar.

Scrapes AccioJob portal and ingests daily digest emails/feeds for early-career
tech roles in India, applies strict stack and fresher filters, auto-registers
new hiring partners into config.COMPANIES, and persists qualified postings into gcc_jobs.db.
"""

import argparse
import asyncio
import logging
from pathlib import Path
import sys
from typing import Any, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from gcc_job_radar.clients.acciojob import (
    AccioJobClient,
    ingest_acciojob_digest_text,
    is_job_good_fit,
)
from gcc_job_radar.db import get_db_path, init_db, record_jobs
from gcc_job_radar.models import JobPosting

try:
    from rich import box
    from rich.console import Console
    from rich.table import Table

    console = Console()
    HAVE_RICH = True
except ImportError:
    HAVE_RICH = False

    class FallbackConsole:
        def print(self, *args: Any, **kwargs: Any) -> None:
            print(*args)

    console = FallbackConsole()  # type: ignore[assignment]

logger = logging.getLogger("ingest_acciojob")


def render_acciojob_table(postings: list[JobPosting]) -> None:
    """Render extracted AccioJob job postings in a Rich table."""
    if not postings:
        console.print("[yellow]No qualified early-career tech postings found.[/yellow]")
        return

    if HAVE_RICH:
        table = Table(
            title=f"AccioJob Verified Early-Career Tech Roles ({len(postings)})",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        table.add_column("Company", style="bold green", min_width=18)
        table.add_column("Title", style="bold white", min_width=25)
        table.add_column("Location", style="cyan", min_width=12)
        table.add_column("Date", style="dim", min_width=12)
        table.add_column("Apply Link", style="blue", overflow="fold")

        for p in postings:
            table.add_row(
                p.company,
                p.title,
                p.location,
                p.published_date or "Recent",
                str(p.apply_url),
            )
        console.print(table)
    else:
        for p in postings:
            console.print(f"- {p.company} | {p.title} | {p.location} | {p.apply_url}")


async def run_acciojob_pipeline(
    raw_text: Optional[str] = None,
    file_path: Optional[Path] = None,
    scrape: bool = True,
    db_path: Optional[Path] = None,
    notify: bool = False,
) -> list[JobPosting]:
    """Execute AccioJob ingestion pipeline across text, file, and web sources."""
    console.print("[bold cyan]=== ACCIOJOB EARLY-CAREER & HIRING PARTNER RADAR ===[/bold cyan]")
    init_db(db_path)

    all_postings: list[JobPosting] = []

    # 1. Ingest raw text if provided
    if raw_text:
        console.print("[*] Parsing provided AccioJob digest text...")
        text_jobs = ingest_acciojob_digest_text(raw_text)
        console.print(f"[*] Extracted [bold green]{len(text_jobs)}[/bold green] qualified role(s) from text.")
        all_postings.extend(text_jobs)

    # 2. Ingest file if provided
    if file_path and file_path.exists():
        console.print(f"[*] Reading AccioJob digest from file: {file_path}")
        content = file_path.read_text(encoding="utf-8")
        file_jobs = ingest_acciojob_digest_text(content)
        console.print(f"[*] Extracted [bold green]{len(file_jobs)}[/bold green] qualified role(s) from file.")
        all_postings.extend(file_jobs)

    # 3. Live portal scraping if requested
    if scrape:
        console.print("[*] Probing AccioJob portal (acciojob.com/jobs & placement.acciojob.com)...")
        client = AccioJobClient()
        try:
            portal_jobs = await client.fetch_portal_jobs()
            console.print(f"[*] Harvested [bold green]{len(portal_jobs)}[/bold green] live opportunity card(s) from portal.")
            all_postings.extend(portal_jobs)
        except Exception as exc:
            logger.warning("Error fetching AccioJob live portal: %s", exc)

    if not all_postings:
        console.print("[dim]No matching tech opportunities identified.[/dim]")
        return []

    # Deduplicate in-memory by (company, title)
    seen: set[tuple[str, str]] = set()
    deduped_postings: list[JobPosting] = []
    for p in all_postings:
        key = (p.company.lower().strip(), p.title.lower().strip())
        if key not in seen:
            seen.add(key)
            deduped_postings.append(p)

    console.print(f"\n[*] Total verified qualified postings: [bold green]{len(deduped_postings)}[/bold green].")
    render_acciojob_table(deduped_postings)

    # Persist into database
    record_jobs(deduped_postings, db_path=db_path)
    console.print(f"[bold green]Saved {len(deduped_postings)} postings into gcc_jobs.db.[/bold green]")

    if notify and deduped_postings:
        from gcc_job_radar.notifier import dispatch_notifications
        try:
            await dispatch_notifications(new_jobs=deduped_postings, db_path=db_path, digest=True)
        except Exception as exc:
            logger.warning("Failed to dispatch notifications: %s", exc)

    return deduped_postings


def build_parser() -> argparse.ArgumentParser:
    """Build CLI parser for tools/ingest_acciojob.py."""
    parser = argparse.ArgumentParser(
        description="Ingest AccioJob hiring partner drives and early-career postings into gcc_jobs.db."
    )
    parser.add_argument(
        "--text",
        default=None,
        help="Raw copied AccioJob digest text or table.",
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=None,
        help="Path to file containing AccioJob digest table or email HTML.",
    )
    parser.add_argument(
        "--scrape",
        action="store_true",
        default=False,
        help="Scrape live AccioJob portal pages (acciojob.com/jobs, placement.acciojob.com).",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Custom SQLite database path (default: gcc_jobs.db).",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        default=False,
        help="Send Telegram notification digest for newly qualified roles.",
    )
    return parser


def main() -> None:
    """Entrypoint for tools/ingest_acciojob.py."""
    parser = build_parser()
    args = parser.parse_args()

    # If neither --text nor --file is given and --scrape is not set, default to scrape
    do_scrape = args.scrape or (not args.text and not args.file)

    asyncio.run(
        run_acciojob_pipeline(
            raw_text=args.text,
            file_path=args.file,
            scrape=do_scrape,
            db_path=args.db,
            notify=args.notify,
        )
    )


if __name__ == "__main__":
    main()
