"""Interactive Telegram bot listener with command handlers and authentication."""

import asyncio
import html
import logging
import os
from pathlib import Path
import re
import sys
from typing import Any, Optional
from dotenv import load_dotenv
import httpx
from rich.console import Console

# Ensure standard streams use utf-8 on Windows consoles to prevent charmap encoding crashes
if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr is not None and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Automatically load environment variables from .env if present
load_dotenv()
from rich.panel import Panel

from gcc_job_radar.ai_agent import ask_ai_agent, clear_chat_history
from gcc_job_radar.config import COMPANIES
from gcc_job_radar.db import (
    filter_new_jobs,
    find_jobs_by_selector,
    get_applied_and_dismissed_companies,
    get_job_by_id,
    get_jobs_by_status,
    get_latest_jobs,
    get_stale_applications,
    get_stats,
    mark_job_status,
    prune_expired_jobs,
    purge_invalid_jobs,
    record_jobs,
)
from gcc_job_radar.scanner import scan_all_companies
from gcc_job_radar.link_resolver import resolve_effective_apply_url
from gcc_job_radar.models import JobPosting, ATSProvider
from gcc_job_radar.notifier import build_job_inline_keyboard
from gcc_job_radar.presentation import (
    build_ranked_presentation,
    format_jobs_html,
    sanitize_telegram_html,
    split_telegram_message,
)
from gcc_job_radar.resume_tailor_bridge import tailor_resume_for_job
from gcc_job_radar.apply_prep import prep_job_application
from gcc_job_radar.display import console

logger = logging.getLogger(__name__)

# Debounce & lock flags to prevent duplicate simultaneous or re-delivered /scan executions
_is_scanning: bool = False
_last_scan_timestamp: float = 0.0
_last_scan_scope: str = ""
_last_scan_label: str = ""

DEFAULT_MENU_COMMANDS: list[dict[str, str]] = [
    {"command": "scan", "description": "Scan GCCs (/scan, /scan new, /scan <co>)"},
    {"command": "search", "description": "Search internet & job boards (/search <profession>)"},
    {"command": "latest", "description": "View 5 latest verified openings"},
    {"command": "email", "description": "Sync 3 email accounts for job alerts"},
    {"command": "accio", "description": "Scan AccioJob portal & hiring drives"},
    {"command": "tailor", "description": "Generate tailored PDF resume for job"},
    {"command": "applied", "description": "View your active applied roles"},
    {"command": "followups", "description": "View stale applications needing follow-up"},
    {"command": "stats", "description": "View database stats & pipeline"},
    {"command": "sync", "description": "Sync database with Turso cloud (/sync [pull|push])"},
    {"command": "help", "description": "Show commands & AI usage guide"},
]


async def sync_telegram_bot_commands(
    bot_token: str,
    client: httpx.AsyncClient,
    commands: Optional[list[dict[str, str]]] = None,
) -> bool:
    """Sync official Telegram bot commands menu via setMyCommands API."""
    url = f"https://api.telegram.org/bot{bot_token}/setMyCommands"
    cmds = commands or DEFAULT_MENU_COMMANDS
    try:
        resp = await client.post(url, json={"commands": cmds}, timeout=10.0)
        if resp.status_code == 200 and resp.json().get("ok"):
            logger.info("Successfully synced %d Telegram bot menu commands.", len(cmds))
            return True
        else:
            logger.warning("Failed to sync Telegram bot commands (status %s): %s", resp.status_code, resp.text)
            return False
    except Exception as exc:
        logger.warning("Error syncing Telegram bot commands: %s", exc)
        return False



async def send_telegram_chat_action(
    bot_token: str, chat_id: str | int, client: httpx.AsyncClient, action: str = "typing"
) -> bool:
    """Send a chat action (e.g. typing) to Telegram."""
    url = f"https://api.telegram.org/bot{bot_token}/sendChatAction"
    try:
        resp = await client.post(url, json={"chat_id": chat_id, "action": action}, timeout=5.0)
        return resp.status_code == 200
    except Exception as exc:
        logger.debug("Failed to send chat action: %s", exc)
        return False


async def send_telegram_reply(
    bot_token: str,
    chat_id: str | int,
    text: str,
    client: httpx.AsyncClient,
    reply_markup: Optional[dict[str, Any]] = None,
) -> bool:
    """Send an HTML-formatted reply to a Telegram chat, auto-splitting messages > 3950 characters."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    chunks = split_telegram_message(text, max_length=3950)
    all_ok = True

    for idx, chunk in enumerate(chunks):
        is_last = (idx == len(chunks) - 1)
        safe_chunk = sanitize_telegram_html(chunk) if chunk else ""
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": safe_chunk or chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if is_last and reply_markup:
            payload["reply_markup"] = reply_markup

        try:
            resp = await client.post(url, json=payload, timeout=12.0)
            if resp.status_code != 200:
                # If Telegram rejects entity parsing (HTML error), retry chunk as plain text
                logger.warning(
                    "Telegram reply chunk failed (status %s): %s. Retrying without HTML parse_mode...",
                    resp.status_code,
                    resp.text,
                )
                payload.pop("parse_mode", None)
                payload["text"] = re.sub(r"<[^>]+>", "", chunk)
                resp_plain = await client.post(url, json=payload, timeout=12.0)
                if resp_plain.status_code != 200:
                    logger.warning(
                        "Failed to send Telegram reply chunk even without HTML: %s (status %s)",
                        resp_plain.text,
                        resp_plain.status_code,
                    )
                    all_ok = False
            if not is_last:
                await asyncio.sleep(0.15)
        except Exception as exc:
            logger.warning("Error sending Telegram reply chunk: %s", exc)
            all_ok = False

    return all_ok


async def send_telegram_document(
    bot_token: str,
    chat_id: str | int,
    file_path: str | Path,
    caption: str,
    client: httpx.AsyncClient,
) -> bool:
    """Send a document (e.g. PDF or LaTeX resume) to a Telegram chat."""
    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    path = Path(file_path)
    if not path.is_file():
        return False

    safe_caption = sanitize_telegram_html(caption) if caption else ""
    try:
        with open(path, "rb") as f:
            file_content = f.read()
        mime_type = "application/pdf" if path.suffix.lower() == ".pdf" else "application/x-tex"
        files = {"document": (path.name, file_content, mime_type)}
        data = {
            "chat_id": str(chat_id),
            "caption": safe_caption or caption,
            "parse_mode": "HTML",
        }
        resp = await client.post(url, data=data, files=files, timeout=30.0)
        if resp.status_code != 200:
            logger.warning("Failed to send Telegram document %s: status %s - %s", path.name, resp.status_code, resp.text)
            return False
        return True
    except Exception as exc:
        logger.warning("Error sending Telegram document %s: %s", path, exc)
        return False


def _dict_to_job_posting(j: dict[str, Any]) -> JobPosting:
    """Convert a database job record dict into a JobPosting model."""
    provider_str = str(j.get("provider", "custom")).lower()
    provider_enum = ATSProvider.CUSTOM
    for p in ATSProvider:
        if p.value.lower() == provider_str:
            provider_enum = p
            break
    return JobPosting(
        id=str(j.get("id", "")),
        numeric_id=j.get("numeric_id"),
        company=str(j.get("company", "")),
        title=str(j.get("title", "")),
        location=str(j.get("location", "")),
        apply_url=str(j.get("apply_url", "")),
        provider=provider_enum,
        published_date=j.get("published_date"),
        description=j.get("notes") or "",
        application_start_date=j.get("application_start_date"),
        application_end_date=j.get("application_end_date"),
        is_expired=bool(j.get("is_expired", False) or j.get("status") == "EXPIRED"),
    )




async def handle_command(
    command_text: str,
    chat_id: str | int,
    bot_token: str,
    allowed_chat_id: str,
    client: httpx.AsyncClient,
    db_path: Optional[Path] = None,
) -> None:
    """Handle incoming Telegram command if chat_id is authorized."""
    if str(chat_id).strip() != str(allowed_chat_id).strip():
        logger.warning("Unauthorized access attempt from chat_id: %s", chat_id)
        await send_telegram_reply(
            bot_token,
            chat_id,
            "⛔ <b>Access Denied</b>: Your Telegram account is not authorized to control this GCC Job Radar bot.",
            client,
        )
        return

    text = command_text.strip()
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("/start", "/help", "/list"):
        help_text = (
            "📋 <b>GCC Radar — Bot Commands Menu</b>\n\n"
            "⚡ <b>Core Actions (In [/] Menu):</b>\n"
            "• <code>/scan</code> — Scan active GCCs for entry-level roles (or <code>/scan all</code>)\n"
            "• <code>/search &lt;role&gt; [in &lt;location&gt;]</code> — Search internet, ATS boards &amp; LinkedIn live\n"
            "• <code>/latest</code> — View 5 latest verified openings with quick buttons\n"
            "• <code>/email</code> — Sync 3 email accounts for job alerts (LinkedIn, Naukri, AccioJob)\n"
            "• <code>/acciojob</code> — Scan AccioJob portal &amp; hiring drives (or <code>/accio</code>)\n"
            "• <code>/tailor &lt;id/company&gt;</code> — Generate tailored LaTeX &amp; PDF resume\n"
            "• <code>/applied</code> — View your active applied roles\n"
            "• <code>/followups</code> — View stale applications needing follow-up (7d+)\n"
            "• <code>/stats</code> — View database stats &amp; pipeline\n"
            "• <code>/sync</code> — Sync database with Turso cloud (<code>/sync [pull|push]</code>)\n"
            "• <code>/help</code> — Show commands &amp; AI usage guide\n\n"
            "🎯 <b>Tracker &amp; Quick Actions:</b>\n"
            "• <code>/check &lt;name&gt;</code> — Check single company live (e.g. <code>/check celonis</code>)\n"
            "• <code>/apply &lt;id/company&gt; [-n note]</code> — Mark job(s) as APPLIED\n"
            "• <code>/dismiss &lt;id(s)/company&gt;</code> — Dismiss job(s) from radar\n"
            "• <code>/dismissed</code> — View your dismissed roles &amp; companies\n"
            "• <code>/restore &lt;id(s)/company&gt;</code> — Restore job(s) back to NEW\n"
            "• <code>/clear</code> — Clear AI conversation memory\n"
            "• <code>/list</code> — Show this commands menu\n\n"
            "💬 <i>You can also ask questions in plain English or ask the AI to find jobs for you!</i>"
        )
        await send_telegram_reply(bot_token, chat_id, help_text, client)


    elif cmd == "/stats":
        stats = get_stats(db_path)
        total = stats.get("total_tracked", 0)
        active = stats.get("active_count", 0)
        needs_resolve = stats.get("needs_resolve_count", 0)
        applied = stats.get("applied_count", 0)
        interviewing = stats.get("interviewing_count", 0)
        rejected = stats.get("rejected_count", 0)
        dismissed = stats.get("dismissed_count", 0)
        first_seen = stats.get("first_recorded") or "N/A"
        last_seen = stats.get("last_active") or "N/A"
        breakdown = stats.get("company_breakdown", {})

        stats_text = (
            "📊 <b>GCC Job Radar - Database Statistics</b>\n\n"
            f"• <b>Total Roles Tracked:</b> {total}\n"
            f"• <b>Active (New):</b> {active}\n"
            f"• <b>Applied:</b> {applied}\n"
            f"• <b>Interviewing:</b> {interviewing}\n"
            f"• <b>Rejected:</b> {rejected}\n"
            f"• <b>Dismissed:</b> {dismissed}\n"
            f"• <b>Needs Resolve:</b> {needs_resolve}\n"
            f"• <b>First Recorded:</b> {first_seen}\n"
            f"• <b>Last Active:</b> {last_seen}\n\n"
        )
        if breakdown:
            stats_text += "<b>Top Tracked Companies:</b>\n"
            for comp, count in list(breakdown.items())[:8]:
                stats_text += f"• {html.escape(comp)}: {count}\n"
        else:
            stats_text += "<i>No postings stored yet.</i>"

        await send_telegram_reply(bot_token, chat_id, stats_text, client)

    elif cmd == "/latest":
        prune_expired_jobs(db_path=db_path)
        show_all_latest = (arg.lower() == "all")
        if show_all_latest:
            limit = 100
            title = "All Latest Discovered Openings"
        elif arg.isdigit():
            limit = max(1, min(100, int(arg)))
            title = f"Latest Discovered Openings ({limit})"
        else:
            limit = 5
            title = "Latest Discovered Openings"

        recent_jobs = get_latest_jobs(limit=limit, status="NEW", db_path=db_path)
        if not recent_jobs:
            reply = format_jobs_html([], title)
            await send_telegram_reply(bot_token, chat_id, reply, client)
        elif len(recent_jobs) == 1:
            reply = format_jobs_html(recent_jobs, title)
            markup = build_job_inline_keyboard(recent_jobs[0])
            await send_telegram_reply(bot_token, chat_id, reply, client, reply_markup=markup)
        else:
            reply = format_jobs_html(recent_jobs, title, max_full_cards=5)
            keyboard = []
            for idx, rj in enumerate(recent_jobs[:5], start=1):
                jid = rj.get("numeric_id") or rj.get("id")
                eff_url, _, _ = resolve_effective_apply_url(rj)
                comp = (rj.get("company") or "")[:12]
                keyboard.append([
                    {"text": f"Apply #{idx} ({comp})", "url": str(eff_url)},
                    {"text": f"Dismiss #{idx}", "callback_data": f"dismiss:{jid}"},
                    {"text": f"Applied #{idx}", "callback_data": f"applied:{jid}"},
                ])
            await send_telegram_reply(
                bot_token, chat_id, reply, client, reply_markup={"inline_keyboard": keyboard}
            )

    elif cmd in ("/applied", "/applications", "applied", "applications", "applies"):
        applied_jobs = get_jobs_by_status("APPLIED", db_path=db_path)
        if not applied_jobs:
            reply = (
                "ℹ️ <b>No Applied Roles Recorded</b>\n\n"
                "You haven't marked any roles as applied yet.\n"
                "Use <code>/apply &lt;id or company&gt;</code> to track your applications!"
            )
        else:
            reply = format_jobs_html(applied_jobs, f"Your Applied Listings ({len(applied_jobs)})")
        await send_telegram_reply(bot_token, chat_id, reply, client)

    elif cmd in ("/followups", "/stale"):
        days = 7
        if arg and arg.isdigit():
            days = int(arg)
        stale_jobs = get_stale_applications(days=days, db_path=db_path)
        if not stale_jobs:
            reply = (
                f"🎉 <b>No Stale Applications!</b>\n\n"
                f"All your applied roles are either submitted under {days} days ago or updated.\n"
                f"Keep up the momentum!"
            )
        else:
            lines = [f"⏳ <b>Pending Follow-up (Applied &gt;= {days}d ago) — {len(stale_jobs)} role(s):</b>\n"]
            for idx, j in enumerate(stale_jobs[:10], start=1):
                jid = j.get("numeric_id") or j.get("id")
                comp = html.escape(str(j.get("company", "")))
                title = html.escape(str(j.get("title", "")))
                elapsed = j.get("days_elapsed", 0)
                applied_date = str(j.get("applied_at", ""))[:10]
                eff_url, _, _ = resolve_effective_apply_url(j)
                notes = j.get("notes")
                notes_str = f" | <i>Note: {html.escape(str(notes))}</i>" if notes else ""
                lines.append(
                    f"{idx}. <b>{comp}</b> — {title} (#{jid})\n"
                    f"   🗓️ Applied: <code>{applied_date}</code> (<b>{elapsed} days ago</b>){notes_str}\n"
                    f"   🔗 <a href=\"{eff_url}\">Outreach / Portal Link</a>"
                )
            reply = "\n\n".join(lines)
        await send_telegram_reply(bot_token, chat_id, reply, client)

    elif cmd == "/check":
        if not arg:
            await send_telegram_reply(
                bot_token,
                chat_id,
                "⚠️ Please provide a company name, e.g. <code>/check celonis</code> or <code>/check databricks</code>",
                client,
            )
            return

        query = arg.lower()
        matched_companies = [
            c for c in COMPANIES if query in c.name.lower() or query in c.board_token.lower()
        ]
        if not matched_companies:
            await send_telegram_reply(
                bot_token,
                chat_id,
                f"❌ Company matching '<code>{html.escape(arg)}</code>' not found in registry.",
                client,
            )
            return

        await send_telegram_reply(
            bot_token,
            chat_id,
            f"🔍 Scanning <b>{html.escape(matched_companies[0].name)}</b> ATS...",
            client,
        )
        jobs = await scan_all_companies(companies=matched_companies)
        record_jobs(jobs, db_path)
        reply = format_jobs_html(jobs, f"Results for {matched_companies[0].name}")
        await send_telegram_reply(bot_token, chat_id, reply, client)

    elif cmd in ("/search", "/find"):
        if not arg:
            usage_msg = (
                "⚠️ <b>Usage:</b> <code>/search &lt;profession/role&gt; [in &lt;location&gt;]</code>\n\n"
                "<b>Examples:</b>\n"
                "• <code>/search Java Developer in Bangalore</code>\n"
                "• <code>/search Backend Engineer Remote</code>\n"
                "• <code>/search Spring Boot Developer</code>\n"
                "• <code>/search SDE 1 India</code>\n\n"
                "<i>Searches live ATS endpoints (Greenhouse, Lever, Ashby, Workday), LinkedIn, and tech feeds on-demand with match scoring!</i>"
            )
            await send_telegram_reply(bot_token, chat_id, usage_msg, client)
            return

        raw_query = arg.strip()
        loc = "India"
        m_loc = re.search(r"\b(?:in|at|near)\s+([A-Za-z\s]+)$", raw_query, re.IGNORECASE)
        if m_loc:
            loc = m_loc.group(1).strip()
            clean_q = raw_query[:m_loc.start()].strip()
        elif "remote" in raw_query.lower():
            loc = "Remote"
            clean_q = raw_query
        else:
            clean_q = raw_query

        if not clean_q:
            clean_q = raw_query

        await send_telegram_reply(
            bot_token,
            chat_id,
            f"🌐 <i>Searching live internet, ATS boards (Greenhouse, Lever, Ashby, Workday) and LinkedIn for <b>{html.escape(clean_q)}</b> in <b>{html.escape(loc)}</b>...</i>",
            client,
        )

        stop_typing = asyncio.Event()
        typing_task = asyncio.create_task(
            _keep_typing_action(bot_token, chat_id, client, stop_typing)
        )
        try:
            from gcc_job_radar.internet_search import (
                format_internet_search_html,
                search_internet_jobs,
            )

            found_jobs = await search_internet_jobs(
                query=clean_q,
                location=loc,
                limit=8,
                db_path=db_path,
            )
            reply = format_internet_search_html(clean_q, loc, found_jobs)

            if found_jobs:
                keyboard = []
                for idx, rj in enumerate(found_jobs[:4], start=1):
                    jid = rj.numeric_id or rj.id
                    eff_url, _, _ = resolve_effective_apply_url(rj)
                    comp = (rj.company or "")[:12]
                    keyboard.append([
                        {"text": f"Apply #{idx} ({comp})", "url": str(eff_url)},
                        {"text": f"Dismiss #{idx}", "callback_data": f"dismiss:{jid}"},
                        {"text": f"Applied #{idx}", "callback_data": f"applied:{jid}"},
                    ])
                await send_telegram_reply(
                    bot_token,
                    chat_id,
                    reply,
                    client,
                    reply_markup={"inline_keyboard": keyboard},
                )
            else:
                await send_telegram_reply(bot_token, chat_id, reply, client)
        except Exception as exc:
            logger.exception("Error searching internet jobs")
            clean_err = re.sub(r"\[/?(bold|dim|cyan|red)[^\]]*\]", "", str(exc))
            reply = f"❌ Error performing internet job search: {html.escape(clean_err)}"
            await send_telegram_reply(bot_token, chat_id, reply, client)
        finally:
            stop_typing.set()
            typing_task.cancel()
            try:
                await typing_task
            except (asyncio.CancelledError, Exception):
                pass

    elif cmd == "/scan":
        global _is_scanning, _last_scan_timestamp, _last_scan_scope, _last_scan_label
        import time

        now = time.time()
        if _is_scanning:
            await send_telegram_reply(
                bot_token,
                chat_id,
                "⏳ <i>A scan is currently already running. Please wait for it to complete.</i>",
                client,
            )
            return

        arg_clean = arg.strip().lower()

        # Debounce: if the exact same scan or a full scan was completed less than 10 seconds ago
        is_same_scope = (arg_clean == _last_scan_scope)
        is_recent_full_scan = (_last_scan_scope in ("", "all", "--all", "-a"))
        if (now - _last_scan_timestamp < 10) and (is_same_scope or is_recent_full_scan):
            prior_name = _last_scan_label or "companies"
            await send_telegram_reply(
                bot_token,
                chat_id,
                f"⚡ <i>A scan for {prior_name} was just completed seconds ago. Please wait a few moments before scanning again.</i>",
                client,
            )
            return

        show_all = arg_clean in ("all", "--all", "-a")
        target_companies = COMPANIES
        scan_title_label = f"all <b>{len(COMPANIES)}</b> foreign GCCs & tech centers in India"
        is_custom_scope = False

        m_new = re.match(r"^(?:new|recent)(?:\s+(\d+))?$", arg_clean)
        if m_new:
            count_str = m_new.group(1)
            count = int(count_str) if count_str else 50
            count = max(1, min(count, len(COMPANIES)))
            target_companies = COMPANIES[-count:]
            scan_title_label = f"the last <b>{count}</b> newly added companies"
            is_custom_scope = True
        elif show_all:
            target_companies = COMPANIES
            scan_title_label = f"all <b>{len(COMPANIES)}</b> foreign GCCs & tech centers in India (including applied/dismissed)"
        elif arg_clean:
            matched = [
                c for c in COMPANIES
                if arg_clean in c.name.lower() or arg_clean in c.board_token.lower()
            ]
            if matched:
                target_companies = matched
                scan_title_label = f"<b>{html.escape(matched[0].name)}</b>"
                is_custom_scope = True
            else:
                await send_telegram_reply(
                    bot_token,
                    chat_id,
                    f"❌ Company matching '<code>{html.escape(arg)}</code>' not found in registry ({len(COMPANIES)} companies).\n\n"
                    f"💡 <i>Tip: Use <code>/scan new</code> to scan recently added companies, or <code>/scan</code> for all.</i>",
                    client,
                )
                return

        _is_scanning = True
        try:
            scan_header = f"⚡ Initiating scan across {scan_title_label}..."
            await send_telegram_reply(
                bot_token,
                chat_id,
                scan_header,
                client,
            )
            jobs = await scan_all_companies(companies=target_companies)
            prune_expired_jobs(db_path=db_path)
            new_jobs, _ = filter_new_jobs(jobs, db_path)
            record_jobs(jobs, db_path)

            applied_comps, dismissed_comps = get_applied_and_dismissed_companies(db_path)
            excluded_comps = applied_comps | dismissed_comps if not show_all else set()

            display_jobs = [
                j
                for j in jobs
                if (show_all or j.company.lower().strip() not in excluded_comps)
                and (show_all or getattr(j, "status", "NEW").upper() not in ("APPLIED", "DISMISSED"))
            ]
            hidden_count = len(jobs) - len(display_jobs)

            title_hdr = (
                f"Verified Active Openings ({scan_title_label.replace('<b>', '').replace('</b>', '')})"
                if is_custom_scope
                else ("All Verified Active Openings (Including Applied/Dismissed)" if show_all else "Verified Active Entry-Level Openings")
            )

            if display_jobs:
                reply = format_jobs_html(display_jobs, title_hdr)
                if hidden_count > 0 and not show_all:
                    reply += (
                        f"\n\n<i>💡 {hidden_count} role(s) from already applied or dismissed companies were hidden. "
                        f"Use <code>/scan all</code> to view all companies.</i>"
                    )
            else:
                if hidden_count > 0 and not show_all:
                    reply = (
                        "ℹ️ <b>Scan Complete</b>\n\n"
                        f"No new unapplied or undismissed roles currently open across {scan_title_label}.\n\n"
                        f"<i>💡 {hidden_count} active role(s) from companies you already applied to or dismissed were hidden. "
                        f"Use <code>/scan all</code> to view all companies.</i>"
                    )
                else:
                    reply = (
                        "ℹ️ <b>Scan Complete</b>\n\n"
                        f"No entry-level tech roles currently open matching strict criteria across {scan_title_label}."
                    )
            await send_telegram_reply(bot_token, chat_id, reply, client)
        finally:
            _is_scanning = False
            _last_scan_timestamp = time.time()
            _last_scan_scope = arg_clean
            _last_scan_label = scan_title_label

    elif cmd in ("/clear", "/reset"):
        clear_chat_history(chat_id)
        await send_telegram_reply(
            bot_token,
            chat_id,
            "🧹 <b>Chat history cleared.</b> How can I help you find GCC roles?",
            client,
        )

    elif cmd in ("/dismiss", "/hide", "/dimsiss", "/dimiss", "/dissmiss", "/dismis", "/dsmiss"):
        if not arg:
            await send_telegram_reply(
                bot_token,
                chat_id,
                "⚠️ <b>Usage:</b> <code>/dismiss &lt;id(s) or company&gt;</code>\n\n"
                "Examples:\n"
                "• <code>/dismiss 1</code>\n"
                "• <code>/dismiss 1, 2, 4</code>\n"
                "• <code>/dismiss wysa, katalystcs, tvaram, WSP, betterworks</code>",
                client,
            )
            return

        from gcc_job_radar.db import dismiss_selectors_or_companies

        res = dismiss_selectors_or_companies(arg, db_path=db_path)
        dismissed_jobs = res.get("dismissed_jobs", [])
        adhoc_comps = res.get("dismissed_adhoc", [])
        all_comps = res.get("dismissed_companies", [])

        if not dismissed_jobs and not adhoc_comps:
            await send_telegram_reply(
                bot_token,
                chat_id,
                f"❌ No valid targets provided in '<code>{html.escape(arg)}</code>'.",
                client,
            )
            return

        items = []
        for j in dismissed_jobs:
            rowid = j.get("numeric_id") or j.get("id")
            items.append(
                f"• <b>#{rowid}. {html.escape(j.get('company', 'Unknown'))}</b> — {html.escape(j.get('title', 'Role'))} <i>(Job Dismissed)</i>"
            )

        for a in adhoc_comps:
            cname = html.escape(str(a.get("company", "Company")))
            items.append(
                f"• 🏢 <b>{cname}</b> — <i>Current opening dismissed (future roles will still be monitored)</i>"
            )

        total = len(dismissed_jobs) + len(adhoc_comps)
        comp_str = f" across {len(all_comps)} company/companies" if all_comps else ""
        if dismissed_jobs and not adhoc_comps:
            header = f"🗑️ <b>Dismissed {len(dismissed_jobs)} Job(s):</b>\n\n"
        elif not dismissed_jobs and adhoc_comps:
            header = f"🗑️ <b>Dismissed {len(adhoc_comps)} Role Target(s):</b>\n\n"
        else:
            header = f"🗑️ <b>Dismissed {total} Target(s){comp_str}:</b>\n\n"
        reply = (
            header
            + "\n".join(items)
            + "\n\n<i>💡 These specific roles won't be shown again, but we will continue searching and alert you whenever these companies post new openings matching your criteria! Use <code>/restore &lt;id or company&gt;</code> to undo.</i>"
        )
        await send_telegram_reply(bot_token, chat_id, reply, client)

    elif cmd == "/apply":
        if not arg:
            await send_telegram_reply(
                bot_token,
                chat_id,
                "⚠️ <b>Usage:</b> <code>/apply &lt;id(s) or company&gt; [-n optional note]</code>\n\n"
                "Examples:\n"
                "• <code>/apply 2</code>\n"
                "• <code>/apply BT Group</code>\n"
                "• <code>/apply 2 -n Applied via official ATS</code>",
                client,
            )
            return

        notes = None
        target_selector = arg
        m_note = re.search(r"(?:-n|--notes)\s+(.+)$", arg, flags=re.IGNORECASE)
        if m_note:
            notes = m_note.group(1).strip().strip('"').strip("'")
            target_selector = arg[: m_note.start()].strip()

        jobs = find_jobs_by_selector(target_selector, db_path=db_path)
        if not jobs:
            clean_sel = re.sub(r"^#", "", target_selector).strip()
            is_numeric = clean_sel.isdigit() or (
                clean_sel
                and all(t.isdigit() for t in re.split(r"[,;\s]+", clean_sel) if t)
            )
            if not is_numeric:
                from gcc_job_radar.ai_agent import parse_apply_target
                from gcc_job_radar.db import record_manual_job

                comp, tit, parsed_notes = parse_apply_target(target_selector)
                eff_notes = notes or parsed_notes
                adhoc_job = record_manual_job(
                    company=comp,
                    title=tit,
                    status="APPLIED",
                    notes=eff_notes,
                    db_path=db_path,
                )
                if adhoc_job:
                    rowid = adhoc_job.get("numeric_id") or adhoc_job.get("id")
                    effective_url, _, _ = resolve_effective_apply_url(adhoc_job)
                    link_html = f' • <a href="{html.escape(str(effective_url))}">Apply Link</a>' if effective_url else ""
                    notes_msg = f"\n📝 <b>Notes:</b> <i>{html.escape(eff_notes)}</i>" if eff_notes else ""
                    reply = (
                        f"✅ <b>Marked as APPLIED (1):</b>\n\n"
                        f"• <b>#{rowid}. {html.escape(adhoc_job.get('company', comp))}</b> — {html.escape(adhoc_job.get('title', tit))}{link_html}"
                        + notes_msg
                        + "\n\n<i>Application recorded in tracker database. Good luck!</i>"
                    )
                    await send_telegram_reply(bot_token, chat_id, reply, client)
                    return

            await send_telegram_reply(
                bot_token,
                chat_id,
                f"❌ No jobs found matching '<code>{html.escape(target_selector)}</code>'.\nUse <code>/latest</code> to check active job IDs.",
                client,
            )
            return

        applied_list = []
        for j in jobs:
            rowid = j.get("numeric_id") or j.get("id")
            mark_job_status(job_id=rowid, status="APPLIED", notes=notes, db_path=db_path)
            effective_url, _, label = resolve_effective_apply_url(j)
            link_html = f' • <a href="{html.escape(str(effective_url))}">Apply Link</a>' if effective_url else ""
            applied_list.append(
                f"• <b>#{rowid}. {html.escape(j.get('company', 'Unknown'))}</b> — {html.escape(j.get('title', 'Role'))}{link_html}"
            )

        notes_msg = f"\n📝 <b>Notes:</b> <i>{html.escape(notes)}</i>" if notes else ""
        reply = (
            f"✅ <b>Marked as APPLIED ({len(applied_list)}):</b>\n\n"
            + "\n".join(applied_list)
            + notes_msg
            + "\n\n<i>Application timestamp recorded in database. Good luck!</i>"
        )
        await send_telegram_reply(bot_token, chat_id, reply, client)

    elif cmd in ("/restore", "/undismiss"):
        if not arg:
            await send_telegram_reply(
                bot_token,
                chat_id,
                "⚠️ <b>Usage:</b> <code>/restore &lt;id(s) or company&gt;</code>\n\n"
                "Examples:\n"
                "• <code>/restore 1</code>\n"
                "• <code>/restore 1, 2</code>\n"
                "• <code>/restore Devmani Traders</code>",
                client,
            )
            return

        jobs = find_jobs_by_selector(arg, db_path=db_path)
        if not jobs:
            await send_telegram_reply(
                bot_token,
                chat_id,
                f"❌ No jobs found matching '<code>{html.escape(arg)}</code>'.",
                client,
            )
            return

        restored_list = []
        for j in jobs:
            rowid = j.get("numeric_id") or j.get("id")
            mark_job_status(job_id=rowid, status="NEW", db_path=db_path)
            restored_list.append(
                f"• <b>#{rowid}. {html.escape(j.get('company', 'Unknown'))}</b> — {html.escape(j.get('title', 'Role'))}"
            )

        reply = (
            f"🔄 <b>Restored {len(restored_list)} Job(s) to NEW:</b>\n\n"
            + "\n".join(restored_list)
            + "\n\n<i>These postings will now appear in scans and active listings again.</i>"
        )
        await send_telegram_reply(bot_token, chat_id, reply, client)

    elif cmd in ("/tailor", "/resume"):
        if not arg:
            await send_telegram_reply(
                bot_token,
                chat_id,
                "⚠️ <b>Usage:</b> <code>/tailor &lt;id or company&gt;</code>\n\n"
                "Examples:\n"
                "• <code>/tailor 1</code>\n"
                "• <code>/tailor Flipkart</code>\n"
                "• <code>/tailor Amazon</code>\n\n"
                "<i>Use <code>/latest</code> to find job IDs from recent findings.</i>",
                client,
            )
            return

        jobs = find_jobs_by_selector(arg, db_path=db_path)
        if not jobs:
            await send_telegram_reply(
                bot_token,
                chat_id,
                f"❌ No jobs found matching '<code>{html.escape(arg)}</code>'.\nUse <code>/latest</code> to check active job IDs.",
                client,
            )
            return

        target_job = jobs[0]
        comp_name = html.escape(str(target_job.get("company", "Unknown")))
        role_title = html.escape(str(target_job.get("title", "Role")))
        jid = target_job.get("numeric_id") or target_job.get("id")

        await send_telegram_chat_action(bot_token, chat_id, client, action="upload_document")
        await send_telegram_reply(
            bot_token,
            chat_id,
            f"⏳ <b>Tailoring resume for #{jid} {comp_name} — {role_title}...</b>\n"
            f"<i>Using Groq AI &amp; LaTeX template to customize bullets and skills. Please wait...</i>",
            client,
        )

        job_obj = _dict_to_job_posting(target_job)
        try:
            tailor_res = await asyncio.to_thread(tailor_resume_for_job, job_obj, force=True)
            tex_path, pdf_path = tailor_res[0], tailor_res[1]
            gap_note = getattr(tailor_res, "gap_note", None)
        except Exception as exc:
            logger.error("Error during on-demand resume tailoring for %s: %s", comp_name, exc)
            tex_path, pdf_path, gap_note = None, None, None

        if not tex_path and not pdf_path:
            await send_telegram_reply(
                bot_token,
                chat_id,
                f"⚠️ <b>Tailoring Failed for {comp_name}</b>\n\n"
                f"Could not generate tailored resume. Please verify that <code>GROQ_API_KEY</code> is configured in <code>.env</code>.",
                client,
            )
            return

        pdf_sent = False
        if pdf_path and Path(pdf_path).is_file():
            effective_url, _, _ = resolve_effective_apply_url(target_job)
            apply_link = f'\n🔗 <a href="{html.escape(str(effective_url))}">Direct Apply Link</a>' if effective_url else ""
            gap_caption = f"\n💡 {html.escape(str(gap_note))}" if gap_note else ""
            caption = (
                f"📄 <b>Tailored Resume — {comp_name}</b>\n"
                f"💼 {role_title}\n"
                f"📍 {html.escape(str(target_job.get('location', 'India')))}"
                f"{apply_link}"
                f"{gap_caption}"
            )
            pdf_sent = await send_telegram_document(
                bot_token, chat_id, pdf_path, caption=caption, client=client
            )

        if not pdf_sent:
            pdf_str = f"\n📄 <b>PDF:</b> <code>{html.escape(str(pdf_path))}</code>" if pdf_path else ""
            gap_str = f"\n💡 <i>{html.escape(str(gap_note))}</i>\n" if gap_note else ""
            reply = (
                f"✅ <b>Tailored Resume Generated!</b>\n\n"
                f"• <b>Company:</b> {comp_name}\n"
                f"• <b>Role:</b> {role_title}\n"
                f"• <b>LaTeX Source:</b> <code>{html.escape(str(tex_path))}</code>"
                f"{pdf_str}"
                f"{gap_str}\n\n"
                f"<i>Files saved in your <code>tailored/</code> workspace folder.</i>"
            )
            await send_telegram_reply(bot_token, chat_id, reply, client)

    elif cmd in ("/dismissed", "/hidden"):
        all_dismissed = get_jobs_by_status("DISMISSED", db_path=db_path)
        total = len(all_dismissed)
        if not total:
            await send_telegram_reply(
                bot_token,
                chat_id,
                "ℹ️ <b>No Dismissed Roles</b>\n\nYou have not dismissed any roles yet. Use <code>/dismiss &lt;id or company&gt;</code> to dismiss roles.",
                client,
            )
            return

        companies = sorted({j.get("company", "Unknown") for j in all_dismissed})
        comp_summary = ", ".join(companies)

        cards = []
        for j in all_dismissed[:25]:
            jid = j.get("numeric_id") or j.get("id")
            cname = html.escape(j.get("company", "Unknown"))
            title = html.escape(j.get("title", "Role"))
            loc = html.escape(j.get("location", ""))
            loc_str = f" • {loc}" if loc else ""
            cards.append(f"• <b>#{jid}. {cname}</b> — {title}{loc_str}")

        reply = (
            f"🗑️ <b>Dismissed Roles ({total} total across {len(companies)} companies):</b>\n\n"
            f"🏢 <b>All {len(companies)} Dismissed Companies:</b>\n"
            f"<i>{html.escape(comp_summary)}</i>\n\n"
            f"<b>Recent Dismissed Roles ({len(cards)} shown):</b>\n"
            + "\n".join(cards)
            + "\n\n💡 <i>Use <code>/restore &lt;id or company&gt;</code> to undo dismissal.</i>"
        )
        await send_telegram_reply(bot_token, chat_id, reply, client)

    elif cmd == "/applied":
        applied_jobs = get_jobs_by_status("APPLIED", db_path=db_path)
        if not applied_jobs:
            await send_telegram_reply(
                bot_token,
                chat_id,
                "ℹ️ <b>No Applied Roles Recorded</b>\n\nYou haven't marked any roles as applied yet. Use <code>/apply &lt;id or company&gt;</code>.",
                client,
            )
            return

        reply = format_jobs_html(applied_jobs, f"Your Applied Listings ({len(applied_jobs)})")
        await send_telegram_reply(bot_token, chat_id, reply, client)

    elif cmd in ("/email", "/emails", "/sync_emails"):
        try:
            from tools.ingest_email import (
                MissingEmailCredentialsError,
                get_configured_email_accounts,
                sync_email_alerts,
            )

            accounts = get_configured_email_accounts(env_path=Path(".env"))
        except MissingEmailCredentialsError:
            reply = (
                "⚠️ <b>Email Alerts: Missing Credentials on Render</b>\n\n"
                "To scan job alerts directly from Telegram, set your credentials in the <b>Render Dashboard</b> (Environment tab):\n\n"
                "• <code>EMAIL_IMAP_SERVER</code> = <code>imap.gmail.com</code>\n"
                "• <code>EMAIL_USER</code> = <i>your_email@gmail.com</i>\n"
                "• <code>EMAIL_PASSWORD</code> = <i>xxxx xxxx xxxx xxxx</i> (16-char Google App Password)\n\n"
                "<i>For multiple accounts, add:</i>\n"
                "• <code>EMAIL_USER_2</code>, <code>EMAIL_PASSWORD_2</code>\n"
                "• <code>EMAIL_USER_3</code>, <code>EMAIL_PASSWORD_3</code>"
            )
            await send_telegram_reply(bot_token, chat_id, reply, client)
            return

        acc_count = len(accounts)
        scan_msg = (
            f"📬 <i>Scanning your 3 configured email accounts for job alerts (LinkedIn, Naukri, Indeed, Glassdoor, AccioJob)...</i>"
            if acc_count == 3
            else f"📬 <i>Scanning your {acc_count} configured email account{'s' if acc_count > 1 else ''} for job alerts (LinkedIn, Naukri, Indeed, Glassdoor, AccioJob)...</i>"
        )
        await send_telegram_reply(bot_token, chat_id, scan_msg, client)

        stop_typing = asyncio.Event()
        typing_task = asyncio.create_task(
            _keep_typing_action(bot_token, chat_id, client, stop_typing)
        )
        try:
            jobs = await asyncio.wait_for(
                asyncio.to_thread(
                    sync_email_alerts,
                    days=7,
                    limit=10,
                    unread_only=False,
                    db_path=db_path,
                    notify=False,
                ),
                timeout=120.0,
            )
            if jobs:
                reply = format_jobs_html(jobs, f"New Email Job Alerts ({len(jobs)})")
            else:
                acc_list = "\n".join(f"• <code>{html.escape(u)}</code>" for u, _ in accounts)
                reply = (
                    f"ℹ️ <b>Email Ingestion Complete</b>\n\n"
                    f"Checked {len(accounts)} email account(s):\n{acc_list}\n\n"
                    f"No new unrecorded entry-level tech job alerts found in the last 7 days."
                )
        except asyncio.TimeoutError:
            logger.warning("Email alert sync timed out after 120s")
            reply = (
                "⏳ <b>Email Sync Timed Out</b>\n\n"
                "Scanning took longer than 2 minutes. The mailboxes have many messages; "
                "processed jobs have been saved to your database. Try running again or check <code>/latest</code>."
            )
        except Exception as exc:
            logger.exception("Error syncing email alerts")
            clean_err = re.sub(r"\[/?(bold|dim|cyan|red)[^\]]*\]", "", str(exc))
            reply = f"❌ Error checking email accounts: {html.escape(clean_err)}"
        finally:
            stop_typing.set()
            typing_task.cancel()
            try:
                await typing_task
            except (asyncio.CancelledError, Exception):
                pass
        await send_telegram_reply(bot_token, chat_id, reply, client)

    elif cmd in ("/accio", "/acciojob"):
        await send_telegram_chat_action(bot_token, chat_id, client, "typing")
        if arg:
            await send_telegram_reply(
                bot_token,
                chat_id,
                "⚡ <i>Parsing AccioJob digest text and registering verified hiring companies...</i>",
                client,
            )
            try:
                from tools.ingest_acciojob import run_acciojob_pipeline
                new_jobs = await run_acciojob_pipeline(raw_text=arg, scrape=False, db_path=db_path)
                if new_jobs:
                    reply = format_jobs_html(new_jobs, f"AccioJob Digest Roles ({len(new_jobs)})")
                else:
                    reply = (
                        "ℹ️ <b>AccioJob Ingestion Complete</b>\n\n"
                        "No new unrecorded tech roles matching your profile (Java/Spring, React, Node, SQL, 0-2 YOE) found in the text."
                    )
            except Exception as exc:
                logger.exception("Error ingesting AccioJob text")
                clean_err = re.sub(r"\[/?(bold|dim|cyan|red)[^\]]*\]", "", str(exc))
                reply = f"❌ Error processing AccioJob digest: {html.escape(clean_err)}"
            await send_telegram_reply(bot_token, chat_id, reply, client)
        else:
            await send_telegram_reply(
                bot_token,
                chat_id,
                "🌐 <i>Scanning AccioJob career portal &amp; fresh opportunities...</i>",
                client,
            )
            try:
                from tools.ingest_acciojob import run_acciojob_pipeline
                jobs = await run_acciojob_pipeline(scrape=True, db_path=db_path)
                if jobs:
                    reply = format_jobs_html(jobs, f"AccioJob Verified Roles ({len(jobs)})")
                else:
                    reply = (
                        "ℹ️ <b>AccioJob Scan Complete</b>\n\n"
                        "No new unrecorded entry-level tech opportunities found on the AccioJob portal right now.\n"
                        "<i>Tip: You can also paste an AccioJob digest table using <code>/acciojob &lt;paste text&gt;</code>!</i>"
                    )
            except Exception as exc:
                logger.exception("Error scanning AccioJob portal")
                clean_err = re.sub(r"\[/?(bold|dim|cyan|red)[^\]]*\]", "", str(exc))
                reply = f"❌ Error scanning AccioJob portal: {html.escape(clean_err)}"
            await send_telegram_reply(bot_token, chat_id, reply, client)

    elif cmd in ("/sync", "/dbsync"):
        from gcc_job_radar.turso_sync import is_turso_configured, sync_turso
        if not is_turso_configured():
            reply = (
                "⚠️ <b>Turso Cloud Database Not Configured</b>\n\n"
                "Cloud synchronization requires Turso credentials.\n\n"
                "Please configure these environment variables in your <b>Render Dashboard</b> (Environment tab):\n"
                "• <code>TURSO_DATABASE_URL</code>\n"
                "• <code>TURSO_AUTH_TOKEN</code>\n\n"
                "<i>Once saved, the bot automatically syncs your applications &amp; dismissals!</i>"
            )
            await send_telegram_reply(bot_token, chat_id, reply, client)
            return

        direction = "push" if arg.strip().lower() in ("push", "upload") else "pull"
        await send_telegram_reply(
            bot_token,
            chat_id,
            f"🔄 <i>Syncing with Turso cloud database (direction: <b>{direction.upper()}</b>)...</i>",
            client,
        )

        try:
            res = await asyncio.to_thread(sync_turso, db_path=db_path, direction=direction)
            if res.get("status") == "error":
                reply = f"❌ <b>Turso Sync Failed:</b> {html.escape(str(res.get('error')))}"
            else:
                stats = get_stats(db_path)
                total = stats.get("total_tracked", 0)
                applied = stats.get("applied_count", 0)
                dismissed = stats.get("dismissed_count", 0)
                table_stats = res.get("stats", {})

                synced_summary = ", ".join(f"<code>{k}</code>: {v}" for k, v in table_stats.items() if v > 0)
                if not synced_summary:
                    synced_summary = "All tables up to date"

                reply = (
                    f"✅ <b>Turso Cloud Sync Completed!</b>\n\n"
                    f"• <b>Direction:</b> <code>{direction.upper()}</code>\n"
                    f"• <b>Tables Synced:</b> {synced_summary}\n\n"
                    f"📊 <b>Current Local State:</b>\n"
                    f"• <b>Total Roles Tracked:</b> {total}\n"
                    f"• <b>Applied:</b> {applied}\n"
                    f"• <b>Dismissed:</b> {dismissed}"
                )
        except Exception as exc:
            logger.exception("Manual Turso sync error: %s", exc)
            reply = f"❌ <b>Turso Sync Error:</b> {html.escape(str(exc))}"

        await send_telegram_reply(bot_token, chat_id, reply, client)

    else:
        await send_telegram_reply(
            bot_token,
            chat_id,
            "❓ Unknown command. Send <code>/help</code> to see available commands.",
            client,
        )



async def handle_callback_query(
    callback_query: dict[str, Any],
    bot_token: str,
    allowed_chat_id: str,
    client: httpx.AsyncClient,
    db_path: Optional[Path] = None,
) -> bool:
    """Handle incoming Telegram callback queries from interactive inline keyboards."""
    cb_id = callback_query.get("id")
    from_user = callback_query.get("from", {})
    user_id = str(from_user.get("id", "")).strip()
    data = (callback_query.get("data") or "").strip()
    message = callback_query.get("message", {})
    chat = message.get("chat", {})
    chat_id = str(chat.get("id") or user_id).strip()
    message_id = message.get("message_id")

    # 1. Access authorization
    if chat_id != str(allowed_chat_id).strip() and user_id != str(allowed_chat_id).strip():
        logger.warning("Unauthorized callback attempt from user_id: %s, chat_id: %s", user_id, chat_id)
        if cb_id:
            try:
                await client.post(
                    f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery",
                    json={"callback_query_id": cb_id, "text": "⛔ Access Denied.", "show_alert": True},
                    timeout=5.0,
                )
            except Exception as exc:
                logger.debug("Failed to answer unauthorized callback: %s", exc)
        return False

    # 2. Handle Dismiss: "dismiss:{job_id}"
    if data.startswith("dismiss:"):
        job_id = data.split("dismiss:", 1)[1].strip()
        mark_job_status(job_id=job_id, status="DISMISSED", db_path=db_path)
        target_job = get_job_by_id(job_id, db_path=db_path)

        comp = target_job["company"] if target_job else "Job"
        pos_title = target_job["title"] if target_job else f"#{job_id}"
        loc = target_job.get("location", "India") if target_job else ""

        updated_text = (
            f"❌ <b>[DISMISSED]</b> <s>{html.escape(comp)} — {html.escape(pos_title)}</s>\n"
            f"📍 <s>{html.escape(loc)}</s>\n"
            f"<i>Marked as DISMISSED from active tracker.</i>"
        )

        if message_id and chat_id:
            try:
                await client.post(
                    f"https://api.telegram.org/bot{bot_token}/editMessageText",
                    json={
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "text": updated_text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                        "reply_markup": {"inline_keyboard": []},
                    },
                    timeout=5.0,
                )
            except Exception as exc:
                logger.warning("Failed to edit message text on dismiss: %s", exc)

        if cb_id:
            try:
                await client.post(
                    f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery",
                    json={
                        "callback_query_id": cb_id,
                        "text": f"Dismissed: {comp} - {pos_title}",
                    },
                    timeout=5.0,
                )
            except Exception as exc:
                logger.warning("Failed to answer callback query on dismiss: %s", exc)

        return True

    # 3. Handle Applied: "applied:{job_id}"
    elif data.startswith("applied:"):
        job_id = data.split("applied:", 1)[1].strip()
        mark_job_status(job_id=job_id, status="APPLIED", db_path=db_path)
        target_job = get_job_by_id(job_id, db_path=db_path)

        comp = target_job["company"] if target_job else "Job"
        pos_title = target_job["title"] if target_job else f"#{job_id}"
        loc = target_job.get("location", "India") if target_job else ""

        updated_text = (
            f"✅ <b>[APPLIED]</b> <b>{html.escape(comp)}</b> — {html.escape(pos_title)}\n"
            f"📍 {html.escape(loc)}\n"
            f"📅 <i>Application recorded in database • Best of luck!</i>"
        )

        if message_id and chat_id:
            try:
                await client.post(
                    f"https://api.telegram.org/bot{bot_token}/editMessageText",
                    json={
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "text": updated_text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                        "reply_markup": {"inline_keyboard": []},
                    },
                    timeout=5.0,
                )
            except Exception as exc:
                logger.warning("Failed to edit message text on applied: %s", exc)

        if cb_id:
            try:
                await client.post(
                    f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery",
                    json={
                        "callback_query_id": cb_id,
                        "text": f"Marked Applied: {comp}! Good luck!",
                    },
                    timeout=5.0,
                )
            except Exception as exc:
                logger.warning("Failed to answer callback query on applied: %s", exc)

        return True

    # 4. Handle Tailor Resume: "tailor:{job_id}"
    elif data.startswith("tailor:"):
        job_id = data.split("tailor:", 1)[1].strip()
        target_job = get_job_by_id(job_id, db_path=db_path)
        if not target_job:
            if cb_id:
                try:
                    await client.post(
                        f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery",
                        json={"callback_query_id": cb_id, "text": f"Job #{job_id} not found.", "show_alert": True},
                        timeout=5.0,
                    )
                except Exception:
                    pass
            return False

        if cb_id:
            try:
                await client.post(
                    f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery",
                    json={"callback_query_id": cb_id, "text": f"⏳ Tailoring resume for {target_job.get('company', 'Job')}..."},
                    timeout=5.0,
                )
            except Exception:
                pass

        comp_name = html.escape(str(target_job.get("company", "Unknown")))
        role_title = html.escape(str(target_job.get("title", "Role")))
        job_obj = _dict_to_job_posting(target_job)

        try:
            tailor_res = await asyncio.to_thread(tailor_resume_for_job, job_obj, force=True)
            tex_path, pdf_path = tailor_res[0], tailor_res[1]
            gap_note = getattr(tailor_res, "gap_note", None)
        except Exception as exc:
            logger.error("Callback query tailor error: %s", exc)
            tex_path, pdf_path, gap_note = None, None, None

        if pdf_path and Path(pdf_path).is_file():
            effective_url, _, _ = resolve_effective_apply_url(target_job)
            apply_link = f'\n🔗 <a href="{html.escape(str(effective_url))}">Direct Apply Link</a>' if effective_url else ""
            gap_caption = f"\n💡 {html.escape(str(gap_note))}" if gap_note else ""
            caption = (
                f"📄 <b>Tailored Resume — {comp_name}</b>\n"
                f"💼 {role_title}\n"
                f"📍 {html.escape(str(target_job.get('location', 'India')))}"
                f"{apply_link}"
                f"{gap_caption}"
            )
            await send_telegram_document(bot_token, chat_id, pdf_path, caption=caption, client=client)
        elif tex_path:
            gap_str = f"\n💡 <i>{html.escape(str(gap_note))}</i>" if gap_note else ""
            await send_telegram_reply(
                bot_token,
                chat_id,
                f"✅ <b>Tailored Resume LaTeX Ready for {comp_name}:</b>\n<code>{html.escape(str(tex_path))}</code>{gap_str}",
                client,
            )
        else:
            await send_telegram_reply(
                bot_token,
                chat_id,
                f"⚠️ Could not tailor resume for {comp_name}. Please verify GROQ_API_KEY in .env.",
                client,
            )
        return True

    # 5. Handle Prep Application: "prep_apply:{job_id}"
    elif data.startswith("prep_apply:"):
        job_id = data.split("prep_apply:", 1)[1].strip()
        target_job = get_job_by_id(job_id, db_path=db_path)
        if not target_job:
            if cb_id:
                try:
                    await client.post(
                        f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery",
                        json={"callback_query_id": cb_id, "text": f"Job #{job_id} not found.", "show_alert": True},
                        timeout=5.0,
                    )
                except Exception:
                    pass
            return False

        comp_name = target_job.get("company", "Company")
        title_name = target_job.get("title", f"#{job_id}")

        if cb_id:
            try:
                await client.post(
                    f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery",
                    json={
                        "callback_query_id": cb_id,
                        "text": f"⏳ Prepping application for {comp_name}...",
                    },
                    timeout=5.0,
                )
            except Exception as exc:
                logger.debug("Failed answering callback query on prep_apply: %s", exc)

        job_obj = _dict_to_job_posting(target_job)

        await send_telegram_chat_action(bot_token, chat_id, client, "typing")

        # 1. Tailor resume (force=True to tailor upon explicit user request)
        tex_path, pdf_path = None, None
        try:
            tex_path, pdf_path = tailor_resume_for_job(job_obj, force=True)
            if pdf_path and Path(pdf_path).is_file():
                await send_telegram_document(
                    bot_token,
                    chat_id,
                    pdf_path,
                    f"📄 <b>Tailored Resume PDF for {comp_name}</b> — {html.escape(title_name)}",
                    client,
                )
            elif tex_path and Path(tex_path).is_file():
                await send_telegram_document(
                    bot_token,
                    chat_id,
                    tex_path,
                    f"📄 <b>Tailored Resume LaTeX for {comp_name}</b>",
                    client,
                )
        except Exception as exc:
            logger.warning("Error during resume tailoring in prep_apply: %s", exc)

        # 2. Execute Form Prep Engine
        prep_result = await prep_job_application(
            job=job_obj,
            tailored_pdf_path=pdf_path,
            tailored_tex_path=tex_path,
            client=client,
            db_path=db_path,
        )

        # 3. Format Response Message
        status_icons = {
            "READY_LOCAL": "🖥️ <b>Application Pre-filled & Ready (Desktop Browser)</b>",
            "READY_REMOTE": "🌐 <b>Application Pre-filled & Ready (Remote Session)</b>",
            "FALLBACK_DRAFT": "📋 <b>Application Draft Prepared (Direct Portal)</b>",
            "ALREADY_PREPPED": "ℹ️ <b>Application Already Prepared</b>",
            "FAILED": "❌ <b>Application Prep Failed</b>",
        }
        header_text = status_icons.get(prep_result.status, "📋 <b>Application Prep</b>")

        reply_lines = [
            f"{header_text}",
            f"🏢 <b>{html.escape(comp_name)}</b> — {html.escape(title_name)}",
            "",
        ]

        if prep_result.prefilled_fields:
            reply_lines.append("📝 <b>Pre-filled Fields:</b>")
            for f_text in prep_result.prefilled_fields:
                reply_lines.append(f"• {html.escape(f_text)}")
            reply_lines.append("")

        if prep_result.drafted_questions:
            reply_lines.append("🤖 <b>Screening Questions:</b>")
            for q_item in prep_result.drafted_questions:
                q_text = html.escape(str(q_item.get("question", "")))
                a_text = html.escape(str(q_item.get("answer", "")))
                is_ai = q_item.get("is_ai", False)
                tag = "<i>(AI-Drafted — please review)</i>" if is_ai else "<i>(Factual)</i>"
                reply_lines.append(f"❓ <b>{q_text}</b> {tag}\n👉 <code>{a_text}</code>")
            reply_lines.append("")

        if prep_result.manual_fields:
            reply_lines.append("⚠️ <b>Needs Your Attention:</b>")
            for m_text in prep_result.manual_fields:
                reply_lines.append(f"• {html.escape(m_text)}")
            reply_lines.append("")

        if prep_result.status == "READY_LOCAL":
            reply_lines.append("✨ <i>Browser is open on your desktop! Review fields, enter any missing items, and click Submit.</i>")
        elif prep_result.status == "READY_REMOTE" and prep_result.interactive_url:
            reply_lines.append(f"🔗 <a href=\"{prep_result.interactive_url}\"><b>Open Interactive Pre-filled Form</b></a>")
        elif prep_result.status == "FALLBACK_DRAFT":
            reply_lines.append(f"🔗 <a href=\"{job_obj.apply_url}\"><b>Open Career Apply Page</b></a>")
            reply_lines.append("<i>Copy-paste the answers above and attach the tailored resume.</i>")
        elif prep_result.message:
            reply_lines.append(f"<i>{html.escape(prep_result.message)}</i>")

        reply_markup: dict[str, Any] = {
            "inline_keyboard": [
                [
                    {"text": "Applied", "callback_data": f"applied:{job_id}"},
                    {"text": "Dismiss", "callback_data": f"dismiss:{job_id}"},
                ]
            ]
        }
        if prep_result.interactive_url:
            reply_markup["inline_keyboard"].insert(0, [{"text": "🌐 Open Form", "url": prep_result.interactive_url}])
        elif prep_result.status == "FALLBACK_DRAFT":
            reply_markup["inline_keyboard"].insert(0, [{"text": "🔗 Apply Link", "url": str(job_obj.apply_url)}])

        await send_telegram_reply(
            bot_token,
            chat_id,
            "\n".join(reply_lines),
            client,
            reply_markup=reply_markup,
        )
        return True

    return False


async def _keep_typing_action(
    bot_token: str,
    chat_id: str | int,
    client: httpx.AsyncClient,
    stop_event: asyncio.Event,
    interval: float = 4.5,
) -> None:
    """Continuously send typing chat action to Telegram until stop_event is set."""
    while not stop_event.is_set():
        await send_telegram_chat_action(bot_token, chat_id, client, "typing")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def dispatch_single_update(
    update: dict[str, Any],
    bot_token: str,
    allowed_chat_id: str,
    client: httpx.AsyncClient,
    db_path: Optional[Path] = None,
) -> None:
    """Process a single incoming Telegram update asynchronously without blocking the polling loop."""
    try:
        # Check for callback queries (interactive inline keyboard actions)
        if "callback_query" in update:
            cb_query = update["callback_query"]
            logger.info(
                "Received callback: %s from user %s",
                cb_query.get("data"),
                cb_query.get("from", {}).get("id"),
            )
            try:
                console.print(
                    f"[magenta]Received callback:[/magenta] [bold]{cb_query.get('data')}[/bold] "
                    f"from user [yellow]{cb_query.get('from', {}).get('id')}[/yellow]"
                )
            except Exception:
                pass
            await handle_callback_query(
                callback_query=cb_query,
                bot_token=bot_token,
                allowed_chat_id=allowed_chat_id,
                client=client,
                db_path=db_path,
            )
            return

        message = update.get("message") or update.get("edited_message")
        if not message:
            return

        chat = message.get("chat", {})
        chat_id = chat.get("id")
        text = message.get("text") or ""

        if text.startswith("/"):
            logger.info("Received command: %s from chat_id %s", text, chat_id)
            try:
                console.print(f"[cyan]Received command:[/cyan] [bold]{text}[/bold] from chat_id [yellow]{chat_id}[/yellow]")
            except Exception:
                pass
            await handle_command(
                command_text=text,
                chat_id=chat_id,
                bot_token=bot_token,
                allowed_chat_id=allowed_chat_id,
                client=client,
                db_path=db_path,
            )
            return
        elif text.strip():
            if str(chat_id).strip() != str(allowed_chat_id).strip():
                logger.warning("Unauthorized access attempt from chat_id: %s", chat_id)
                await send_telegram_reply(
                    bot_token,
                    chat_id,
                    "⛔ <b>Access Denied</b>: Your Telegram account is not authorized to control this GCC Job Radar bot.",
                    client,
                )
                return

            raw_msg = text.strip()
            raw_lower = raw_msg.lower()

            # Fast-path 1: Natural language scan new
            m_scan_new = re.match(
                r"^(?:run\s+)?scan\s+(?:the\s+)?(?:new|newly\s+added|recent)(?:\s+companies)?(?:\s+(\d+))?$",
                raw_lower,
            )
            if m_scan_new:
                count_val = m_scan_new.group(1)
                cmd_str = f"/scan new {count_val}".strip() if count_val else "/scan new"
                await handle_command(
                    command_text=cmd_str,
                    chat_id=chat_id,
                    bot_token=bot_token,
                    allowed_chat_id=allowed_chat_id,
                    client=client,
                    db_path=db_path,
                )
                return

            # Fast-path 2: Natural language applied list / applications view
            if re.match(r"^(?:show\s+|view\s+|get\s+|my\s+)?(?:applied|applies|applications?)(?:\s+(?:list|jobs|roles|history))?$", raw_lower):
                await handle_command(
                    command_text="/applied",
                    chat_id=chat_id,
                    bot_token=bot_token,
                    allowed_chat_id=allowed_chat_id,
                    client=client,
                    db_path=db_path,
                )
                return

            # Fast-path 3: Natural language apply
            is_nl_apply = (
                re.search(r"\b(?:applied|aoplies|applies)(?:\s+opening)?(?:\s+so\s+mark\s+.*)?$", raw_lower)
                or re.match(r"^(?:i\s+mean\s+)?(?:i\s+)?(?:have\s+|already\s+)?(?:applied|applies|apply)\s+(?:to\s+|for\s+)", raw_lower)
                or re.match(r"^mark(?:ed)?\s+.+\s+as\s+applied\b", raw_lower)
            )
            if is_nl_apply:
                await handle_command(
                    command_text=f"/apply {raw_msg}",
                    chat_id=chat_id,
                    bot_token=bot_token,
                    allowed_chat_id=allowed_chat_id,
                    client=client,
                    db_path=db_path,
                )
                return

            # Fast-path 4: Natural language dismiss / hide (with typo tolerance)
            m_nl_dismiss = re.match(
                r"^(?:please\s+)?(?:dismiss|dimsiss|dimiss|dissmiss|dismis|dsmiss|hide|ignore|remove|drop)\s+(.+)$",
                raw_lower,
            )
            if m_nl_dismiss:
                dismiss_target = re.sub(
                    r"^(?:please\s+)?(?:dismiss|dimsiss|dimiss|dissmiss|dismis|dsmiss|hide|ignore|remove|drop)\s+",
                    "",
                    raw_msg,
                    flags=re.IGNORECASE,
                ).strip()
                dismiss_target = re.sub(
                    r"\s+(?:from\s+(?:radar|tracker)|roles?|jobs?)$",
                    "",
                    dismiss_target,
                    flags=re.IGNORECASE,
                ).strip()
                if dismiss_target:
                    await handle_command(
                        command_text=f"/dismiss {dismiss_target}",
                        chat_id=chat_id,
                        bot_token=bot_token,
                        allowed_chat_id=allowed_chat_id,
                        client=client,
                        db_path=db_path,
                    )
                    return

            logger.info("AI Query: %s from chat_id %s", raw_msg, chat_id)
            try:
                console.print(f"[green]AI Query:[/green] [bold]{raw_msg}[/bold] from chat_id [yellow]{chat_id}[/yellow]")
            except Exception:
                pass

            stop_typing = asyncio.Event()
            typing_task = asyncio.create_task(
                _keep_typing_action(bot_token, chat_id, client, stop_typing)
            )
            try:
                ai_reply = await ask_ai_agent(raw_msg, chat_id=chat_id, db_path=db_path, client=client)
            finally:
                stop_typing.set()
                typing_task.cancel()
                try:
                    await typing_task
                except (asyncio.CancelledError, Exception):
                    pass

            await send_telegram_reply(bot_token, chat_id, ai_reply, client)
    except Exception as exc:
        logger.exception("Error processing update %s: %s", update.get("update_id"), exc)
        try:
            msg = update.get("message") or update.get("edited_message")
            if msg and msg.get("chat", {}).get("id"):
                await send_telegram_reply(
                    bot_token,
                    msg["chat"]["id"],
                    "⚠️ <i>An error occurred while processing your request. Please try again.</i>",
                    client,
                )
        except Exception:
            pass


async def run_bot_listener(
    bot_token: Optional[str] = None,
    allowed_chat_id: Optional[str] = None,
    db_path: Optional[Path] = None,
    poll_timeout: int = 20,
    max_iterations: Optional[int] = None,
    sleep_func: Any = asyncio.sleep,
) -> None:
    """Run long-polling loop to listen for Telegram commands and callback queries."""
    bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
    allowed_chat_id = allowed_chat_id or os.getenv("TELEGRAM_CHAT_ID")

    if not bot_token:
        raise ValueError("Missing TELEGRAM_BOT_TOKEN. Set it in environment or pass bot_token explicitly.")
    if not allowed_chat_id:
        raise ValueError("Missing TELEGRAM_CHAT_ID. Set it in environment or pass allowed_chat_id explicitly.")

    logger.info("Telegram Bot Active. Authorized Chat ID: %s, Target Boards: %d", allowed_chat_id, len(COMPANIES))
    try:
        console.print(
            Panel(
                f"[bold white]Authorized Chat ID:[/bold white] [bold cyan]{allowed_chat_id}[/bold cyan]\n"
                f"[bold white]Target Boards:[/bold white] [green]{len(COMPANIES)} GCCs[/green]\n"
                f"[bold white]Mode:[/bold white] Long-polling via Telegram Bot API\n"
                f"[dim]Press Ctrl+C to stop the bot listener.[/dim]",
                title="[bold cyan]GCC Job Radar - Telegram Bot Active[/bold cyan]",
                border_style="cyan",
                padding=(1, 2),
            )
        )
    except Exception:
        pass

    offset: Optional[int] = None
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    iteration = 0

    async with httpx.AsyncClient(timeout=poll_timeout + 10.0) as client:
        # Sync official bot menu commands with Telegram API on startup
        await sync_telegram_bot_commands(bot_token, client)

        # Sync latest state from Turso Cloud Database on startup
        from gcc_job_radar.turso_sync import is_turso_configured, sync_turso
        if is_turso_configured():
            try:
                sync_turso(db_path, direction="pull")
                logger.info("Synced state from Turso cloud database on startup.")
            except Exception as e:
                logger.warning(f"Failed to sync with Turso on startup: {e}")

        # Purge any pre-existing invalid unreviewed records from earlier scans
        try:
            purge_invalid_jobs(db_path)
        except Exception as err:
            logger.debug("Startup purge error: %s", err)

        active_tasks: set[asyncio.Task] = set()
        try:
            while True:
                if max_iterations is not None and iteration >= max_iterations:
                    break
                iteration += 1

                try:
                    params: dict[str, Any] = {"timeout": poll_timeout}
                    if offset is not None:
                        params["offset"] = offset

                    resp = await client.get(url, params=params)
                    if resp.status_code != 200:
                        if resp.status_code == 409:
                            logger.warning(
                                "Telegram getUpdates returned 409 Conflict: another instance is connected. "
                                "This is expected during zero-downtime rolling deployments while the previous container shuts down. Retrying in 5s..."
                            )
                            await sleep_func(5)
                        else:
                            logger.warning("Telegram getUpdates returned status %s: %s", resp.status_code, resp.text)
                            await sleep_func(3)
                        continue

                    data = resp.json()
                    updates = data.get("result", [])

                    for update in updates:
                        offset = update["update_id"] + 1
                        t = asyncio.create_task(
                            dispatch_single_update(
                                update=update,
                                bot_token=bot_token,
                                allowed_chat_id=allowed_chat_id,
                                client=client,
                                db_path=db_path,
                            )
                        )
                        active_tasks.add(t)
                        t.add_done_callback(active_tasks.discard)

                except asyncio.CancelledError:
                    break
                except httpx.ConnectTimeout:
                    logger.error(
                        "Connection timeout connecting to api.telegram.org. If your local ISP blocks Telegram API, enable WARP/VPN or configure a proxy."
                    )
                    await sleep_func(5)
                except Exception as exc:
                    logger.error("Error in bot polling loop: %s", exc)
                    await sleep_func(2)
        finally:
            if active_tasks:
                logger.info("Awaiting %d in-flight Telegram task(s) during shutdown...", len(active_tasks))
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*list(active_tasks), return_exceptions=True),
                        timeout=15.0,
                    )
                except (asyncio.TimeoutError, Exception):
                    pass

