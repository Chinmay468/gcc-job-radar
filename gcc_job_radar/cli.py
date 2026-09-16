"""CLI entrypoint and command interface for GCC Job Radar."""

import asyncio
import csv
import json
import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
import typer
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

# Automatically load environment variables from .env if present
load_dotenv()

from gcc_job_radar import __version__
from gcc_job_radar.config import COMPANIES
from gcc_job_radar.db import (
    filter_new_jobs,
    filter_unalerted_jobs,
    get_applied_and_dismissed_companies,
    get_dormant_companies_entries,
    get_dormant_company_names,
    get_job_by_id,
    get_stale_applications,
    get_stats,
    init_db,
    make_job_key,
    mark_job_status,
    query_jobs,
    reactivate_company,
    record_companies_scan_activity,
    record_jobs,
)
from gcc_job_radar.display import (
    console,
    render_banner,
    render_dormant_companies,
    render_results,
    render_stale_applications,
    render_stats,
)
from gcc_job_radar.scanner import scan_all_companies
from gcc_job_radar.filters import is_remote_opening
from gcc_job_radar.models import ATSProvider, JobPosting
from gcc_job_radar.notifier import dispatch_notifications
from gcc_job_radar.relevance import score_job_posting

app = typer.Typer(
    name="gcc-job-radar",
    help="CLI tool to fetch verified entry-level tech roles in India from non-Indian tech companies & GCCs.",
    add_completion=False,
)


def export_json(jobs: list[JobPosting], path: Path) -> None:
    """Export matching job postings to a JSON file."""
    data = [job.model_dump(mode="json") for job in jobs]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    console.print(f"[bold green][+][/bold green] Exported {len(jobs)} postings to JSON: [cyan]{path}[/cyan]")


def export_csv(jobs: list[JobPosting], path: Path) -> None:
    """Export matching job postings to a CSV file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "company",
                "title",
                "location",
                "apply_url",
                "published_date",
                "provider",
                "is_remote",
                "relevance_score",
                "id",
                "status",
                "notes",
                "direct_search_url",
            ],
        )
        writer.writeheader()
        for job in jobs:
            writer.writerow(
                {
                    "company": job.company,
                    "title": job.title,
                    "location": job.location,
                    "apply_url": str(job.apply_url),
                    "published_date": job.published_date or "Active",
                    "provider": job.provider.value,
                    "is_remote": job.is_remote,
                    "relevance_score": getattr(job, "relevance_score", 0) or 0,
                    "id": getattr(job, "numeric_id", None) or job.id,
                    "status": getattr(job, "status", "NEW"),
                    "notes": getattr(job, "notes", None) or "",
                    "direct_search_url": getattr(job, "direct_search_url", None) or "",
                }
            )
    console.print(f"[bold green][+][/bold green] Exported {len(jobs)} postings to CSV: [cyan]{path}[/cyan]")


@app.command("scan")
def scan(
    company: Optional[str] = typer.Option(
        None,
        "--company",
        "-c",
        help="Filter scan to a specific company by name or board token.",
    ),
    provider: Optional[str] = typer.Option(
        None,
        "--provider",
        "-p",
        help="Filter scan by ATS provider (e.g., greenhouse, ashby, lever, workday, smartrecruiters).",
    ),
    concurrency: int = typer.Option(
        30,
        "--concurrency",
        help="Maximum concurrent HTTP requests to ATS endpoints.",
    ),
    remote_only: bool = typer.Option(
        False,
        "--remote-only",
        "--remote",
        "-r",
        help="Only display and export 100% remote roles eligible in India.",
    ),
    new_only: bool = typer.Option(
        False,
        "--new-only",
        "-n",
        help="Only display and export postings not seen in previous runs.",
    ),
    show_all: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="Include roles from already applied and dismissed companies.",
    ),
    min_score: int = typer.Option(
        0,
        "--min-score",
        "-m",
        help="Filter out jobs with relevance score below this threshold (0-100).",
    ),
    digest: bool = typer.Option(
        False,
        "--digest",
        help="Batch alerts into a single consolidated daily digest message.",
    ),
    list_dormant: bool = typer.Option(
        False,
        "--list-dormant",
        help="Display registered dormant / paused companies and exit.",
    ),
    reactivate_comp: Optional[str] = typer.Option(
        None,
        "--reactivate",
        help="Reactivate a dormant company so it is monitored in scans.",
    ),
    auto_dormant_threshold: int = typer.Option(
        5,
        "--auto-dormant-threshold",
        help="Number of consecutive scans with 0 matches before auto-marking a company dormant (0 to disable).",
    ),
    stats: bool = typer.Option(
        False,
        "--stats",
        help="Display historical database statistics and exit.",
    ),
    db_path: Optional[Path] = typer.Option(
        None,
        "--db",
        help="Custom path to SQLite database file.",
    ),
    notify_discord: Optional[str] = typer.Option(
        None,
        "--notify-discord",
        help="Discord webhook URL to alert on newly detected postings.",
    ),
    notify_telegram_token: Optional[str] = typer.Option(
        None,
        "--notify-telegram-token",
        help="Telegram bot token to alert on newly detected postings.",
    ),
    notify_telegram_chat: Optional[str] = typer.Option(
        None,
        "--notify-telegram-chat",
        help="Telegram chat ID or channel to alert on newly detected postings.",
    ),
    json_path: Optional[Path] = typer.Option(
        None,
        "--json",
        "-j",
        help="Path to export results to a JSON file.",
    ),
    csv_path: Optional[Path] = typer.Option(
        None,
        "--csv",
        help="Path to export results to a CSV file.",
    ),
) -> None:
    """Scan canonical ATS endpoints for verified entry-level tech roles in India."""
    if stats:
        db_statistics = get_stats(db_path)
        render_stats(db_statistics)
        raise typer.Exit()

    # Guard against direct Python function invocations passing OptionInfo defaults
    if not isinstance(provider, str):
        provider = None
    if not isinstance(company, str):
        company = None
    if not isinstance(concurrency, int):
        concurrency = 30
    if not isinstance(remote_only, bool):
        remote_only = False
    if not isinstance(min_score, int):
        min_score = 0
    if not isinstance(digest, bool):
        digest = False
    if not isinstance(show_all, bool):
        show_all = False
    if not isinstance(auto_dormant_threshold, int):
        auto_dormant_threshold = 5

    init_db(db_path)

    if list_dormant:
        dormant_entries = get_dormant_companies_entries(db_path)
        render_dormant_companies(dormant_entries)
        raise typer.Exit()

    if reactivate_comp:
        reactivate_company(reactivate_comp, db_path)
        console.print(f"[bold green][+][/bold green] Reactivated company [cyan]{reactivate_comp}[/cyan].")

    target_companies = COMPANIES
    dormant_names = get_dormant_company_names(db_path)
    if not company:
        target_companies = [c for c in target_companies if c.name.lower().strip() not in dormant_names]
    if provider:
        provider_norm = provider.strip().lower()
        target_companies = [
            c
            for c in target_companies
            if c.provider.value.lower() == provider_norm
            or c.provider.name.lower() == provider_norm
        ]
        if not target_companies:
            console.print(
                f"[bold red]Error:[/bold red] No companies found matching ATS provider [yellow]'{provider}'[/yellow]."
            )
            valid_providers = ", ".join(p.value for p in ATSProvider)
            console.print(f"[dim]Available providers: {valid_providers}[/dim]")
            raise typer.Exit(code=1)

    if company:
        query = company.strip().lower()
        target_companies = [
            c for c in target_companies if query in c.name.lower() or query in c.board_token.lower()
        ]
        if not target_companies:
            console.print(
                f"[bold red]Error:[/bold red] Company matching [yellow]'{company}'[/yellow] not found in registry."
            )
            console.print(
                f"[dim]Available companies: {', '.join([c.name for c in COMPANIES])}[/dim]"
            )
            raise typer.Exit(code=1)

    render_banner(len(target_companies), provider=provider)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task("Scanning ATS endpoints...", total=len(target_companies))

        def on_progress(comp_name: str, current: int, total: int) -> None:
            progress.update(
                task_id,
                completed=current,
                description=f"Scanning [cyan]{comp_name}[/cyan] ({current}/{total})...",
            )

        all_jobs = asyncio.run(
            scan_all_companies(
                companies=target_companies,
                concurrency=concurrency,
                on_progress=on_progress,
            )
        )
        progress.update(task_id, description="[bold green]Scan completed![/bold green]")

    if remote_only:
        all_jobs = [j for j in all_jobs if getattr(j, "is_remote", False) or is_remote_opening(j)]

    for j in all_jobs:
        score_job_posting(j)

    if min_score > 0:
        all_jobs = [j for j in all_jobs if (getattr(j, "relevance_score", 0) or 0) >= min_score]

    all_jobs.sort(key=lambda j: (getattr(j, "relevance_score", 0) or 0), reverse=True)

    company_match_counts: dict[str, int] = {}
    for j in all_jobs:
        cname = j.company.lower().strip()
        company_match_counts[cname] = company_match_counts.get(cname, 0) + 1

    batch_counts = [
        (c.name, company_match_counts.get(c.name.lower().strip(), 0))
        for c in target_companies
    ]
    record_companies_scan_activity(batch_counts, auto_dormant_threshold=auto_dormant_threshold, db_path=db_path)

    new_jobs, existing_jobs = filter_new_jobs(all_jobs, db_path)

    # Persist all current active jobs (attaches rowid, numeric_id, status, notes to all_jobs)
    record_jobs(all_jobs, db_path)

    # Check for unalerted active postings (handles new jobs as well as retrying any failed prior dispatches)
    unalerted_jobs = []
    if notify_discord or os.getenv("DISCORD_WEBHOOK_URL"):
        unalerted_jobs.extend(filter_unalerted_jobs(all_jobs, "discord", db_path))
    if (notify_telegram_token and notify_telegram_chat) or (
        os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID")
    ):
        unalerted_jobs.extend(filter_unalerted_jobs(all_jobs, "telegram", db_path))

    jobs_to_notify_map = {make_job_key(j): j for j in (new_jobs + unalerted_jobs)}
    jobs_to_notify = list(jobs_to_notify_map.values())

    # Dispatch notifications if new or unalerted postings exist
    if jobs_to_notify:
        asyncio.run(
            dispatch_notifications(
                new_jobs=jobs_to_notify,
                discord_webhook=notify_discord,
                telegram_token=notify_telegram_token,
                telegram_chat_id=notify_telegram_chat,
                db_path=db_path,
                digest=digest,
            )
        )

    if show_all:
        display_jobs = all_jobs
        display_new_jobs = new_jobs
    else:
        # Default output displays only unapplied (status = 'NEW') jobs
        unapplied_jobs = [j for j in all_jobs if getattr(j, "status", "NEW").upper() == "NEW"]
        unapplied_new_jobs = [j for j in new_jobs if getattr(j, "status", "NEW").upper() == "NEW"]
        if not company:
            applied_comps, dismissed_comps = get_applied_and_dismissed_companies(db_path)
            excluded_comps = applied_comps | dismissed_comps
            unapplied_jobs = [j for j in unapplied_jobs if j.company.lower().strip() not in excluded_comps]
            unapplied_new_jobs = [j for j in unapplied_new_jobs if j.company.lower().strip() not in excluded_comps]
        display_jobs = unapplied_jobs
        display_new_jobs = unapplied_new_jobs

    if new_only:
        render_results(display_new_jobs, is_new_only=True)
        jobs_to_export = display_new_jobs
    else:
        render_results(display_jobs, is_new_only=False)
        jobs_to_export = display_jobs

    if json_path:
        export_json(jobs_to_export, json_path)
    if csv_path:
        export_csv(jobs_to_export, csv_path)


@app.command("list")
def list_jobs(
    company: Optional[str] = typer.Option(
        None,
        "--company",
        "-c",
        help="Filter listed jobs by company name.",
    ),
    title: Optional[str] = typer.Option(
        None,
        "--title",
        "-t",
        help="Filter listed jobs by title keyword.",
    ),
    status: str = typer.Option(
        "NEW",
        "--status",
        "-s",
        help="Filter listed jobs by status ('NEW', 'NEEDS_RESOLVE', 'APPLIED', 'INTERVIEWING', 'REJECTED', 'DISMISSED', or 'ALL'). [default: NEW]",
    ),
    remote_only: bool = typer.Option(
        False,
        "--remote-only",
        "--remote",
        "-r",
        help="Only list 100% remote roles eligible in India.",
    ),
    min_score: int = typer.Option(
        0,
        "--min-score",
        "-m",
        help="Filter listed jobs by minimum relevance score (0-100).",
    ),
    stale: bool = typer.Option(
        False,
        "--stale",
        help="List stale applications awaiting follow-up (applied >= 7 days ago).",
    ),
    stale_days: int = typer.Option(
        7,
        "--stale-days",
        help="Threshold in days for stale applications follow-up.",
    ),
    limit: int = typer.Option(
        25,
        "--limit",
        "-l",
        help="Maximum number of jobs to list.",
    ),
    db_path: Optional[Path] = typer.Option(
        None,
        "--db",
        help="Custom path to SQLite database file.",
    ),
    json_path: Optional[Path] = typer.Option(
        None,
        "--json",
        "-j",
        help="Path to export results to a JSON file.",
    ),
    csv_path: Optional[Path] = typer.Option(
        None,
        "--csv",
        help="Path to export results to a CSV file.",
    ),
) -> None:
    """List tracked entry-level jobs from the local database."""
    init_db(db_path)
    if not isinstance(remote_only, bool):
        remote_only = False
    if not isinstance(min_score, int):
        min_score = 0
    if not isinstance(stale_days, int):
        stale_days = 7

    if stale:
        stale_results = get_stale_applications(days=stale_days, db_path=db_path)
        render_stale_applications(stale_results, days=stale_days)
        return

    jobs_dict = query_jobs(
        company=company,
        title_keyword=title,
        is_remote=True if remote_only else None,
        status=status,
        min_score=min_score if min_score > 0 else None,
        limit=limit,
        db_path=db_path,
    )
    job_postings: list[JobPosting] = []
    for jd in jobs_dict:
        try:
            job_postings.append(
                JobPosting(
                    id=jd["id"],
                    numeric_id=jd.get("numeric_id"),
                    company=jd["company"],
                    title=jd["title"],
                    location=jd["location"],
                    apply_url=jd["apply_url"],
                    published_date=jd.get("published_date") or "Active",
                    provider=ATSProvider(jd["provider"]),
                    is_remote=bool(jd.get("is_remote", False)),
                    status=jd.get("status", "NEW"),
                    applied_at=jd.get("applied_at"),
                    notes=jd.get("notes"),
                    direct_search_url=jd.get("direct_search_url"),
                    relevance_score=jd.get("relevance_score", 0) or 0,
                )
            )
        except Exception:
            continue

    job_postings.sort(key=lambda j: (getattr(j, "relevance_score", 0) or 0), reverse=True)

    if remote_only:
        job_postings = [j for j in job_postings if getattr(j, "is_remote", False) or is_remote_opening(j)]

    render_results(job_postings, is_new_only=False)

    if json_path:
        export_json(job_postings, json_path)
    if csv_path:
        export_csv(job_postings, csv_path)


@app.command("stale")
def stale_command(
    days: int = typer.Option(
        7,
        "--days",
        "-d",
        help="Number of days since applied without status update to consider stale.",
    ),
    db_path: Optional[Path] = typer.Option(
        None,
        "--db",
        help="Custom path to SQLite database file.",
    ),
    json_path: Optional[Path] = typer.Option(
        None,
        "--json",
        "-j",
        help="Path to export results to a JSON file.",
    ),
    csv_path: Optional[Path] = typer.Option(
        None,
        "--csv",
        help="Path to export results to a CSV file.",
    ),
) -> None:
    """List applications pending follow-up (applied >= N days ago)."""
    init_db(db_path)
    if not isinstance(days, int):
        days = 7

    stale_results = get_stale_applications(days=days, db_path=db_path)
    render_stale_applications(stale_results, days=days)

    if json_path or csv_path:
        stale_postings: list[JobPosting] = []
        for jd in stale_results:
            try:
                stale_postings.append(
                    JobPosting(
                        id=jd["id"],
                        numeric_id=jd.get("numeric_id"),
                        company=jd["company"],
                        title=jd["title"],
                        location=jd["location"],
                        apply_url=jd["apply_url"],
                        published_date="Applied",
                        provider=ATSProvider(jd.get("provider", "greenhouse")) if jd.get("provider") in [p.value for p in ATSProvider] else ATSProvider.GREENHOUSE,
                        is_remote=bool(jd.get("is_remote", False)),
                        status=jd.get("status", "APPLIED"),
                        applied_at=jd.get("applied_at"),
                        notes=jd.get("notes"),
                        direct_search_url=jd.get("direct_search_url"),
                        relevance_score=jd.get("relevance_score", 0) or 0,
                    )
                )
            except Exception:
                continue
        if json_path:
            export_json(stale_postings, json_path)
        if csv_path:
            export_csv(stale_postings, csv_path)


@app.command("dormant")
def dormant_command(
    db_path: Optional[Path] = typer.Option(
        None,
        "--db",
        help="Custom path to SQLite database file.",
    ),
) -> None:
    """List registered dormant / paused companies."""
    init_db(db_path)
    entries = get_dormant_companies_entries(db_path)
    render_dormant_companies(entries)


@app.command("reactivate")
def reactivate_command(
    company: str = typer.Argument(
        ...,
        help="Company name to reactivate from dormant registry.",
    ),
    db_path: Optional[Path] = typer.Option(
        None,
        "--db",
        help="Custom path to SQLite database file.",
    ),
) -> None:
    """Reactivate a dormant company to resume monitoring."""
    init_db(db_path)
    reactivate_company(company, db_path)
    console.print(f"[bold green][+][/bold green] Reactivated company [cyan]{company}[/cyan] in scanning registry.")


@app.command("ask")
def ask_command(
    question: str = typer.Argument(
        ...,
        help="Question to ask the AI agent about tracked jobs, stats, companies, or recommendations.",
    ),
    db_path: Optional[Path] = typer.Option(
        None,
        "--db",
        help="Custom path to SQLite database file.",
    ),
) -> None:
    """Ask the AI agent about jobs, stats, companies, or career recommendations."""
    init_db(db_path)
    from gcc_job_radar.ai_agent import ask_ai_agent
    from rich.markdown import Markdown

    with console.status("[bold cyan]Consulting GCC Job Radar AI Agent...[/bold cyan]"):
        answer = asyncio.run(ask_ai_agent(prompt=question, chat_id="cli", db_path=db_path, as_markdown=True))

    console.print()
    renderable = (
        Markdown(answer)
        if not (answer.startswith("<") or answer.startswith("ℹ️ <b>") or answer.startswith("🚀 <b>") or answer.startswith("📊 <b>"))
        else answer
    )
    console.print(
        Panel(
            renderable,
            title="[bold cyan]GCC Job Radar • AI Career Agent[/bold cyan]",
            border_style="cyan",
            padding=(1, 2),
        )
    )


@app.command("apply")
def apply_job(
    job_id: str = typer.Argument(
        ...,
        help="Job ID to mark as APPLIED (numeric ID from table or unique job key).",
    ),
    notes: Optional[str] = typer.Option(
        None,
        "--notes",
        "-n",
        help="Optional notes for this application (e.g. referral, recruiter contact, date).",
    ),
    fallback: bool = typer.Option(
        False,
        "--fallback",
        "-f",
        help="Open fallback direct ATS search query in browser.",
    ),
    db_path: Optional[Path] = typer.Option(
        None,
        "--db",
        help="Custom path to SQLite database file.",
    ),
) -> None:
    """Mark a job as APPLIED with an automatic timestamp and optional notes."""
    init_db(db_path)
    job = get_job_by_id(job_id, db_path=db_path)
    if not job:
        console.print(f"[bold red]Error:[/bold red] Job with ID [yellow]'{job_id}'[/yellow] not found in database.")
        raise typer.Exit(code=1)

    if fallback:
        import webbrowser
        from gcc_job_radar.link_resolver import build_direct_search_url

        target_url = job.get("direct_search_url") or build_direct_search_url(job["company"], job["title"])
        try:
            webbrowser.open(str(target_url))
        except Exception:
            pass
        fallback_note = f"Opened direct ATS search: {target_url}"
        notes = f"{notes} | {fallback_note}" if notes else fallback_note

    mark_job_status(job_id=job_id, status="APPLIED", notes=notes, db_path=db_path)
    disp_id = job.get("numeric_id") or job_id
    notes_msg = f" (Notes: [dim]{notes}[/dim])" if notes else ""
    console.print(
        f"[bold green][+][/bold green] Marked job [bold cyan]#{disp_id}[/bold cyan] "
        f"([bold white]{job['company']}[/bold white] - [cyan]{job['title']}[/cyan]) as [bold green]APPLIED[/bold green].{notes_msg}"
    )


@app.command("dismiss")
def dismiss_job(
    job_id: str = typer.Argument(
        ...,
        help="Job ID to dismiss (numeric ID from table or unique job key).",
    ),
    db_path: Optional[Path] = typer.Option(
        None,
        "--db",
        help="Custom path to SQLite database file.",
    ),
) -> None:
    """Mark a job as DISMISSED so it will no longer appear in scans or default listings."""
    init_db(db_path)
    job = get_job_by_id(job_id, db_path=db_path)
    if not job:
        console.print(f"[bold red]Error:[/bold red] Job with ID [yellow]'{job_id}'[/yellow] not found in database.")
        raise typer.Exit(code=1)

    mark_job_status(job_id=job_id, status="DISMISSED", db_path=db_path)
    disp_id = job.get("numeric_id") or job_id
    console.print(
        f"[bold yellow][-][/bold yellow] Marked job [bold cyan]#{disp_id}[/bold cyan] "
        f"([bold white]{job['company']}[/bold white] - [cyan]{job['title']}[/cyan]) as [bold yellow]DISMISSED[/bold yellow]. "
        "It will no longer appear in scans or default listings."
    )


@app.command("hide", hidden=True)
def hide_job(
    job_id: str = typer.Argument(..., help="Job ID to hide."),
    db_path: Optional[Path] = typer.Option(
        None,
        "--db",
        help="Custom path to SQLite database file.",
    ),
) -> None:
    """Alias for dismiss command."""
    dismiss_job(job_id=job_id, db_path=db_path)


@app.command("restore")
def restore_job(
    job_id: str = typer.Argument(
        ...,
        help="Job ID to restore back to NEW (numeric ID from table or unique job key).",
    ),
    db_path: Optional[Path] = typer.Option(
        None,
        "--db",
        help="Custom path to SQLite database file.",
    ),
) -> None:
    """Restore a dismissed or applied job back to NEW status."""
    init_db(db_path)
    job = get_job_by_id(job_id, db_path=db_path)
    if not job:
        console.print(f"[bold red]Error:[/bold red] Job with ID [yellow]'{job_id}'[/yellow] not found in database.")
        raise typer.Exit(code=1)

    mark_job_status(job_id=job_id, status="NEW", db_path=db_path)
    disp_id = job.get("numeric_id") or job_id
    console.print(
        f"[bold green][+][/bold green] Restored job [bold cyan]#{disp_id}[/bold cyan] "
        f"([bold white]{job['company']}[/bold white] - [cyan]{job['title']}[/cyan]) to [bold green]NEW[/bold green]. "
        "It will appear in scans and default listings again."
    )


@app.command("undismiss")
def undismiss_job(
    job_id: str = typer.Argument(..., help="Job ID to undismiss (revert back to NEW)."),
    db_path: Optional[Path] = typer.Option(
        None,
        "--db",
        help="Custom path to SQLite database file.",
    ),
) -> None:
    """Alias for restore command: restore a dismissed job back to NEW status."""
    restore_job(job_id=job_id, db_path=db_path)


@app.command("bot")
def bot_command(
    token: Optional[str] = typer.Option(
        None,
        "--token",
        "-t",
        help="Telegram bot token (or set TELEGRAM_BOT_TOKEN env var).",
    ),
    chat_id: Optional[str] = typer.Option(
        None,
        "--chat-id",
        "-c",
        help="Authorized Telegram chat ID (or set TELEGRAM_CHAT_ID env var).",
    ),
    db_path: Optional[Path] = typer.Option(
        None,
        "--db",
        help="Custom path to SQLite database file.",
    ),
) -> None:
    """Start interactive Telegram bot listener for scans and job alerts."""
    import os
    from gcc_job_radar.bot_listener import run_bot_listener

    bot_token = token or os.getenv("TELEGRAM_BOT_TOKEN")
    allowed_chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")

    if not bot_token:
        console.print("[bold red]Error:[/bold red] Missing Telegram bot token. Pass `--token` or set `TELEGRAM_BOT_TOKEN`.")
        raise typer.Exit(code=1)

    if not allowed_chat_id:
        console.print("[bold red]Error:[/bold red] Missing authorized Telegram chat ID. Pass `--chat-id` or set `TELEGRAM_CHAT_ID`.")
        raise typer.Exit(code=1)

    try:
        asyncio.run(run_bot_listener(bot_token=bot_token, allowed_chat_id=str(allowed_chat_id), db_path=db_path))
    except KeyboardInterrupt:
        console.print("\n[yellow]Telegram bot listener stopped.[/yellow]")


@app.command("sync-mail")
def sync_mail(
    limit: int = typer.Option(
        10,
        "--limit",
        "-l",
        help="Maximum unread alert emails to process (default: 10).",
    ),
    days: int = typer.Option(
        7,
        "--days",
        "-d",
        help="Search emails received in the last N days (default: 7). Use 0 for all time.",
    ),
    mark_read: bool = typer.Option(
        False,
        "--mark-read",
        help="Mark processed alert emails as READ in inbox (default: keep unread).",
    ),
    folder: str = typer.Option(
        "INBOX",
        "--folder",
        "-f",
        help="IMAP mailbox folder to inspect (default: INBOX).",
    ),
    server: Optional[str] = typer.Option(
        None,
        "--server",
        help="IMAP server hostname (default: from EMAIL_IMAP_SERVER or imap.gmail.com).",
    ),
    user: Optional[str] = typer.Option(
        None,
        "--user",
        "-u",
        help="IMAP username / email address (or set EMAIL_USER in .env).",
    ),
    password: Optional[str] = typer.Option(
        None,
        "--password",
        help="IMAP password / Google App Password (or set EMAIL_PASSWORD in .env).",
    ),
    all_emails: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="Process recent alert emails even if already read (default: unread only).",
    ),
    query: Optional[str] = typer.Option(
        None,
        "--query",
        "-q",
        help="Custom IMAP search query (overrides default alert sender search).",
    ),
    db_path: Optional[Path] = typer.Option(
        None,
        "--db",
        help="Custom path to SQLite database file.",
    ),
    notify_discord: Optional[str] = typer.Option(
        None,
        "--notify-discord",
        help="Discord webhook URL to alert on newly detected postings.",
    ),
    notify_telegram_token: Optional[str] = typer.Option(
        None,
        "--notify-telegram-token",
        help="Telegram bot token to alert on newly detected postings.",
    ),
    notify_telegram_chat: Optional[str] = typer.Option(
        None,
        "--notify-telegram-chat",
        help="Telegram chat ID or channel to alert on newly detected postings.",
    ),
) -> None:
    """Ingest job alert emails (LinkedIn, Naukri, Indeed) directly from inbox via IMAP SSL."""
    import sys
    root_dir = Path(__file__).resolve().parent.parent
    if str(root_dir) not in sys.path:
        sys.path.insert(0, str(root_dir))

    from tools.ingest_email import (
        DEFAULT_IMAP_SERVER,
        MissingEmailCredentialsError,
        sync_email_alerts,
    )

    imap_server = server or DEFAULT_IMAP_SERVER
    try:
        jobs = sync_email_alerts(
            server=imap_server,
            user=user,
            password=password,
            folder=folder,
            limit=limit,
            days=days,
            mark_read=mark_read,
            unread_only=not all_emails,
            search_query=query,
            db_path=db_path,
        )

        # Dispatch notifications for newly detected postings (Discord / Telegram)
        new_postings = [j for j in jobs if getattr(j, "status", "NEW").upper() == "NEW"]
        if new_postings:
            asyncio.run(
                dispatch_notifications(
                    new_jobs=new_postings,
                    discord_webhook=notify_discord,
                    telegram_token=notify_telegram_token,
                    telegram_chat_id=notify_telegram_chat,
                    db_path=db_path,
                )
            )

        render_results(jobs, is_new_only=False)
    except MissingEmailCredentialsError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1)
    except Exception as exc:
        console.print(f"[bold red]Email sync error:[/bold red] {exc}")
        raise typer.Exit(code=1)


@app.command("check-links")
def check_links_command(
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Check links and report dead postings without updating database status.",
    ),
    concurrency: int = typer.Option(
        25,
        "--concurrency",
        "-c",
        help="Maximum concurrent HTTP requests to verify links (default: 25).",
    ),
    limit: Optional[int] = typer.Option(
        None,
        "--limit",
        "-l",
        help="Maximum number of unapplied jobs to verify (default: all).",
    ),
    db_path: Optional[Path] = typer.Option(
        None,
        "--db",
        help="Custom path to SQLite database file.",
    ),
) -> None:
    """Validate URLs of active unapplied jobs and auto-dismiss broken/expired postings."""
    import sys
    root_dir = Path(__file__).resolve().parent.parent
    if str(root_dir) not in sys.path:
        sys.path.insert(0, str(root_dir))

    from tools.check_links import run_link_checker

    run_link_checker(
        db_path=db_path,
        concurrency=concurrency,
        limit=limit,
        dry_run=dry_run,
    )


@app.command("deep-verify")
def deep_verify(
    batch: int = typer.Option(
        2027,
        "--batch",
        "-b",
        help="Target student graduation year to verify against (default: 2027).",
    ),
    concurrency: int = typer.Option(
        25,
        "--concurrency",
        "-c",
        help="Concurrent link crawler connections (default: 25).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview evaluation and audit table without modifying database.",
    ),
    hard_delete: bool = typer.Option(
        False,
        "--hard-delete",
        help="Permanently delete disqualified jobs from database instead of marking DISMISSED.",
    ),
    limit: Optional[int] = typer.Option(
        None,
        "--limit",
        "-l",
        help="Maximum number of active jobs to audit.",
    ),
    db_path: Optional[Path] = typer.Option(
        None,
        "--db",
        help="Custom path to SQLite database file.",
    ),
) -> None:
    """Crawl job posting links, read full descriptions, and purge/dismiss roles that do not fit Class of 2027."""
    import sys
    root_dir = Path(__file__).resolve().parent.parent
    if str(root_dir) not in sys.path:
        sys.path.insert(0, str(root_dir))

    from tools.deep_verify_jobs import run_deep_verification

    asyncio.run(
        run_deep_verification(
            batch_year=batch,
            concurrency=concurrency,
            dry_run=dry_run,
            hard_delete=hard_delete,
            limit=limit,
            db_path=db_path,
        )
    )


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        "-v",
        help="Show version and exit.",
        is_eager=True,
    ),
    company: Optional[str] = typer.Option(
        None,
        "--company",
        "-c",
        help="Filter scan to a specific company by name or board token.",
    ),
    provider: Optional[str] = typer.Option(
        None,
        "--provider",
        "-p",
        help="Filter scan by ATS provider (e.g., greenhouse, ashby, lever, workday, smartrecruiters).",
    ),
    concurrency: int = typer.Option(
        30,
        "--concurrency",
        help="Maximum concurrent HTTP requests to ATS endpoints.",
    ),
    remote_only: bool = typer.Option(
        False,
        "--remote-only",
        "--remote",
        "-r",
        help="Only display and export 100% remote roles eligible in India.",
    ),
    new_only: bool = typer.Option(
        False,
        "--new-only",
        "-n",
        help="Only display and export postings not seen in previous runs.",
    ),
    min_score: int = typer.Option(
        0,
        "--min-score",
        "-m",
        help="Filter out jobs with relevance score below this threshold (0-100).",
    ),
    digest: bool = typer.Option(
        False,
        "--digest",
        help="Batch alerts into a single consolidated daily digest message.",
    ),
    list_dormant: bool = typer.Option(
        False,
        "--list-dormant",
        help="Display registered dormant / paused companies and exit.",
    ),
    reactivate_comp: Optional[str] = typer.Option(
        None,
        "--reactivate",
        help="Reactivate a dormant company so it is monitored in scans.",
    ),
    auto_dormant_threshold: int = typer.Option(
        5,
        "--auto-dormant-threshold",
        help="Number of consecutive scans with 0 matches before auto-marking a company dormant (0 to disable).",
    ),
    stats: bool = typer.Option(
        False,
        "--stats",
        help="Display historical database statistics and exit.",
    ),
    db_path: Optional[Path] = typer.Option(
        None,
        "--db",
        help="Custom path to SQLite database file.",
    ),
    notify_discord: Optional[str] = typer.Option(
        None,
        "--notify-discord",
        help="Discord webhook URL to alert on newly detected postings.",
    ),
    notify_telegram_token: Optional[str] = typer.Option(
        None,
        "--notify-telegram-token",
        help="Telegram bot token to alert on newly detected postings.",
    ),
    notify_telegram_chat: Optional[str] = typer.Option(
        None,
        "--notify-telegram-chat",
        help="Telegram chat ID or channel to alert on newly detected postings.",
    ),
    json_path: Optional[Path] = typer.Option(
        None,
        "--json",
        "-j",
        help="Path to export results to a JSON file.",
    ),
    csv_path: Optional[Path] = typer.Option(
        None,
        "--csv",
        help="Path to export results to a CSV file.",
    ),
) -> None:
    """Root entrypoint: runs scan when no subcommand is given."""
    if version:
        console.print(f"[bold green]gcc-job-radar[/bold green] version [cyan]{__version__}[/cyan]")
        raise typer.Exit()

    if ctx.invoked_subcommand is None:
        scan(
            company=company,
            provider=provider,
            concurrency=concurrency,
            remote_only=remote_only,
            new_only=new_only,
            min_score=min_score,
            digest=digest,
            list_dormant=list_dormant,
            reactivate_comp=reactivate_comp,
            auto_dormant_threshold=auto_dormant_threshold,
            stats=stats,
            db_path=db_path,
            notify_discord=notify_discord,
            notify_telegram_token=notify_telegram_token,
            notify_telegram_chat=notify_telegram_chat,
            json_path=json_path,
            csv_path=csv_path,
        )


if __name__ == "__main__":
    app()
