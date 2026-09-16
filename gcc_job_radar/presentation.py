"""Shared presentation and formatting engine for ranked job recommendations."""

from dataclasses import dataclass, field
import html
from typing import Any, Optional, Sequence

from gcc_job_radar.link_resolver import resolve_effective_apply_url
from gcc_job_radar.models import ATSProvider, JobPosting
from gcc_job_radar.relevance import evaluate_job_relevance, score_job_posting


@dataclass
class JobTier:
    """A tier grouping of job postings based on tech-stack relevance score."""

    label: str  # "⭐ Best Fit", "⚡ Strong Fit", "📋 Worth a Look"
    jobs: list[JobPosting] = field(default_factory=list)


@dataclass
class RankedPresentation:
    """Pure data representation of ranked, tiered job recommendations."""

    lead_in: str
    tiers: list[JobTier]
    tail_summary: Optional[str]
    omitted_jobs: list[JobPosting]
    omitted_companies: list[str]
    total_count: int
    strong_count: int


def to_job_posting(item: JobPosting | dict[str, Any]) -> JobPosting:
    """Normalize a JobPosting or dictionary into a scored JobPosting instance."""
    if isinstance(item, JobPosting):
        if item.relevance_score is None or item.relevance_score == 0:
            score_job_posting(item)
        elif not item.why:
            _, reasons, why = evaluate_job_relevance(
                title=item.title,
                description=item.description or item.notes or "",
                published_date=item.published_date,
                is_remote=item.is_remote,
            )
            item.why = why
            item.matched_reasons = reasons
        return item

    prov_val = item.get("provider", "custom")
    if isinstance(prov_val, ATSProvider):
        prov_enum = prov_val
    else:
        try:
            prov_enum = ATSProvider(str(prov_val).lower())
        except Exception:
            prov_enum = ATSProvider.CUSTOM

    apply_u = item.get("apply_url") or "https://example.com"
    if not str(apply_u).startswith(("http://", "https://")):
        apply_u = "https://example.com"

    posting = JobPosting(
        id=str(item.get("id") or item.get("numeric_id") or "job"),
        numeric_id=item.get("numeric_id"),
        company=str(item.get("company", "Unknown")),
        title=str(item.get("title", "Role")),
        location=str(item.get("location", "India")),
        apply_url=apply_u,
        published_date=item.get("published_date") or "Active",
        provider=prov_enum,
        is_remote=bool(item.get("is_remote", False)),
        status=str(item.get("status", "NEW")),
        direct_search_url=item.get("direct_search_url"),
        description=item.get("description") or item.get("notes") or item.get("content"),
        relevance_score=item.get("relevance_score"),
        matched_reasons=item.get("matched_reasons") or [],
        why=item.get("why"),
        tailored_tex_path=item.get("tailored_tex_path"),
        tailored_pdf_path=item.get("tailored_pdf_path"),
    )
    if posting.relevance_score is None or posting.relevance_score == 0:
        score_job_posting(posting)
    elif not posting.why:
        _, reasons, why = evaluate_job_relevance(
            title=posting.title,
            description=posting.description or "",
            published_date=posting.published_date,
            is_remote=posting.is_remote,
        )
        posting.why = why
        posting.matched_reasons = reasons
    return posting


def build_ranked_presentation(
    jobs: Sequence[JobPosting | dict[str, Any]],
    max_full_cards: int = 5,
) -> RankedPresentation:
    """Build pure data ranked recommendation presentation from a sequence of jobs."""
    if not jobs:
        return RankedPresentation(
            lead_in="No matching entry-level roles found.",
            tiers=[],
            tail_summary=None,
            omitted_jobs=[],
            omitted_companies=[],
            total_count=0,
            strong_count=0,
        )

    # 1. Normalize and score all jobs
    scored_jobs = [to_job_posting(j) for j in jobs]

    # 2. Sort by relevance_score DESC
    sorted_jobs = sorted(
        scored_jobs,
        key=lambda j: (getattr(j, "relevance_score", 0) or 0),
        reverse=True,
    )
    total_count = len(sorted_jobs)

    # 3. Calculate strong_count (roles with score >= 20)
    strong_count = sum(1 for j in sorted_jobs if (getattr(j, "relevance_score", 0) or 0) >= 20)

    # 4. Formulate one-line lead-in
    if total_count == 1:
        if strong_count == 1:
            lead_in = "Found 1 new role, a strong fit for your stack."
        else:
            lead_in = "Found 1 verified entry-level opening."
    else:
        if strong_count > 0:
            fit_word = "strong fit" if strong_count == 1 else "strong fits"
            lead_in = f"Found {total_count} new roles, {strong_count} {fit_word} for your stack."
        else:
            lead_in = f"Found {total_count} verified entry-level roles across target GCCs."

    # 5. Partition displayed jobs vs omitted jobs for tail summary
    if total_count > max_full_cards:
        displayed_jobs = sorted_jobs[:max_full_cards]
        omitted_jobs = sorted_jobs[max_full_cards:]

        omitted_companies: list[str] = []
        for j in omitted_jobs:
            c = j.company.strip()
            if c and c not in omitted_companies:
                omitted_companies.append(c)

        comp_str = ", ".join(omitted_companies[:4])
        if len(omitted_companies) > 4:
            comp_str += f" (+{len(omitted_companies) - 4} more)"
        tail_summary = f"+{len(omitted_jobs)} more roles at {comp_str} — reply '/latest all' or search by company to view"
    else:
        displayed_jobs = sorted_jobs
        omitted_jobs = []
        omitted_companies = []
        tail_summary = None

    # 6. Assign displayed jobs to tiers
    best_fit_jobs: list[JobPosting] = []
    strong_fit_jobs: list[JobPosting] = []
    worth_look_jobs: list[JobPosting] = []

    has_high_score = any((getattr(j, "relevance_score", 0) or 0) >= 40 for j in displayed_jobs)
    if has_high_score:
        for j in displayed_jobs:
            s = getattr(j, "relevance_score", 0) or 0
            if s >= 40:
                best_fit_jobs.append(j)
            elif s >= 20:
                strong_fit_jobs.append(j)
            else:
                worth_look_jobs.append(j)
    else:
        has_medium_score = any((getattr(j, "relevance_score", 0) or 0) >= 25 for j in displayed_jobs)
        if has_medium_score:
            count_top = 0
            for j in displayed_jobs:
                s = getattr(j, "relevance_score", 0) or 0
                if s >= 25 and count_top < 2:
                    best_fit_jobs.append(j)
                    count_top += 1
                elif s >= 20:
                    strong_fit_jobs.append(j)
                else:
                    worth_look_jobs.append(j)
        else:
            for j in displayed_jobs:
                s = getattr(j, "relevance_score", 0) or 0
                if s >= 15:
                    strong_fit_jobs.append(j)
                else:
                    worth_look_jobs.append(j)

    tiers: list[JobTier] = []
    if best_fit_jobs:
        tiers.append(JobTier(label="⭐ Best Fit", jobs=best_fit_jobs))
    if strong_fit_jobs:
        tiers.append(JobTier(label="⚡ Strong Fit", jobs=strong_fit_jobs))
    if worth_look_jobs:
        tiers.append(JobTier(label="📋 Worth a Look", jobs=worth_look_jobs))

    return RankedPresentation(
        lead_in=lead_in,
        tiers=tiers,
        tail_summary=tail_summary,
        omitted_jobs=omitted_jobs,
        omitted_companies=omitted_companies,
        total_count=total_count,
        strong_count=strong_count,
    )


def format_jobs_html(
    jobs: Sequence[JobPosting | dict[str, Any]],
    title: str = "",
    max_full_cards: int = 5,
) -> str:
    """Format job listings into structured, ranked Telegram HTML."""
    if not jobs:
        escaped_title = html.escape(title) if title else "Job Openings"
        return f"ℹ️ <b>{escaped_title}</b>\n\nNo matching entry-level roles found."

    presentation = build_ranked_presentation(jobs, max_full_cards=max_full_cards)

    parts: list[str] = []

    # Title header
    if title:
        count_suffix = "" if f"({presentation.total_count})" in title else f" ({presentation.total_count})"
        parts.append(f"🚀 <b>{html.escape(title)}{count_suffix}</b>")

    # One-line lead-in
    parts.append(presentation.lead_in)

    # Tiers and Cards with continuous numbering
    current_idx = 1
    for tier in presentation.tiers:
        tier_blocks = [f"{tier.label}:"]
        for job in tier.jobs:
            effective_url, _, label = resolve_effective_apply_url(job)
            clean_company = html.escape(job.company)
            clean_title = html.escape(job.title)
            clean_location = html.escape(job.location)
            ats = job.provider.value.upper()
            date = html.escape(str(job.published_date or "Active"))
            why_text = html.escape(job.why or "Verified entry-level opening")

            card = (
                f"<b>{current_idx}. {clean_company}</b>\n"
                f"💼 {clean_title}\n"
                f"📍 {clean_location} ({ats}) • 📅 {date}\n"
                f'🔗 <a href="{html.escape(str(effective_url))}">{html.escape(label)}</a>\n'
                f"💡 <i>{why_text}</i>"
            )
            if getattr(job, "tailored_tex_path", None):
                tex_p = html.escape(str(job.tailored_tex_path))
                pdf_note = (
                    f" (PDF: <code>{html.escape(str(job.tailored_pdf_path))}</code>)"
                    if getattr(job, "tailored_pdf_path", None)
                    else ""
                )
                card += f"\n📄 <b>Resume:</b> <code>{tex_p}</code>{pdf_note}"

            tier_blocks.append(card)
            current_idx += 1

        parts.append("\n\n".join(tier_blocks))

    # Tail summary for omitted jobs
    if presentation.tail_summary:
        if presentation.omitted_companies:
            comp_str = ", ".join(presentation.omitted_companies[:4])
            if len(presentation.omitted_companies) > 4:
                comp_str += f" (+{len(presentation.omitted_companies) - 4} more)"
            tail_line = f"➕ <b>+{len(presentation.omitted_jobs)} more roles at {html.escape(comp_str)}</b> — reply <code>/latest all</code> or search by company to view"
        else:
            tail_line = f"➕ <b>{html.escape(presentation.tail_summary)}</b>"
        parts.append(tail_line)

    return "\n\n".join(parts).strip()
