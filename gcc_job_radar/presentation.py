"""Shared presentation and formatting engine for ranked job recommendations."""

from dataclasses import dataclass, field
import html
from html.parser import HTMLParser
import re
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
        application_start_date=item.get("application_start_date"),
        application_end_date=item.get("application_end_date"),
        is_expired=bool(item.get("is_expired", False)),
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
        for j in best_fit_jobs:
            if isinstance(j, JobPosting):
                j.tier = "⭐ Best Fit"
            elif isinstance(j, dict):
                j["tier"] = "⭐ Best Fit"
        tiers.append(JobTier(label="⭐ Best Fit", jobs=best_fit_jobs))
    if strong_fit_jobs:
        for j in strong_fit_jobs:
            if isinstance(j, JobPosting):
                j.tier = "⚡ Strong Fit"
            elif isinstance(j, dict):
                j["tier"] = "⚡ Strong Fit"
        tiers.append(JobTier(label="⚡ Strong Fit", jobs=strong_fit_jobs))
    if worth_look_jobs:
        for j in worth_look_jobs:
            if isinstance(j, JobPosting):
                j.tier = "📋 Worth a Look"
            elif isinstance(j, dict):
                j["tier"] = "📋 Worth a Look"
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


def _format_presentation_job_card(job: JobPosting | dict[str, Any], idx: int) -> str:
    """Format an individual job listing card for ranked presentation."""
    effective_url, _, label = resolve_effective_apply_url(job)
    if isinstance(job, JobPosting):
        comp = job.company
        title = job.title
        loc = job.location
        ats = job.provider.value.upper()
        pub_date = job.published_date
        start_date = getattr(job, "application_start_date", None) or pub_date
        end_date = getattr(job, "application_end_date", None)
        why_text = job.why or "Verified entry-level opening"
        tex_p = getattr(job, "tailored_tex_path", None)
        pdf_p = getattr(job, "tailored_pdf_path", None)
    else:
        comp = job.get("company", "Unknown")
        title = job.get("title", "Role")
        loc = job.get("location", "India")
        prov = job.get("provider", "")
        ats = getattr(prov, "value", str(prov)).upper()
        pub_date = job.get("published_date")
        start_date = job.get("application_start_date") or pub_date
        end_date = job.get("application_end_date")
        why_text = job.get("why") or "Verified entry-level opening"
        tex_p = job.get("tailored_tex_path")
        pdf_p = job.get("tailored_pdf_path")

    clean_company = html.escape(str(comp))
    clean_title = html.escape(str(title))
    clean_location = html.escape(str(loc))
    why_clean = html.escape(str(why_text))

    date_parts: list[str] = []
    if start_date and str(start_date).strip() not in ("Recent", "Active", "None", ""):
        date_parts.append(f"📅 Posted: {html.escape(str(start_date))}")
    elif not end_date:
        date_parts.append(f"📅 {html.escape(str(pub_date or 'Active'))}")

    if end_date and str(end_date).strip():
        date_parts.append(f"⏳ Closes: {html.escape(str(end_date))}")

    date_str = " • ".join(date_parts) if date_parts else f"📅 {html.escape(str(pub_date or 'Active'))}"

    card = (
        f"<b>{idx}. {clean_company}</b>\n"
        f"💼 {clean_title}\n"
        f"📍 {clean_location} ({ats}) • {date_str}\n"
        f'🔗 <a href="{html.escape(str(effective_url))}">{html.escape(label)}</a>\n'
        f"💡 <i>{why_clean}</i>"
    )
    if tex_p:
        escaped_tex = html.escape(str(tex_p))
        pdf_note = f" (PDF: <code>{html.escape(str(pdf_p))}</code>)" if pdf_p else ""
        card += f"\n📄 <b>Resume:</b> <code>{escaped_tex}</code>{pdf_note}"

    return card


def format_jobs_html(
    jobs: Sequence[JobPosting | dict[str, Any]],
    title: str = "",
    max_full_cards: int = 5,
) -> str:
    """Format job listings into structured, ranked Telegram HTML with expandable blockquote."""
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
            tier_blocks.append(_format_presentation_job_card(job, current_idx))
            current_idx += 1
        parts.append("\n\n".join(tier_blocks))

    # Expandable section containing all remaining/omitted job cards
    if presentation.omitted_jobs:
        if presentation.omitted_companies:
            comp_str = ", ".join(presentation.omitted_companies[:4])
            if len(presentation.omitted_companies) > 4:
                comp_str += f" (+{len(presentation.omitted_companies) - 4} more)"
            summary_header = f"➕ <b>+{len(presentation.omitted_jobs)} more roles at {html.escape(comp_str)}</b> — reply <code>/latest all</code> or search by company to view"
        else:
            summary_header = f"➕ <b>{html.escape(presentation.tail_summary or f'+{len(presentation.omitted_jobs)} more roles')}</b>"

        omitted_blocks = [summary_header]
        for job in presentation.omitted_jobs:
            omitted_blocks.append(_format_presentation_job_card(job, current_idx))
            current_idx += 1

        parts.append(f"<blockquote expandable>\n" + "\n\n".join(omitted_blocks) + "\n</blockquote>")
    elif presentation.tail_summary:
        parts.append(f"➕ <b>{html.escape(presentation.tail_summary)}</b>")

    return "\n\n".join(parts).strip()


def format_job_card_html(job: JobPosting | dict[str, Any]) -> str:
    """Format an individual job posting card for Telegram alert."""
    if isinstance(job, JobPosting):
        company = job.company
        pos_title = job.title
        location = job.location
        ats = job.provider.value.upper()
        date = job.published_date or "Active"
    else:
        company = job.get("company", "Unknown")
        pos_title = job.get("title", "Role")
        location = job.get("location", "India")
        prov = job.get("provider", "")
        ats = getattr(prov, "value", str(prov)).upper()
        date = job.get("published_date") or "Active"

    effective_url, _, label = resolve_effective_apply_url(job)
    start_date = getattr(job, "application_start_date", None) if isinstance(job, JobPosting) else job.get("application_start_date")
    end_date = getattr(job, "application_end_date", None) if isinstance(job, JobPosting) else job.get("application_end_date")

    date_parts: list[str] = []
    if start_date and str(start_date).strip() not in ("Recent", "Active", "None", ""):
        date_parts.append(f"📅 Posted: {html.escape(str(start_date))}")
    elif not end_date:
        date_parts.append(f"📅 {html.escape(str(date or 'Active'))}")
    if end_date and str(end_date).strip():
        date_parts.append(f"⏳ Closes: {html.escape(str(end_date))}")
    date_line = " • ".join(date_parts) if date_parts else f"📅 {html.escape(str(date or 'Active'))}"

    card = (
        f"🚀 <b>{html.escape(company)}</b>\n"
        f"💼 {html.escape(pos_title)}\n"
        f"📍 {html.escape(location)} ({ats}) • {date_line}\n"
        f'🔗 <a href="{html.escape(str(effective_url))}">{html.escape(label)}</a>'
    )
    tex_path = getattr(job, "tailored_tex_path", None) if isinstance(job, JobPosting) else (job.get("tailored_tex_path") if isinstance(job, dict) else None)
    pdf_path = getattr(job, "tailored_pdf_path", None) if isinstance(job, JobPosting) else (job.get("tailored_pdf_path") if isinstance(job, dict) else None)
    why_text = getattr(job, "why", None) if isinstance(job, JobPosting) else (job.get("why") if isinstance(job, dict) else None)
    if not why_text:
        score_job_posting(job)
        why_text = getattr(job, "why", None) if isinstance(job, JobPosting) else (job.get("why") if isinstance(job, dict) else None)
    if why_text:
        card += f"\n💡 <i>{html.escape(str(why_text))}</i>"
    if tex_path:
        pdf_note = f" (PDF: <code>{html.escape(str(pdf_path))}</code>)" if pdf_path else ""
        card += f"\n📄 <b>Tailored Resume:</b> <code>{html.escape(str(tex_path))}</code>{pdf_note}"
    return card


def split_telegram_message(text: str, max_length: int = 3950) -> list[str]:
    """Split a long message into safe chunks <= max_length (Telegram's hard limit is 4096).

    Prefers splitting on paragraph breaks (\n\n), then line breaks (\n), then hard slices.
    Safely closes and re-opens <blockquote expandable> blocks across chunk boundaries.
    """
    if not text:
        return [""]
    if len(text) <= max_length:
        return [text]

    chunks: list[str] = []
    paragraphs = text.split("\n\n")
    current_chunk = ""
    in_expandable = False

    for para in paragraphs:
        para_opens = "<blockquote expandable>" in para
        para_closes = "</blockquote>" in para

        head_tag = "<blockquote expandable>\n" if in_expandable and not para_opens and not current_chunk else ""
        tail_tag = "\n</blockquote>" if (in_expandable or para_opens) and not para_closes else ""

        candidate = f"{current_chunk}\n\n{para}" if current_chunk else f"{head_tag}{para}"
        candidate_len = len(candidate) + (len(tail_tag) if tail_tag and not candidate.endswith("</blockquote>") else 0)

        if candidate_len <= max_length:
            current_chunk = candidate
            if para_opens:
                in_expandable = True
            if para_closes:
                in_expandable = False
        else:
            if current_chunk:
                if in_expandable and not current_chunk.rstrip().endswith("</blockquote>"):
                    current_chunk = f"{current_chunk}\n</blockquote>"
                chunks.append(current_chunk)
                current_chunk = ""

            head_tag = "<blockquote expandable>\n" if in_expandable and not para_opens else ""
            para_to_add = f"{head_tag}{para}"
            tail_tag = "\n</blockquote>" if in_expandable and not para_closes else ""

            if len(para_to_add) + (len(tail_tag) if tail_tag and not para_to_add.endswith("</blockquote>") else 0) > max_length:
                lines = para_to_add.split("\n")
                line_chunk = ""
                for line in lines:
                    line_tail = "\n</blockquote>" if in_expandable and not line.endswith("</blockquote>") else ""
                    if len(line_chunk) + (1 if line_chunk else 0) + len(line) + len(line_tail) <= max_length:
                        line_chunk = f"{line_chunk}\n{line}" if line_chunk else line
                    else:
                        if line_chunk:
                            if in_expandable and not line_chunk.rstrip().endswith("</blockquote>"):
                                line_chunk = f"{line_chunk}\n</blockquote>"
                            chunks.append(line_chunk)
                            line_chunk = ""
                        while len(line) > max_length:
                            chunks.append(line[:max_length])
                            line = line[max_length:]
                        line_head = "<blockquote expandable>\n" if in_expandable and not ("<blockquote expandable>" in line) else ""
                        line_chunk = f"{line_head}{line}"
                    if "<blockquote expandable>" in line:
                        in_expandable = True
                    if "</blockquote>" in line:
                        in_expandable = False
                if line_chunk:
                    current_chunk = line_chunk
            else:
                current_chunk = para_to_add
                if para_opens:
                    in_expandable = True
                if para_closes:
                    in_expandable = False

    if current_chunk:
        if in_expandable and not current_chunk.rstrip().endswith("</blockquote>"):
            current_chunk = f"{current_chunk}\n</blockquote>"
        chunks.append(current_chunk)

    return chunks


def _clean_cell(val: str) -> str:
    val = val.strip()
    if (val.startswith("**") and val.endswith("**")) or (val.startswith("__") and val.endswith("__")):
        val = val[2:-2].strip()
    return val


def _split_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _is_table_separator(line: str) -> bool:
    stripped = line.strip()
    return bool(re.match(r"^\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)+\|?$", stripped))


def convert_markdown_tables_to_cards(text: str) -> str:
    """Convert raw markdown tables into mobile-friendly structured cards."""
    lines = text.split("\n")
    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if "|" in line and i + 1 < len(lines) and _is_table_separator(lines[i + 1]):
            headers = [_clean_cell(c) for c in _split_table_row(line)]
            i += 2  # skip header and separator line
            table_cards = []
            while i < len(lines) and "|" in lines[i] and not _is_table_separator(lines[i]):
                row_cells = _split_table_row(lines[i])
                row_dict = {h.lower(): cell for h, cell in zip(headers, row_cells)}

                comp = next((_clean_cell(v) for k, v in row_dict.items() if any(w in k for w in ["company", "employer", "org", "firm"]) and v), None)
                role = next((_clean_cell(v) for k, v in row_dict.items() if any(w in k for w in ["role", "title", "position", "job", "designation"]) and v), None)
                loc = next((_clean_cell(v) for k, v in row_dict.items() if any(w in k for w in ["location", "city", "place", "office"]) and v), None)
                date = next((_clean_cell(v) for k, v in row_dict.items() if any(w in k for w in ["posted", "date", "published", "added"]) and v), None)
                link = next((v for k, v in row_dict.items() if any(w in k for w in ["link", "apply", "url", "action"]) and v), None)

                if comp or role:
                    card = []
                    if comp:
                        card.append(f"🏢 **{comp}**")
                    if role:
                        card.append(f"💼 {role}")
                    meta = []
                    if loc:
                        meta.append(f"📍 {loc}")
                    if date:
                        meta.append(f"📅 {date}")
                    if meta:
                        card.append(" • ".join(meta))
                    if link:
                        if link.startswith("[") and "](" in link:
                            card.append(f"🔗 {link}")
                        elif link.startswith("http://") or link.startswith("https://"):
                            card.append(f"🔗 [Apply on ATS]({link})")
                        else:
                            card.append(f"🔗 {link}")
                    table_cards.append("\n".join(card))
                else:
                    items = [f"• **{h.title()}**: {v}" for h, v in zip(headers, row_cells) if v]
                    table_cards.append("\n".join(items))
                i += 1
            if table_cards:
                new_lines.append("\n\n".join(table_cards))
            continue
        new_lines.append(line)
        i += 1
    return "\n".join(new_lines)


class TelegramHTMLSanitizer(HTMLParser):
    """Enforces strictly balanced, properly nested HTML tags for Telegram Bot API."""

    ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre", "a", "blockquote"}

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.out: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        if tag not in self.ALLOWED_TAGS:
            return
        attr_str = ""
        if tag == "a":
            href = dict(attrs).get("href", "")
            if href:
                attr_str = f' href="{html.escape(href, quote=True)}"'
            else:
                return
        elif tag == "blockquote":
            if any(attr[0].lower() == "expandable" for attr in attrs):
                attr_str = " expandable"
        self.stack.append(tag)
        self.out.append(f"<{tag}{attr_str}>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag not in self.ALLOWED_TAGS or tag not in self.stack:
            return
        # Pop down to tag, closing in proper reverse order so tags are NEVER uncrossed or overlapping
        while self.stack:
            top = self.stack.pop()
            self.out.append(f"</{top}>")
            if top == tag:
                break

    def handle_data(self, data: str) -> None:
        self.out.append(html.escape(data, quote=False))

    def get_clean_html(self) -> str:
        # Close any lingering tags at end of message
        while self.stack:
            top = self.stack.pop()
            self.out.append(f"</{top}>")
        return "".join(self.out)


def sanitize_telegram_html(text: str) -> str:
    """Strictly balance and validate HTML tags for Telegram Bot API."""
    if not text:
        return ""
    try:
        parser = TelegramHTMLSanitizer()
        parser.feed(text)
        return parser.get_clean_html().strip()
    except Exception:
        return re.sub(r"<[^>]+>", "", text).strip()


def markdown_to_telegram_html(text: str) -> str:
    """Convert common markdown patterns to safe Telegram HTML."""
    if not text:
        return ""

    # 1. Convert any raw markdown tables to clean card layout
    text = convert_markdown_tables_to_cards(text)

    # 2. Convert markdown task list checkboxes to visual checkboxes for Telegram
    text = re.sub(r"(?m)^[\*\-]\s+\[\s*\]\s+", "☐ ", text)
    text = re.sub(r"(?m)^[\*\-]\s+\[[xX]\]\s+", "☑ ", text)

    # 3. Convert remaining markdown bullet points (* or - at start of line) to •
    text = re.sub(r"(?m)^[\*\-]\s+", "• ", text)

    # Replace markdown code blocks ```code``` -> <pre>code</pre>
    def replace_code_block(match: re.Match) -> str:
        content = match.group(1)
        return f"<pre>{html.escape(content.strip())}</pre>"

    text = re.sub(r"```(?:[a-zA-Z0-9_-]+)?\n?(.*?)```", replace_code_block, text, flags=re.DOTALL)

    # Escape HTML special chars in text outside already replaced <pre>
    parts = re.split(r"(<pre>.*?</pre>)", text, flags=re.DOTALL)
    escaped_parts = []
    for part in parts:
        if part.startswith("<pre>"):
            escaped_parts.append(part)
        else:
            # Escape raw & < >
            part = html.escape(part)
            # Convert markdown headers (### Header) to bold <b>Header</b>
            part = re.sub(r"(?m)^#{1,6}\s+\**([^\*\n]+?)\**\s*$", r"<b>\1</b>", part)
            # Restore markdown links [title](url) -> <a href="url">title</a>
            part = re.sub(r"\[([^\]]+)\]\((https?://[^\)]+)\)", r'<a href="\2">\1</a>', part)
            # Bold **text** or __text__ -> <b>text</b>
            part = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", part)
            part = re.sub(r"__(.+?)__", r"<b>\1</b>", part)

            # Inline code `code` -> <code>code</code>
            part = re.sub(r"`([^`]+)`", r"<code>\1</code>", part)
            # Italics *text* or _text_ -> <i>text</i> (excluding word boundaries or whitespace)
            part = re.sub(r"(?<![\*\w])\*([^\*\s](?:[^\*]*?[^\*\s])?)\*(?![\*\w])", r"<i>\1</i>", part)
            part = re.sub(r"(?<![_\w])_([^_\s](?:[^_]*?[^_\s])?)_(?![_\w])", r"<i>\1</i>", part)
            escaped_parts.append(part)

    raw_html = "".join(escaped_parts).strip()
    return sanitize_telegram_html(raw_html)

