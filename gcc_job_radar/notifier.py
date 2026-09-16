"""Webhook notification dispatchers for Discord and Telegram."""

import html
import logging
import os
from typing import Any, Optional
import httpx
from rich.console import Console

from gcc_job_radar.link_resolver import resolve_effective_apply_url
from gcc_job_radar.models import JobPosting
from gcc_job_radar.presentation import build_ranked_presentation, format_jobs_html
from gcc_job_radar.relevance import score_job_posting
from gcc_job_radar.resume_tailor_bridge import tailor_resume_for_job

logger = logging.getLogger(__name__)
console = Console(highlight=False)

DISCORD_EMBED_COLOR = 0x00FF88  # Bright neon green
MAX_DISCORD_FIELDS_PER_EMBED = 25
DISCORD_CHUNK_SIZE = 5


async def send_discord_notification(
    webhook_url: str, new_jobs: list[JobPosting], client: httpx.AsyncClient
) -> bool:
    """Send formatted Discord webhook embeds for newly detected job postings."""
    webhook_url = webhook_url.strip() if webhook_url else ""
    if not webhook_url or not new_jobs:
        return False

    success = True
    # Send in chunks of 5 to stay well within Discord message size limits
    for i in range(0, len(new_jobs), DISCORD_CHUNK_SIZE):
        chunk = new_jobs[i : i + DISCORD_CHUNK_SIZE]
        embeds = []

        for job in chunk:
            effective_url, _, label = resolve_effective_apply_url(job)
            embed = {
                "title": f"🚀 {job.company} - {job.title}",
                "url": str(effective_url),
                "color": DISCORD_EMBED_COLOR,
                "fields": [
                    {"name": "🏢 Company", "value": job.company, "inline": True},
                    {"name": "💼 Position", "value": job.title, "inline": True},
                    {"name": "📍 Location", "value": job.location, "inline": True},
                    {"name": "📡 Source", "value": job.provider.value.upper(), "inline": True},
                    {"name": "📅 Date", "value": job.published_date or "Active", "inline": True},
                ],
                "footer": {
                    "text": "GCC Job Radar • India Tech Tracker"
                },
            }
            if getattr(job, "why", None):
                embed["fields"].append({
                    "name": "💡 Why",
                    "value": str(job.why),
                    "inline": False,
                })
            embed["fields"].append({
                "name": "🔗 Apply Link",
                "value": f"[{label}]({effective_url})",
                "inline": False,
            })
            if getattr(job, "tailored_tex_path", None):
                res_val = f"`{job.tailored_tex_path}`"
                if getattr(job, "tailored_pdf_path", None):
                    res_val += f" (PDF: `{job.tailored_pdf_path}`)"
                embed["fields"].append({
                    "name": "📄 Tailored Resume",
                    "value": res_val,
                    "inline": False,
                })
            embeds.append(embed)

        payload = {
            "content": "⚡ **New GCC Entry-Level Opening(s) Detected!**" if i == 0 else "",
            "embeds": embeds,
        }

        try:
            resp = await client.post(webhook_url, json=payload)
            if resp.status_code not in (200, 204):
                logger.warning("Discord webhook returned status %s: %s", resp.status_code, resp.text)
                success = False
        except Exception as exc:
            logger.warning("Failed to send Discord webhook: %s", exc)
            success = False

    return success


def build_job_inline_keyboard(job: JobPosting | dict[str, Any]) -> dict[str, Any]:
    """Build Telegram InlineKeyboardMarkup for an individual job posting card.

    Requirements:
    - Button 1 (URL): "Apply" pointing to apply_url.
    - Button 2 (URL, conditional): If status is 'NEEDS_RESOLVE' or direct_search_url is present,
      add "Search Direct ATS" pointing to direct_search_url.
    - Button 3 (Callback): "Dismiss" (callback_data="dismiss:{job_id}").
    - Button 4 (Callback): "Applied" (callback_data="applied:{job_id}").
    """
    if isinstance(job, JobPosting):
        job_id = job.numeric_id if job.numeric_id is not None else job.id
        effective_url, _, _ = resolve_effective_apply_url(job)
        apply_url = str(effective_url) if effective_url else str(job.apply_url)
        direct_search_url = str(job.direct_search_url) if job.direct_search_url else None
        status = getattr(job, "status", "NEW")
    else:
        job_id = job.get("numeric_id") if job.get("numeric_id") is not None else job.get("id", "")
        effective_url, _, _ = resolve_effective_apply_url(job)
        apply_url = str(effective_url) if effective_url else str(job.get("apply_url", ""))
        direct_search_url = str(job.get("direct_search_url")) if job.get("direct_search_url") else None
        status = job.get("status", "NEW")

    # Row 1: URL Navigation Buttons
    row_1: list[dict[str, str]] = [{"text": "Apply", "url": apply_url}]
    if (status == "NEEDS_RESOLVE" or direct_search_url) and direct_search_url:
        row_1.append({"text": "Search Direct ATS", "url": direct_search_url})

    # Row 2: Interactive Callback Buttons
    row_2: list[dict[str, str]] = [
        {"text": "Dismiss", "callback_data": f"dismiss:{job_id}"},
        {"text": "Applied", "callback_data": f"applied:{job_id}"},
    ]

    return {
        "inline_keyboard": [row_1, row_2]
    }


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
    card = (
        f"🚀 <b>{html.escape(company)}</b>\n"
        f"💼 {html.escape(pos_title)}\n"
        f"📍 {html.escape(location)} ({ats}) • 📅 {html.escape(date)}\n"
        f"🔗 <a href=\"{html.escape(str(effective_url))}\">{html.escape(label)}</a>"
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


async def send_telegram_job_card(
    bot_token: str,
    chat_id: str | int,
    job: JobPosting | dict[str, Any],
    client: httpx.AsyncClient,
) -> bool:
    """Send an individual interactive job card with InlineKeyboardMarkup."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": str(chat_id),
        "text": format_job_card_html(job),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": build_job_inline_keyboard(job),
    }
    try:
        resp = await client.post(url, json=payload, timeout=10.0)
        return resp.status_code == 200
    except Exception as exc:
        logger.warning("Failed to send Telegram job card: %s", exc)
        return False


async def send_telegram_notification(
    bot_token: str,
    chat_id: str,
    new_jobs: list[JobPosting],
    client: httpx.AsyncClient,
    send_as_cards: bool = False,
) -> bool:
    """Send formatted Telegram message via Bot API for newly detected postings."""
    bot_token = bot_token.strip() if bot_token else ""
    chat_id = chat_id.strip() if chat_id else ""
    if not bot_token or not chat_id or not new_jobs:
        return False

    if send_as_cards:
        all_ok = True
        for job in new_jobs:
            ok = await send_telegram_job_card(bot_token, chat_id, job, client)
            if not ok:
                all_ok = False
        return all_ok

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    success = True

    # Use shared presentation engine
    presentation = build_ranked_presentation(new_jobs, max_full_cards=6)
    full_text = format_jobs_html(
        new_jobs,
        title="New GCC Entry-Level Opening(s) Detected",
        max_full_cards=6,
    )

    # Telegram messages are limited to 4096 characters, chunk if needed
    message_chunks = []
    if len(full_text) <= 3800:
        message_chunks.append(full_text)
    else:
        current_chunk = ""
        for block in full_text.split("\n\n"):
            if len(current_chunk) + len(block) + 2 > 3800:
                if current_chunk:
                    message_chunks.append(current_chunk.strip())
                current_chunk = block + "\n\n"
            else:
                current_chunk += block + "\n\n"
        if current_chunk.strip():
            message_chunks.append(current_chunk.strip())

    # Build interactive inline keyboard for displayed jobs
    displayed_jobs = [j for tier in presentation.tiers for j in tier.jobs]
    reply_markup: Optional[dict[str, Any]] = None
    if len(displayed_jobs) == 1:
        reply_markup = build_job_inline_keyboard(displayed_jobs[0])
    elif len(displayed_jobs) <= 8:
        keyboard_rows = []
        for idx, job in enumerate(displayed_jobs, start=1):
            job_id = job.numeric_id if job.numeric_id is not None else job.id
            effective_url, _, _ = resolve_effective_apply_url(job)
            apply_url = str(effective_url) if effective_url else str(job.apply_url)
            row = [
                {"text": f"Apply #{idx}", "url": apply_url},
                {"text": f"Dismiss #{idx}", "callback_data": f"dismiss:{job_id}"},
                {"text": f"Applied #{idx}", "callback_data": f"applied:{job_id}"},
            ]
            keyboard_rows.append(row)
        reply_markup = {"inline_keyboard": keyboard_rows}

    for chunk in message_chunks:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": chunk.strip(),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        try:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                logger.warning("Telegram Bot API returned status %s: %s", resp.status_code, resp.text)
                success = False
        except Exception as exc:
            logger.warning("Failed to send Telegram notification: %s", exc)
            success = False

    return success


def group_jobs_by_company(jobs: list[JobPosting]) -> list[tuple[str, list[JobPosting]]]:
    """Group jobs by company, sorted by highest relevance score first."""
    groups: dict[str, list[JobPosting]] = {}
    for j in jobs:
        comp = j.company
        if comp not in groups:
            groups[comp] = []
        groups[comp].append(j)

    # Sort jobs within each company by relevance_score DESC
    for comp in groups:
        groups[comp].sort(key=lambda j: (getattr(j, "relevance_score", 0) or 0), reverse=True)

    # Sort companies by top job relevance score DESC, then company name
    sorted_companies = sorted(
        groups.items(),
        key=lambda item: (getattr(item[1][0], "relevance_score", 0) or 0, item[0]),
        reverse=True,
    )
    return sorted_companies


async def send_discord_digest(
    webhook_url: str,
    jobs: list[JobPosting],
    client: httpx.AsyncClient,
) -> bool:
    """Send a consolidated daily digest embed to Discord grouped by company and sorted by relevance score."""
    webhook_url = webhook_url.strip() if webhook_url else ""
    if not webhook_url or not jobs:
        return False

    grouped = group_jobs_by_company(jobs)
    embeds: list[dict[str, Any]] = []
    current_embed: dict[str, Any] = {
        "title": f"📬 GCC Job Radar — Daily Digest ({len(jobs)} openings)",
        "color": DISCORD_EMBED_COLOR,
        "fields": [],
        "footer": {"text": "GCC Job Radar • India Tech Daily Digest"},
    }

    for comp, comp_jobs in grouped:
        lines = []
        for j in comp_jobs:
            score = getattr(j, "relevance_score", 0) or 0
            eff_url, _, label = resolve_effective_apply_url(j)
            score_prefix = f"`[{score} pts]` " if score > 0 else ""
            line = f"• {score_prefix}[{j.title}]({eff_url}) — *{j.location}*"
            why_text = getattr(j, "why", None)
            if score >= 20 and why_text:
                line += f"\n  💡 *{why_text}*"
            lines.append(line)

        field_value = "\n".join(lines)
        if len(field_value) > 1024:
            field_value = field_value[:1020] + "..."

        if len(current_embed["fields"]) >= 25:
            embeds.append(current_embed)
            current_embed = {
                "title": "📬 GCC Job Radar — Daily Digest (Continued)",
                "color": DISCORD_EMBED_COLOR,
                "fields": [],
                "footer": {"text": "GCC Job Radar • India Tech Daily Digest"},
            }

        current_embed["fields"].append({
            "name": f"🏢 {comp} ({len(comp_jobs)})",
            "value": field_value,
            "inline": False,
        })

    if current_embed["fields"]:
        embeds.append(current_embed)

    success = True
    for i in range(0, len(embeds), 5):
        chunk = embeds[i : i + 5]
        payload = {
            "content": "⚡ **Daily Entry-Level Tech Digest Ready!**" if i == 0 else "",
            "embeds": chunk,
        }
        try:
            resp = await client.post(webhook_url, json=payload)
            if resp.status_code not in (200, 204):
                logger.warning("Discord digest webhook returned status %s: %s", resp.status_code, resp.text)
                success = False
        except Exception as exc:
            logger.warning("Failed to send Discord digest webhook: %s", exc)
            success = False

    return success


async def send_telegram_digest(
    bot_token: str,
    chat_id: str | int,
    jobs: list[JobPosting],
    client: httpx.AsyncClient,
) -> bool:
    """Send a consolidated daily digest message to Telegram grouped by company and sorted by relevance score."""
    bot_token = bot_token.strip() if bot_token else ""
    chat_id = str(chat_id).strip() if chat_id else ""
    if not bot_token or not chat_id or not jobs:
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    grouped = group_jobs_by_company(jobs)

    presentation = build_ranked_presentation(jobs, max_full_cards=len(jobs))
    strong_suffix = f" ({presentation.strong_count} top picks)" if presentation.strong_count > 0 else ""
    header = (
        f"📬 <b>GCC Job Radar — Daily Digest ({len(jobs)} openings{strong_suffix})</b>\n\n"
        f"{presentation.lead_in}\n\n"
    )
    company_blocks = []

    for comp, comp_jobs in grouped:
        comp_lines = [f"🏢 <b>{html.escape(comp)}</b>"]
        for j in comp_jobs:
            score = getattr(j, "relevance_score", 0) or 0
            score_badge = f"<code>[{score} pts]</code> " if score > 0 else ""
            eff_url, _, _ = resolve_effective_apply_url(j)
            why_text = getattr(j, "why", None)
            line = f"  • {score_badge}<a href=\"{html.escape(str(eff_url))}\">{html.escape(j.title)}</a> — <i>{html.escape(j.location)}</i>"
            if score >= 20 and why_text:
                line += f"\n    💡 <i>{html.escape(why_text)}</i>"
            comp_lines.append(line)
        company_blocks.append("\n".join(comp_lines))

    chunks = []
    current_chunk = header
    for block in company_blocks:
        if len(current_chunk) + len(block) > 3800:
            chunks.append(current_chunk)
            current_chunk = block + "\n\n"
        else:
            current_chunk += block + "\n\n"

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    success = True
    for chunk in chunks:
        payload = {
            "chat_id": chat_id,
            "text": chunk.strip(),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                logger.warning("Telegram Bot API digest returned status %s: %s", resp.status_code, resp.text)
                success = False
        except Exception as exc:
            logger.warning("Failed to send Telegram digest: %s", exc)
            success = False

    return success


async def dispatch_notifications(
    new_jobs: list[JobPosting],
    discord_webhook: Optional[str] = None,
    telegram_token: Optional[str] = None,
    telegram_chat_id: Optional[str] = None,
    db_path: Optional[os.PathLike] = None,
    digest: bool = False,
) -> None:
    """Dispatch notifications to configured channels for new postings, preventing duplicates."""
    if not new_jobs:
        return

    from gcc_job_radar.db import filter_unalerted_jobs, record_dispatched_alerts

    # Ensure we only alert on active/new postings, never on individual jobs already marked applied or dismissed
    new_jobs = [
        j for j in new_jobs
        if getattr(j, "status", "NEW").upper() not in ("APPLIED", "DISMISSED")
    ]
    if not new_jobs:
        return

    # Ensure all jobs have relevance scores and deterministic why rationale, sorted highest first
    for j in new_jobs:
        if not getattr(j, "relevance_score", None) or not getattr(j, "why", None):
            score_job_posting(j)
    new_jobs.sort(key=lambda j: (getattr(j, "relevance_score", 0) or 0), reverse=True)

    # Fallback to environment variables
    discord_url = (discord_webhook or os.getenv("DISCORD_WEBHOOK_URL") or "").strip()
    tg_token = (telegram_token or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    tg_chat = (telegram_chat_id or os.getenv("TELEGRAM_CHAT_ID") or "").strip()

    if not discord_url and not (tg_token and tg_chat):
        return

    # Automated resume tailoring for new postings if GROQ_API_KEY is configured
    groq_api_key = os.getenv("GROQ_API_KEY", "").strip()
    if groq_api_key:
        for job in new_jobs:
            if getattr(job, "tailored_tex_path", None):
                continue
            try:
                tex_path, pdf_path = tailor_resume_for_job(job)
                if tex_path:
                    job.tailored_tex_path = tex_path
                    job.tailored_pdf_path = pdf_path
            except Exception as exc:
                logger.warning("Error during resume tailoring for %s: %s", job.company, exc)
    else:
        logger.debug("GROQ_API_KEY not configured; skipping automated resume tailoring.")

    async with httpx.AsyncClient(timeout=10.0) as client:
        if discord_url:
            # Filter out jobs already alerted to Discord
            discord_jobs = filter_unalerted_jobs(new_jobs, "discord", db_path)
            if discord_jobs:
                if digest:
                    ok = await send_discord_digest(discord_url, discord_jobs, client)
                    label = "digest"
                else:
                    ok = await send_discord_notification(discord_url, discord_jobs, client)
                    label = "alert"
                if ok:
                    record_dispatched_alerts(discord_jobs, "discord", db_path)
                    console.print(f"[bold green][+][/bold green] Sent Discord {label} for {len(discord_jobs)} new posting(s).")
                else:
                    console.print("[bold red][!][/bold red] Failed to send Discord notification.")

        if tg_token and tg_chat:
            # Filter out jobs already alerted to Telegram
            telegram_jobs = filter_unalerted_jobs(new_jobs, "telegram", db_path)
            if telegram_jobs:
                if digest:
                    ok = await send_telegram_digest(tg_token, tg_chat, telegram_jobs, client)
                    label = "digest"
                else:
                    ok = await send_telegram_notification(tg_token, tg_chat, telegram_jobs, client)
                    label = "alert"
                if ok:
                    record_dispatched_alerts(telegram_jobs, "telegram", db_path)
                    console.print(f"[bold green][+][/bold green] Sent Telegram {label} for {len(telegram_jobs)} new posting(s).")
                else:
                    console.print("[bold red][!][/bold red] Failed to send Telegram notification.")
