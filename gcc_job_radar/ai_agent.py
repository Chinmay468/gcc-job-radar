"""Interactive conversational AI job assistant for GCC Job Radar."""

import asyncio
import html
from html.parser import HTMLParser
import json
import logging

import os
from pathlib import Path
import re
import sys
from typing import Any, Optional
from dotenv import load_dotenv
import httpx

# Automatically load environment variables from .env if present
load_dotenv()

# Ensure repository root is in sys.path so tools.* can be imported seamlessly
_repo_root = str(Path(__file__).resolve().parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from gcc_job_radar.config import COMPANIES
from gcc_job_radar.db import (
    find_jobs_by_selector,
    get_applied_and_dismissed_companies,
    get_jobs_by_status,
    get_stats,
    mark_job_status,
    query_jobs,
    record_jobs,
    record_manual_job,
)
from gcc_job_radar.link_resolver import resolve_effective_apply_url
from gcc_job_radar.presentation import build_ranked_presentation, format_jobs_html
from gcc_job_radar.scanner import scan_all_companies

logger = logging.getLogger(__name__)


def _safe_print(text: str, file: Any = None) -> None:
    """Print text safely, replacing unencodable characters on Windows cp1252/cp437 console."""
    target = file or sys.stdout
    try:
        print(text, file=target)
    except UnicodeEncodeError:
        encoding = getattr(target, "encoding", "ascii") or "ascii"
        sanitized = text.encode(encoding, errors="replace").decode(encoding)
        print(sanitized, file=target)

SYSTEM_PROMPT = (
    "You are the GCC Job Radar AI Assistant. You help candidates discover and evaluate verified "
    "entry-level engineering, software, and tech roles at foreign GCCs (Global Capability Centers), enterprise tech hubs, and verified job alerts in India.\n\n"
    "CRITICAL CAPABILITY — DIRECT EMAIL INBOX ACCESS:\n"
    "- YOU HAVE FULL DIRECT ACCESS TO THE USER'S CONFIGURED EMAIL ACCOUNTS.\n"
    "- The user has 3 active email accounts configured in .env with authorized IMAP SSL credentials (chinmay8064@gmail.com, chinmaymaheshwari.it27@gmail.com, chinmaymaheshwari.it27@jecrc.ac.in).\n"
    "- When the user asks to check, scan, go through, or sync their email accounts / inbox for jobs, job alerts, or emails (e.g. 'Go through all 3 email accounts for new relevant jobs', 'check my emails for jobs', 'scan inbox for job alerts', 'sync emails'), ALWAYS invoke the `sync_email_jobs` tool immediately.\n"
    "- NEVER say 'I am not able to access your email accounts directly' or 'I don't have access to your email'. You DO have direct access via `sync_email_jobs`.\n\n"
    "- ONLY invoke tools (`check_company_live`, `query_jobs`, `sync_email_jobs`, `get_applied_jobs`, `get_dismissed_jobs`, `manage_job_status`) when searching for job openings or checking company/email status. NEVER invoke them for compensation, CTC, salary inquiries, interview advice, resume tips, or general role comparisons. ATS endpoints do NOT contain Indian CTC/compensation figures.\n"
    "- Invoke `query_jobs` when the user is searching for open job listings in the database by title, keyword, city, or company name (e.g. 'BlackRock', 'Flipkart', 'find python roles in Bangalore'). If `query_jobs` returns 0 jobs for a requested company or if the user asks to scan, check, or refresh active openings at a specific company (e.g. 'check Databricks live', 'scan Celonis', 'add/check BlackRock', 'check Flipkart'), invoke `check_company_live` to fetch live openings directly from the company's verified ATS board.\n"
    "- Use `get_applied_jobs` whenever the user asks for their applied jobs, application history, applied sheet, applied list, or asks 'where are the rest of my applications'. ALWAYS invoke `get_applied_jobs` to retrieve the authentic list of applied jobs from the database instead of guessing from recent chat context.\n"
    "- Use `get_dismissed_jobs` whenever the user asks for dismissed jobs, dismissed companies, hidden jobs, 'name of all', 'names of all companies', 'list all dismissed', or asks which companies/roles have been dismissed. ALWAYS invoke `get_dismissed_jobs` to retrieve the comprehensive list of ALL dismissed companies and total count from the database instead of guessing or listing only 4-5 from recent chat context.\n"
    "- Use `manage_job_status` when the user asks to dismiss, hide, apply, mark as applied, or restore/undismiss jobs by ID number (e.g. 'dismiss job 1 and 4') or company name (e.g. 'dismiss Devmani Traders', 'mark BT Group as applied', 'restore job 2', 'applied to uipath, celonis', 'dismiss wysa, katalystcs, tvaram, WSP, betterworks'). Dismissing companies by name permanently suppresses them from future scans, email alerts, and daily digests, even if they have no currently active listings in the local database.\n"
    "- Use `tailor_job_resume` when the user asks to tailor, customize, adapt, or generate a resume/CV for a specific job (e.g. 'tailor my resume for Flipkart', 'generate a resume for job 1', 'tailor resume for Amazon SDE-1').\n"
    "- Use `sync_email_jobs` whenever the user asks to scan, check, go through, or ingest job alert emails from their configured email accounts.\n"
    "- FILTERING APPLIED AND DISMISSED COMPANIES: By default, NEVER show or suggest roles or company names that the user has already marked as APPLIED or DISMISSED, unless the user specifically asks for 'all' (e.g. 'show all', 'all companies', 'include dismissed'). `query_jobs` and `get_configured_companies` accept `include_all`: only set `include_all=True` when specifically asked for all companies/jobs.\n\n"
    "DOMAIN KNOWLEDGE FOR COMPENSATION & CTC QUERIES IN INDIA:\n"
    "- When asked about compensation, CTC, or salary thresholds (e.g. 'which role offers CTC over 12 lakhs?'):\n"
    "  • Foreign GCCs & Enterprise Tech Hubs (e.g. BT Group, Google, Microsoft, Morgan Stanley, Snowflake, Databricks, Celonis, Cisco, Walmart): Entry-level Associate / Graduate Engineers typically receive ₹11 – 24+ LPA (readily crossing ₹12 LPA).\n"
    "  • Telecom / IT Service MNCs (e.g. Ericsson, Nokia, TCS, Infosys, Wipro, Cognizant): Entry-level Associate Engineers / Graduate Trainees typically receive ₹3.5 – 6.5 LPA (rarely exceeding ₹7 LPA).\n"
    "  • Early-Stage Agencies / Staffing Portals (e.g. Talentd, NexisGrow aggregators): Placements typically fall in the ₹3 – 6 LPA range.\n"
    "- Deliver an immediate, clear, direct, and well-reasoned comparative verdict with specific CTC estimates without running live scans.\n\n"
    "DOMAIN KNOWLEDGE FOR RELEVANCE SCORE POINTS (e.g. '[15 pts]', '[10 pts]', '[25 pts]', '[45 pts]'):\n"
    "- When the user asks what the points mean (e.g. 'what does these points mean', 'what is pts', 'what are the points', 'how are points calculated', 'why does this role have 15 pts?'):\n"
    "  • The points represent the Personal Tech-Stack Relevance Score (0 to 100) calculated automatically for each job posting based on how closely it matches the candidate's core skills:\n"
    "    - Core Stack Matches (+20 to +25 pts each): Java, Spring Boot 3, MERN (MongoDB, Express, React, Node.js), Apache Kafka, MySQL.\n"
    "    - Architecture & DevOps (+10 to +15 pts each): Docker, JWT Auth, GitHub Actions, CI/CD, Microservices / REST APIs.\n"
    "    - Supporting Tech (+5 to +10 pts each): TypeScript, SQL/PostgreSQL, Redis, Git, Linux.\n"
    "    - Title Affinity Bonus (+10 to +20 pts): Backend Developer, Full Stack, Java Developer, SDE / Software Engineer.\n"
    "  • Higher points indicate a higher match with the candidate's target tech stack. Roles with 0 pts are still verified entry-level tech roles, but without explicit keywords matching those specific stack skills in their title or description.\n\n"
    "DOMAIN KNOWLEDGE FOR COMPANY REGISTRY & SCRAPER ARCHITECTURE:\n"
    "- TOTAL TRACKED COMPANIES: 5,313 companies in the active registry.\n"
    "- DEDICATED CUSTOM CAREER PORTAL SCRAPERS (NON-ATS) — EXACTLY 6 COMPANIES:\n"
    "  • These 6 companies do NOT use standard ATS job boards (like Greenhouse, Ashby, Lever, etc.) and instead have proprietary career portals with dedicated custom API/DOM scrapers:\n"
    "    1. Flipkart (Custom Turbohire internal portal scraper)\n"
    "    2. Apple (Official Apple Jobs Search API scraper)\n"
    "    3. Amazon (Amazon Jobs Search API scraper)\n"
    "    4. Microsoft (Microsoft Careers Search API scraper)\n"
    "    5. Electronic Arts / EA (EA Careers internal API scraper)\n"
    "    6. Majid Al Futtaim (Phenom / SuccessFactors career API scraper)\n"
    "  • NOTE ON CISCO: Cisco GCC is tracked via Workday ATS (cisco/Cisco_Careers), not a custom portal scraper.\n"
    "- STANDARD ATS PLATFORMS — 5,307 COMPANIES:\n"
    "  • Monitored via standardized ATS connectors:\n"
    "    - Greenhouse: 2,044 companies\n"
    "    - Ashby: 1,499 companies\n"
    "    - SmartRecruiters: 999 companies\n"
    "    - Lever: 704 companies\n"
    "    - Workday: 61 companies (e.g. Cisco GCC, Walmart)\n"
    "- WHEN THE USER ASKS HOW MANY COMPANIES WE SCRAPE DIRECTLY FROM CAREER PAGES (OR NOT ON ANY ATS BOARD LIKE FLIPKART, APPLE, ETC.):\n"
    "  • Answer clearly: Exactly 6 companies have dedicated custom career portal scrapers (Flipkart, Apple, Amazon, Microsoft, EA, Majid Al Futtaim).\n"
    "  • NEVER answer 5,313 when asked about custom scrapers or non-ATS boards! 5,313 is the total count across ALL sources (5,307 standard ATS + 6 custom).\n\n"
    "CRITICAL FORMATTING GUIDELINES FOR TELEGRAM (JOB LISTINGS & RECOMMENDATIONS):\n"
    "- NEVER use markdown tables (no '| ... |' format). Telegram cannot render tables and they look broken and unreadable on mobile screens.\n"
    "- Present job listings as a clean, structured RANKED RECOMMENDATION, not a flat data dump:\n"
    "  1. ONE-LINE LEAD-IN: Always begin with a concise one-line lead-in summarizing the total roles and count of strong stack fits (e.g. 'Found 6 new roles, 2 are strong fits for your stack.' or 'Found 3 verified entry-level roles.'). Absolutely NO throat-clearing intro sentences ('Sure, here are the jobs I found', 'I found the following listings:').\n"
    "  2. TOP PICK(S) CALLED OUT FIRST: Order jobs by relevance_score descending. Feature top pick(s) under ⭐ **Best Fit** (roles with score ≥ 40, or top 1-2 with score ≥ 25).\n"
    "  3. ONE-LINE 'WHY' UNDERNEATH EACH JOB: Include a concrete rationale line (💡 *Why: ...*) underneath each job card using the factual `why` or `matched_reasons` provided in the tool output (e.g. 'Matches Spring Boot + Kafka • Core target stack'). NEVER fabricate reasons.\n"
    "  4. TIERED GROUPING:\n"
    "     • ⭐ **Best Fit:** (highest score roles, core tech matches)\n"
    "     • ⚡ **Strong Fit:** (good stack match, score 20–39)\n"
    "     • 📋 **Worth a Look:** (adjacent skills, general tech, or score < 20)\n"
    "  5. TAIL SUMMARY FOR LONG LISTS: When there are more than 5 results, display the top 4–5 cards in full detail, then append a tail summary naming the omitted companies (e.g. '➕ **+3 more roles at Snowflake, Databricks** — reply `/latest all` or search by company to view'). NEVER silently drop jobs!\n"
    "  6. JOB CARD STRUCTURE:\n"
    "     **1. Company Name**\n"
    "     💼 Job Title\n"
    "     📍 Location • 📅 Posted: <date> [• ⏳ Closes: <end_date>]\n"
    "     🔗 [Apply on ATS](apply_url)\n"
    "     💡 *Why: Matches Spring Boot + Kafka • Core target stack*\n\n"
    "- APPLICATION DATES & EXPIRATION:\n"
    "  • Always display the application start date (or posted date) and application end date (closing deadline) when present.\n"
    "  • If an end date / deadline is available in the job record, format as '• ⏳ Closes: <end_date>'.\n"
    "  • Expired roles where the deadline has passed must never be presented as open or recommended.\n\n"
    "GENERAL RESPONSE FORMATTING RULES (FOR ALL NON-JOB-LISTING ANSWERS):\n"
    "- Default to structured markdown: short headers (### or **Header**), bullet points, and numbered lists for steps or priorities.\n"
    "- Break any answer longer than ~3 sentences into bullets or short labeled sections instead of one paragraph. NEVER output a wall of text or a dense continuous paragraph.\n"
    "- Bold the key term, metric, or verdict at the start of each bullet (e.g. '**Stale for 9 days** — no status change since Sept 4', '**High Priority** — follow up on Celonis application').\n"
    "- Use checkbox-style bullets (`- [ ] `) specifically for action items or follow-ups the user still needs to do (e.g. '- [ ] Message recruiter on LinkedIn', '- [ ] Tailor resume for Databricks'). Do NOT use checkboxes for informational bullets (use standard bullets `•` or `-` instead).\n"
    "- Never open with a throat-clearing intro sentence (e.g., 'Sure, here is a summary...', 'Certainly! I can help with that', 'Here are your priorities:'). Start directly with the structured content.\n"
    "- Keep it scannable: prefer 4 to 8 short, focused bullets over 2 long, dense ones.\n\n"
    "FEW-SHOT EXAMPLES FOR GENERAL / CONVERSATIONAL QUESTIONS:\n\n"
    "User: What should I prioritize this week?\n"
    "Assistant:\n"
    "### Weekly Priorities & Action Plan\n\n"
    "**Immediate Action Items:**\n"
    "- [ ] **Follow up on Celonis** — Pending for 8 days without status update.\n"
    "- [ ] **Tailor resume for Databricks** — Associate Java Engineer matches 45 pts of your core stack.\n"
    "- [ ] **Review fresh email job alerts** — 5 new postings detected in your inbox today.\n\n"
    "**Strategic Focus:**\n"
    "• **Backend & Java Roles** — Highest current hiring volume across foreign GCCs this month.\n"
    "• **Application Momentum** — Maintain 3–5 active submissions weekly for steady pipeline health.\n\n"
    "User: Summarize my stale applications\n"
    "Assistant:\n"
    "### Stale Applications Summary (Pending ≥ 7 Days)\n\n"
    "**High-Priority Follow-ups:**\n"
    "- [ ] **Celonis (Associate Software Engineer)** — Applied 9 days ago (Aug 25).\n"
    "- [ ] **BT Group (Graduate Software Engineer)** — Applied 11 days ago (Aug 23).\n\n"
    "**Recommended Next Steps:**\n"
    "• **Reach out on LinkedIn** — Message 1–2 technical recruiters or engineering managers.\n"
    "• **Check ATS Portal** — Confirm application status has not silently updated to in-review.\n\n"
    "User: Why was this job filtered out?\n"
    "Assistant:\n"
    "### Role Filtering Analysis\n\n"
    "**Verdict:**\n"
    "• **Seniority Mismatch** — Title specifies 'Senior Lead Engineer' (requires 5+ years experience). GCC Job Radar strictly targets entry-level and associate roles (0–2 years).\n\n"
    "**Additional Checks:**\n"
    "• **Tech Stack Alignment** — 0 pts match with candidate profile (demands C#/.NET instead of Java/Spring).\n"
    "• **Actionable Alternative** — Check for Associate or Graduate openings at the same company using `/check <company>`.\n\n"
    "User: how many companies are currently present that we do scrap of careers page (not on any ATS board like flipkart, apple, cisco)?\n"
    "Assistant:\n"
    "### Career Page Scraper Architecture\n\n"
    "**Custom Career Portal Scrapers (6 Companies):**\n"
    "These companies do not use standard ATS platforms and are scraped via dedicated proprietary API/DOM scrapers:\n"
    "• **Flipkart** — Custom Turbohire API/DOM portal scraper\n"
    "• **Apple** — Official Apple Jobs Search API scraper\n"
    "• **Amazon** — Amazon.jobs Search API scraper\n"
    "• **Microsoft** — Microsoft Careers Search API scraper\n"
    "• **Electronic Arts (EA)** — EA Careers internal API scraper\n"
    "• **Majid Al Futtaim** — Phenom / SuccessFactors career API scraper\n"
    "*(Note: Cisco GCC is integrated via Workday ATS, not a custom portal scraper).*\n\n"
    "**Standard ATS Platform Integrations (5,307 Companies):**\n"
    "• **Greenhouse** — 2,044 companies\n"
    "• **Ashby** — 1,499 companies\n"
    "• **SmartRecruiters** — 999 companies\n"
    "• **Lever** — 704 companies\n"
    "• **Workday** — 61 companies\n\n"
    "**Total Tracked Companies:** 5,313\n\n"
    "FEW-SHOT EXAMPLES FOR JOB SEARCH & RECOMMENDATION QUERIES:\n\n"
    "User: find java roles in bangalore\n"
    "Assistant:\n"
    "Found 3 new roles, 2 are strong fits for your stack.\n\n"
    "⭐ **Best Fit:**\n\n"
    "**1. Databricks**\n"
    "💼 Associate Software Engineer (Backend)\n"
    "📍 Bengaluru • 📅 Active\n"
    "🔗 [Apply on Greenhouse](https://job-boards.greenhouse.io/databricks/jobs/101)\n"
    "💡 *Matches Java + Spring Boot • Core target stack*\n\n"
    "⚡ **Strong Fit:**\n\n"
    "**2. Snowflake**\n"
    "💼 Software Engineer I\n"
    "📍 Bengaluru • 📅 Posted: 2026-09-12 • ⏳ Closes: 2026-10-15\n"
    "🔗 [Apply on Ashby](https://jobs.ashbyhq.com/snowflake/jobs/102)\n"
    "💡 *Matches Java + Docker • Target stack*\n\n"
    "📋 **Worth a Look:**\n\n"
    "**3. BT Group**\n"
    "💼 Graduate Software Engineer\n"
    "📍 Bengaluru • 📅 Active\n"
    "🔗 [Apply on Workday](https://bt.wd3.myworkdayjobs.com/bt/job/103)\n"
    "💡 *Good match for entry-level tech role*\n\n"
    "User: check recent jobs at flipkart, stripe, and celonis\n"
    "Assistant:\n"
    "Found 3 verified entry-level roles across target GCCs.\n\n"
    "⭐ **Best Fit:**\n\n"
    "**1. Flipkart**\n"
    "💼 SDE-1 (Java / Backend)\n"
    "📍 Bengaluru • 📅 Active\n"
    "🔗 [Apply on Flipkart Careers](https://www.flipkartcareers.com/job/301)\n"
    "💡 *Matches Java + MySQL • Core target stack*\n\n"
    "⚡ **Strong Fit:**\n\n"
    "**2. Celonis**\n"
    "💼 Associate Software Development Engineer\n"
    "📍 Bengaluru • 📅 Active\n"
    "🔗 [Apply on Greenhouse](https://job-boards.greenhouse.io/celonis/jobs/201)\n"
    "💡 *Matches REST/Microservices + MySQL • Target stack*\n\n"
    "📋 **Worth a Look:**\n\n"
    "**3. Stripe**\n"
    "💼 Technical Support Engineer - New Grad\n"
    "📍 Bengaluru • 📅 2026-09-10\n"
    "🔗 [Apply on Greenhouse](https://job-boards.greenhouse.io/stripe/jobs/202)\n"
    "💡 *Verified entry-level opening • 0-2 YOE*"
)


# Tool Schemas for Gemini & OpenAI

GEMINI_TOOLS = [
    {
        "function_declarations": [
            {
                "name": "query_jobs",
                "description": "Query the database of verified entry-level GCC tech jobs in India by title keyword, location, company, or status. ONLY use when searching for job postings. NEVER use for salary, CTC, or general questions.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "title_keyword": {"type": "STRING", "description": "Role or skill keyword (e.g. software, python, intern, backend)"},
                        "location": {"type": "STRING", "description": "City or region in India (e.g. Bangalore, Hyderabad, Pune)"},
                        "company": {"type": "STRING", "description": "Company name (e.g. Celonis, Snowflake, Databricks)"},
                        "status": {"type": "STRING", "description": "Job status filter: 'NEW' (default for open listings), 'APPLIED' (roles user has applied to), 'DISMISSED', or 'ALL'"},
                        "limit": {"type": "INTEGER", "description": "Max results to return (default 5)"},
                        "include_all": {"type": "BOOLEAN", "description": "Set to true ONLY if user explicitly requested ALL roles including applied and dismissed companies. Defaults to false."},
                    },
                },
            },
            {
                "name": "get_applied_jobs",
                "description": "Retrieve all job postings that the user has marked as APPLIED in the tracker database. ALWAYS use when the user asks for their applied jobs, application history, applied sheet, applied list, or asks what roles they applied to.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "limit": {"type": "INTEGER", "description": "Max results to return (default 50)"},
                    },
                },
            },
            {
                "name": "get_stale_applications",
                "description": "Retrieve job postings marked as APPLIED that have been pending without status update for 7+ days (or custom threshold). ALWAYS use when user asks for stale applications, pending follow-ups, or applications awaiting follow-up.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "days": {"type": "INTEGER", "description": "Threshold in days to consider application stale (default 7)."},
                    },
                },
            },
            {
                "name": "get_dismissed_jobs",
                "description": "Retrieve job postings that the user has marked as DISMISSED or hidden in the tracker database. ALWAYS use when the user asks for their dismissed jobs, hidden jobs, dismissed list, or asks what roles/companies were dismissed.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "limit": {"type": "INTEGER", "description": "Max results to return (default 30)"},
                    },
                },
            },
            {
                "name": "check_company_live",
                "description": "Trigger an immediate real-time live scan of a specific GCC company's career portal for open positions. ONLY use when the user explicitly requests a live scan or asks for active vacancies at a company. NEVER use for salary, CTC, or role comparisons.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "company_name": {"type": "STRING", "description": "Company name to scan (e.g. Celonis, Databricks)"},
                    },
                    "required": ["company_name"],
                },
            },
            {
                "name": "get_tracking_stats",
                "description": "Get database statistics (total jobs tracked, top companies, first and last seen timestamps).",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {},
                },
            },
            {
                "name": "get_configured_companies",
                "description": "Get the directory of configured GCC companies and foreign tech hubs tracked by GCC Job Radar.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "include_all": {"type": "BOOLEAN", "description": "Set to true ONLY if user specifically requested ALL companies including applied or dismissed. Defaults to false."},
                    },
                },
            },
            {
                "name": "manage_job_status",
                "description": "Update the status of tracked jobs in the database (mark as 'apply', 'dismiss', or 'restore' back to NEW). Accepts numeric job IDs (e.g. '1', '1, 2, 4'), unique IDs, or company names (e.g. 'Devmani Traders', 'BT Group', 'uipath, celonis').",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "action": {"type": "STRING", "description": "Action to perform: 'apply', 'dismiss', or 'restore'"},
                        "target": {"type": "STRING", "description": "Job ID number(s) or company name(s) to target (e.g. '1', '1, 2, 4', 'Devmani Traders', 'uipath, celonis')"},
                        "notes": {"type": "STRING", "description": "Optional notes when marking as applied (e.g. 'Applied via official ATS')"},
                    },
                    "required": ["action", "target"],
                },
            },
            {
                "name": "sync_email_jobs",
                "description": "Scan and ingest job alert emails directly from the user's 3 configured email accounts (e.g. LinkedIn, Naukri, Indeed, Glassdoor alerts via IMAP SSL). Extracts verified entry-level tech openings, saves them to the database, and returns the findings. ALWAYS use when user asks to check, scan, or go through their emails or inboxes for jobs.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "days": {"type": "INTEGER", "description": "Number of days back to search emails (default 7)."},
                        "limit": {"type": "INTEGER", "description": "Maximum emails to inspect per mailbox (default 15)."},
                        "unread_only": {"type": "BOOLEAN", "description": "Set to true to check only unread emails, or false to inspect all recent alert emails while deduplicating against database. Defaults to false."},
                    },
                },
            },
            {
                "name": "tailor_job_resume",
                "description": "Tailor and compile a customized LaTeX and PDF resume aligned specifically to a job opening using Groq AI. ALWAYS invoke when the user asks to tailor, customize, adapt, or generate a resume/CV for a job ID or company.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "job_id": {"type": "STRING", "description": "Numeric row ID or unique ID of the job posting (e.g. '1', '42')."},
                        "company": {"type": "STRING", "description": "Company name if job ID is not specified (e.g. 'Flipkart', 'Amazon')."},
                    },
                },
            },
        ]
    }
]

OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "query_jobs",
            "description": "Query the database of verified entry-level GCC tech jobs in India by title keyword, location, company, or status. ONLY use when searching for job postings. NEVER use for salary, CTC, or general questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title_keyword": {"type": "string", "description": "Role or skill keyword (e.g. software, python, intern, backend)"},
                    "location": {"type": "string", "description": "City or region in India (e.g. Bangalore, Hyderabad, Pune)"},
                    "company": {"type": "string", "description": "Company name (e.g. Celonis, Snowflake, Databricks)"},
                    "status": {"type": "string", "description": "Job status filter: 'NEW' (default for open listings), 'APPLIED' (roles user has applied to), 'DISMISSED', or 'ALL'"},
                    "limit": {"type": "integer", "description": "Max results to return (default 5)"},
                    "include_all": {"type": "boolean", "description": "Set to true ONLY if user explicitly requested ALL roles including applied and dismissed companies. Defaults to false."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_applied_jobs",
            "description": "Retrieve all job postings that the user has marked as APPLIED in the tracker database. ALWAYS use when the user asks for their applied jobs, application history, applied sheet, applied list, or asks what roles they applied to.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max results to return (default 50)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stale_applications",
            "description": "Retrieve job postings marked as APPLIED that have been pending without status update for 7+ days (or custom threshold). ALWAYS use when user asks for stale applications, pending follow-ups, or applications awaiting follow-up.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "Threshold in days to consider application stale (default 7)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_dismissed_jobs",
            "description": "Retrieve job postings that the user has marked as DISMISSED or hidden in the tracker database. ALWAYS use when the user asks for their dismissed jobs, hidden jobs, dismissed list, or asks what roles/companies were dismissed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max results to return (default 30)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_company_live",
            "description": "Trigger an immediate real-time live scan of a specific GCC company's career portal for open positions. ONLY use when the user explicitly requests a live scan or asks for active vacancies at a company. NEVER use for salary, CTC, or role comparisons.",
            "parameters": {
                "type": "object",
                "properties": {
                    "company_name": {"type": "string", "description": "Company name to scan (e.g. Celonis, Databricks)"},
                },
                "required": ["company_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_tracking_stats",
            "description": "Get database statistics (total jobs tracked, top companies, first and last seen timestamps).",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_configured_companies",
            "description": "Get the directory of configured GCC companies and foreign tech hubs tracked by GCC Job Radar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "include_all": {"type": "boolean", "description": "Set to true ONLY if user specifically requested ALL companies including applied or dismissed. Defaults to false."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_job_status",
            "description": "Update the status of tracked jobs in the database (mark as 'apply', 'dismiss', or 'restore' back to NEW). Accepts numeric job IDs (e.g. '1', '1, 2, 4'), unique IDs, or company names (e.g. 'Devmani Traders', 'BT Group', 'uipath, celonis').",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "Action to perform: 'apply', 'dismiss', or 'restore'"},
                    "target": {"type": "string", "description": "Job ID number(s) or company name(s) to target (e.g. '1', '1, 2, 4', 'Devmani Traders', 'uipath, celonis')"},
                    "notes": {"type": "string", "description": "Optional notes when marking as applied (e.g. 'Applied via official ATS')"},
                },
                "required": ["action", "target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sync_email_jobs",
            "description": "Scan and ingest job alert emails directly from the user's 3 configured email accounts (e.g. LinkedIn, Naukri, Indeed, Glassdoor alerts via IMAP SSL). Extracts verified entry-level tech openings, saves them to the database, and returns the findings. ALWAYS use when user asks to check, scan, or go through their emails or inboxes for jobs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "Number of days back to search emails (default 7)."},
                    "limit": {"type": "integer", "description": "Maximum emails to inspect per mailbox (default 15)."},
                    "unread_only": {"type": "boolean", "description": "Set to true to check only unread emails, or false to inspect all recent alert emails while deduplicating against database. Defaults to false."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tailor_job_resume",
            "description": "Tailor and compile a customized LaTeX and PDF resume aligned specifically to a job opening using Groq AI. ALWAYS invoke when the user asks to tailor, customize, adapt, or generate a resume/CV for a job ID or company.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "Numeric row ID or unique ID of the job posting (e.g. '1', '42')."},
                    "company": {"type": "string", "description": "Company name if job ID is not specified (e.g. 'Flipkart', 'Amazon')."},
                },
            },
        },
    },
]


class ChatHistoryManager:
    """Manages short multi-turn conversation histories per chat session."""

    def __init__(self, max_turns: int = 10) -> None:
        self.max_turns = max_turns
        self._histories: dict[str, list[dict[str, str]]] = {}

    def add_turn(self, chat_id: str | int, role: str, content: str) -> None:
        cid = str(chat_id)
        if cid not in self._histories:
            self._histories[cid] = []
        # Protect memory footprint: truncate very long assistant responses in memory
        saved_content = content
        if role == "assistant" and len(content) > 1500:
            saved_content = content[:1500] + "\n[...truncated in memory...]"
        self._histories[cid].append({"role": role, "content": saved_content})
        if len(self._histories[cid]) > self.max_turns * 2:
            self._histories[cid] = self._histories[cid][-self.max_turns * 2 :]

    def get_history(self, chat_id: str | int) -> list[dict[str, str]]:
        return list(self._histories.get(str(chat_id), []))

    def clear(self, chat_id: str | int) -> None:
        self._histories.pop(str(chat_id), None)


_chat_manager = ChatHistoryManager()


def clear_chat_history(chat_id: str | int) -> None:
    """Clear conversation history for a given chat session."""
    _chat_manager.clear(chat_id)


# Tool Implementations


def get_configured_companies(
    include_all: bool = False,
    db_path: Optional[Path] = None,
) -> list[dict[str, str]]:
    """Return configured GCC companies with name and ATS provider, excluding applied/dismissed by default."""
    excluded: set[str] = set()
    if not include_all:
        applied_comps, dismissed_comps = get_applied_and_dismissed_companies(db_path)
        excluded = applied_comps | dismissed_comps

    return [
        {
            "name": c.name,
            "provider": c.provider.value,
        }
        for c in COMPANIES
        if c.name.lower().strip() not in excluded
    ]


def parse_apply_target(text: str) -> tuple[str, str, Optional[str]]:
    """Parse company name, job title, and optional notes from natural language apply queries.

    Examples:
        "flam applied" -> ("Flam", "Software Engineer", None)
        "i mean i applies to flam software engineering intern opening so mark it as aoplies"
            -> ("Flam", "Software Engineering Intern", None)
        "mark Google as applied -n Referral from Alice"
            -> ("Google", "Software Engineer", "Referral from Alice")
        "software engineering intern at flam"
            -> ("Flam", "Software Engineering Intern", None)
    """
    s = text.strip()
    notes = None
    m_note = re.search(r"(?:-n|--notes|notes?:)\s+(.+)$", s, flags=re.IGNORECASE)
    if m_note:
        notes = m_note.group(1).strip().strip('"\'')
        s = s[: m_note.start()].strip()

    s = re.sub(
        r"^(?:i\s+mean\s+)?(?:i\s+)?(?:have\s+|already\s+)?(?:applied|applies|apply)\s+(?:to\s+|for\s+)?",
        "",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(r"^mark(?:ed)?\s+", "", s, flags=re.IGNORECASE)

    s = re.sub(
        r"\s+so\s+mark\s+(?:it\s+)?(?:as\s+)?(?:applied|aoplies|apply).*$",
        "",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(r"\s+as\s+(?:applied|aoplies|apply)$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+(?:applied|applies|aoplies)$", "", s, flags=re.IGNORECASE)
    s = re.sub(
        r"\s+(?:opening|openings|role|roles|job|jobs|position|positions)$",
        "",
        s,
        flags=re.IGNORECASE,
    )
    s = s.strip()

    m_at = re.match(r"^(.+?)\s+(?:at|in|@)\s+(.+)$", s, flags=re.IGNORECASE)
    if m_at:
        title = m_at.group(1).strip().title()
        comp = m_at.group(2).strip().title()
        return comp, title, notes

    title_keywords = (
        r"(?:software\s+engineering\s+intern(?:ship)?|"
        r"software\s+engineer(?:ing)?|"
        r"software\s+developer|"
        r"software\s+intern(?:ship)?|"
        r"software|"
        r"intern(?:ship)?|"
        r"engineer(?:ing)?|"
        r"developer|"
        r"analyst|"
        r"sde|"
        r"swe|"
        r"frontend|"
        r"backend|"
        r"fullstack|"
        r"full\s+stack|"
        r"data|"
        r"consultant|"
        r"trainee|"
        r"graduate|"
        r"associate|"
        r"qa|"
        r"devops|"
        r"cloud|"
        r"security)"
    )
    m_split = re.search(r"\b(" + title_keywords + r".*)$", s, flags=re.IGNORECASE)
    if m_split and m_split.start() > 0:
        comp = s[: m_split.start()].strip().title()
        title = s[m_split.start() :].strip().title()
        return comp, title, notes

    comp = s.title()
    title = "Software Engineer"
    return comp, title, notes


async def execute_tool(
    name: str, args: dict[str, Any], db_path: Optional[Path] = None
) -> dict[str, Any]:
    """Execute a registered tool function by name."""
    if name == "query_jobs":
        title_keyword = args.get("title_keyword")
        location = args.get("location")
        company = args.get("company")
        status = args.get("status", "NEW")
        include_all = bool(args.get("include_all", False))
        # Hard cap limit to max 15 to stay strictly within LLM context & TPM budgets
        limit = min(max(1, int(args.get("limit", 5))), 15)
        jobs = query_jobs(
            title_keyword=title_keyword,
            location=location,
            company=company,
            status=status,
            limit=limit,
            db_path=db_path,
            exclude_applied_or_dismissed_companies=(not include_all),
        )
        compact_jobs = []
        for j in jobs:
            eff_url, _, _ = resolve_effective_apply_url(j)
            score = j.get("relevance_score") or 0
            why = j.get("why")
            reasons = j.get("matched_reasons") or []
            if not why:
                from gcc_job_radar.relevance import evaluate_job_relevance
                calc_score, reasons, why = evaluate_job_relevance(
                    title=j.get("title", ""),
                    description=j.get("description", "") or j.get("notes", ""),
                    published_date=j.get("published_date"),
                    is_remote=bool(j.get("is_remote")),
                )
                if not score:
                    score = calc_score
            compact_jobs.append({
                "id": j.get("numeric_id") or j.get("id"),
                "company": j.get("company", "Unknown"),
                "title": j.get("title", "Role"),
                "location": j.get("location", ""),
                "apply_url": eff_url or j.get("apply_url") or "",
                "published_date": str(j.get("published_date") or "Active")[:10],
                "relevance_score": score,
                "matched_reasons": reasons,
                "why": why,
            })
        return {
            "status": "success",
            "count": len(compact_jobs),
            "jobs": compact_jobs,
            "note": "Returned up to 15 matching roles. Prompt the user to filter if they need more specific roles."
        }

    elif name == "get_applied_jobs":
        limit = min(max(1, int(args.get("limit", 20))), 20)

        jobs = get_jobs_by_status("APPLIED", limit=limit, db_path=db_path)
        formatted_jobs = []
        for j in jobs:
            eff_url, _, label = resolve_effective_apply_url(j)
            formatted_jobs.append({
                "id": j.get("numeric_id") or j.get("id"),
                "company": j.get("company", "Unknown"),
                "title": j.get("title", "Role"),
                "location": j.get("location", ""),
                "status": "APPLIED",
                "applied_at": str(j.get("applied_at") or "")[:10],
                "notes": j.get("notes"),
                "apply_url": eff_url,
                "published_date": str(j.get("published_date") or "Active")[:10],
            })
        return {"status": "success", "count": len(formatted_jobs), "jobs": formatted_jobs}

    elif name in ("get_stale_applications", "get_followups"):
        days = int(args.get("days", 7))
        from gcc_job_radar.db import get_stale_applications
        stale = get_stale_applications(days=days, db_path=db_path)
        formatted_stale = []
        for j in stale[:20]:
            eff_url, _, _ = resolve_effective_apply_url(j)
            formatted_stale.append({
                "id": j.get("numeric_id") or j.get("id"),
                "company": j.get("company", "Unknown"),
                "title": j.get("title", "Role"),
                "location": j.get("location", ""),
                "applied_at": str(j.get("applied_at") or "")[:10],
                "applied_days": j.get("applied_days", days),
                "notes": j.get("notes"),
                "apply_url": eff_url,
            })
        return {
            "status": "success",
            "days_threshold": days,
            "count": len(formatted_stale),
            "total_stale": len(stale),
            "stale_jobs": formatted_stale,
        }

    elif name == "get_dismissed_jobs":
        limit = min(max(1, int(args.get("limit", 30))), 50)

        all_dismissed = get_jobs_by_status("DISMISSED", db_path=db_path)
        total_dismissed = len(all_dismissed)
        recent_dismissed = all_dismissed[:limit]

        companies_summary = sorted({j.get("company", "Unknown") for j in all_dismissed})

        formatted_jobs = [
            {
                "id": j.get("numeric_id") or j.get("id"),
                "company": j.get("company", "Unknown"),
                "title": j.get("title", "Role"),
                "location": j.get("location", ""),
                "status": "DISMISSED",
            }
            for j in recent_dismissed
        ]
        return {
            "status": "success",
            "total_dismissed_count": total_dismissed,
            "all_dismissed_companies": companies_summary,
            "recent_dismissed_jobs": formatted_jobs,
            "note": (
                f"There are {total_dismissed} total dismissed roles in the database across companies: {', '.join(companies_summary)}. "
                f"Always inform the user of the total count ({total_dismissed}) and summarize the companies dismissed, then list the recent ones with their IDs."
            ),
        }


    elif name == "check_company_live":
        company_name = args.get("company_name", "").strip()
        query = company_name.lower()
        matched = [
            c for c in COMPANIES if query in c.name.lower() or query in c.board_token.lower()
        ]
        if not matched:
            return {
                "status": "not_found",
                "message": f"Company '{company_name}' is not in the tracked GCC registry.",
                "jobs": [],
            }

        target_company = matched[0]
        jobs = await scan_all_companies(companies=[target_company])
        record_jobs(jobs, db_path)
        job_dicts = [
            {
                "company": j.company,
                "title": j.title,
                "location": j.location,
                "apply_url": str(j.apply_url),
                "published_date": j.published_date or "Active",
            }
            for j in jobs
        ]
        return {
            "status": "success",
            "company": target_company.name,
            "count": len(job_dicts),
            "jobs": job_dicts,
        }

    elif name == "get_tracking_stats":
        stats = get_stats(db_path)
        return {"status": "success", "stats": stats}

    elif name == "get_configured_companies":
        include_all = bool(args.get("include_all", False))
        comps = get_configured_companies(include_all=include_all, db_path=db_path)
        custom_providers = {"custom", "apple", "amazon", "microsoft", "ea", "phenom_successfactors"}
        custom_scrapers = [c for c in comps if c.get("provider", "").lower() in custom_providers]
        ats_counts: dict[str, int] = {}
        for c in comps:
            p = c.get("provider", "").lower()
            ats_counts[p] = ats_counts.get(p, 0) + 1

        return {
            "status": "success",
            "count": len(comps),
            "companies": comps,
            "total_count": len(comps),
            "custom_career_scrapers_count": len(custom_scrapers),
            "custom_career_scrapers": [c["name"] for c in custom_scrapers],
            "ats_provider_breakdown": ats_counts,
            "include_all": include_all,
            "note": (
                f"Total tracked companies: {len(comps)}. "
                f"Custom career portal scrapers (non-ATS): {len(custom_scrapers)} ({', '.join(c['name'] for c in custom_scrapers)}). "
                f"Standard ATS boards: {len(comps) - len(custom_scrapers)} across Greenhouse, Ashby, SmartRecruiters, Lever, Workday."
            ),
        }

    elif name == "manage_job_status":
        action = str(args.get("action", "")).lower().strip()
        target = str(args.get("target", "")).strip()
        notes = args.get("notes")

        if action in ("dismiss", "hide"):
            from gcc_job_radar.db import dismiss_selectors_or_companies

            dismiss_res = dismiss_selectors_or_companies(target, notes=notes, db_path=db_path)
            updated_jobs = []
            for j in dismiss_res.get("dismissed_jobs", []):
                rowid = j.get("numeric_id") or j.get("id")
                eff_url, _, _ = resolve_effective_apply_url(j)
                updated_jobs.append({
                    "id": rowid,
                    "company": j.get("company", "Unknown"),
                    "title": j.get("title", "Role"),
                    "status": "DISMISSED",
                    "apply_url": eff_url or str(j.get("apply_url") or ""),
                })

            adhoc_items = dismiss_res.get("dismissed_adhoc", [])
            all_comps = dismiss_res.get("dismissed_companies", [])
            total_count = len(updated_jobs) + len(adhoc_items)

            return {
                "status": "success",
                "action": action,
                "target_status": "DISMISSED",
                "count": total_count,
                "jobs": updated_jobs,
                "adhoc_companies": adhoc_items,
                "companies": all_comps,
                "notes": notes,
            }

        jobs = find_jobs_by_selector(target, db_path=db_path)
        if not jobs:
            clean_sel = re.sub(r"^#", "", target).strip()
            is_numeric = clean_sel.isdigit() or (
                clean_sel
                and all(t.isdigit() for t in re.split(r"[,;\s]+", clean_sel) if t)
            )
            if action == "apply" and not is_numeric:
                comp, tit, parsed_notes = parse_apply_target(target)
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
                    eff_url, _, _ = resolve_effective_apply_url(adhoc_job)
                    return {
                        "status": "success",
                        "action": action,
                        "target_status": "APPLIED",
                        "count": 1,
                        "jobs": [
                            {
                                "id": rowid,
                                "company": adhoc_job.get("company", comp),
                                "title": adhoc_job.get("title", tit),
                                "status": "APPLIED",
                                "apply_url": eff_url or str(adhoc_job.get("apply_url") or ""),
                            }
                        ],
                        "notes": eff_notes,
                        "is_adhoc": True,
                    }

            return {
                "status": "not_found",
                "action": action,
                "message": f"No jobs found matching '{target}'.",
                "jobs": [],
            }

        target_status = "APPLIED" if action == "apply" else "NEW"
        updated_jobs = []
        for j in jobs:
            rowid = j.get("numeric_id") or j.get("id")
            mark_job_status(
                job_id=rowid,
                status=target_status,
                notes=notes if target_status == "APPLIED" else None,
                db_path=db_path,
            )
            eff_url, _, _ = resolve_effective_apply_url(j)
            updated_jobs.append({
                "id": rowid,
                "company": j.get("company", "Unknown"),
                "title": j.get("title", "Role"),
                "status": target_status,
                "apply_url": eff_url or str(j.get("apply_url") or ""),
            })

        return {
            "status": "success",
            "action": action,
            "target_status": target_status,
            "count": len(updated_jobs),
            "jobs": updated_jobs,
            "notes": notes,
        }

    elif name == "sync_email_jobs":
        days = int(args.get("days", 7))
        limit = int(args.get("limit", 15))
        unread_only = bool(args.get("unread_only", False))

        from tools.ingest_email import sync_email_alerts, get_configured_email_accounts
        import asyncio

        accounts = get_configured_email_accounts()
        jobs = await asyncio.to_thread(
            sync_email_alerts,
            days=days,
            limit=limit,
            unread_only=unread_only,
            db_path=db_path,
            notify=False,
        )

        compact_jobs = []
        for j in jobs[:20]:
            eff_url, _, label = resolve_effective_apply_url(j)
            compact_jobs.append({
                "id": getattr(j, "numeric_id", None) or getattr(j, "id", None),
                "company": j.company,
                "title": j.title,
                "location": j.location,
                "apply_url": eff_url or str(j.apply_url),
                "published_date": str(j.published_date or "Recent")[:10],
            })

        return {
            "status": "success",
            "accounts_checked": [u for u, _ in accounts],
            "count": len(compact_jobs),
            "total_found": len(jobs),
            "jobs": compact_jobs,
            "note": f"Scanned {len(accounts)} configured email account(s) and found {len(jobs)} relevant opening(s). Present newly found jobs clearly using structured job cards.",
        }

    elif name in ("tailor_job_resume", "tailor_resume"):
        job_id = args.get("job_id")
        company = args.get("company")
        selector = str(job_id).strip() if job_id else str(company or "").strip()
        if not selector:
            return {"status": "error", "message": "Please specify a job_id or company name to tailor the resume for."}

        jobs = find_jobs_by_selector(selector, db_path=db_path)
        if not jobs:
            return {"status": "not_found", "message": f"No jobs found matching '{selector}'."}

        target_job = jobs[0]
        from gcc_job_radar.resume_tailor_bridge import tailor_resume_for_job
        from gcc_job_radar.models import JobPosting, ATSProvider
        import asyncio

        prov_str = str(target_job.get("provider", "custom")).lower()
        prov_enum = ATSProvider.CUSTOM
        for p in ATSProvider:
            if p.value.lower() == prov_str:
                prov_enum = p
                break

        job_obj = JobPosting(
            id=str(target_job.get("id", "")),
            numeric_id=target_job.get("numeric_id"),
            company=str(target_job.get("company", "")),
            title=str(target_job.get("title", "")),
            location=str(target_job.get("location", "")),
            apply_url=str(target_job.get("apply_url", "")),
            provider=prov_enum,
            published_date=target_job.get("published_date"),
            description=target_job.get("notes") or "",
        )

        try:
            tex_path, pdf_path = await asyncio.to_thread(tailor_resume_for_job, job_obj, force=True)
        except Exception as exc:
            logger.error("Error in tailor_job_resume tool: %s", exc)
            tex_path, pdf_path = None, None

        if not tex_path and not pdf_path:
            return {
                "status": "error",
                "message": f"Could not tailor resume for {job_obj.company}. Ensure GROQ_API_KEY is configured in .env.",
            }

        return {
            "status": "success",
            "company": job_obj.company,
            "title": job_obj.title,
            "location": job_obj.location,
            "tex_path": tex_path,
            "pdf_path": pdf_path,
            "message": f"Successfully tailored resume for {job_obj.company} - {job_obj.title}. LaTeX: {tex_path}, PDF: {pdf_path}.",
        }

    return {"status": "error", "message": f"Unknown tool '{name}'"}


# Telegram HTML Formatting Helper


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
    # Note: Telegram Bot API uses HTML parse mode in this repository.
    # Telegram HTML has no native checkbox element; Unicode ballot box characters (☐ / ☑)
    # render reliably across iOS, Android, and Desktop clients without triggering
    # MarkdownV2 escaping errors or breaking HTML entity sanitization.
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




def format_tool_result_summary(name: str, result: dict[str, Any]) -> str:
    """Format tool result as clean readable HTML rather than raw JSON."""
    if name == "manage_job_status":
        action = result.get("action", "").lower()
        jobs = result.get("jobs", [])
        adhoc_comps = result.get("adhoc_companies", [])
        if not jobs and not adhoc_comps:
            return f"⚠️ {html.escape(result.get('message', 'No matching jobs found.'))}"

        if action == "apply":
            emoji = "✅"
            header = f"{emoji} <b>Marked as APPLIED ({len(jobs)}):</b>\n\n"
        elif action in ("dismiss", "hide"):
            emoji = "🗑️"
            if jobs and not adhoc_comps:
                header = f"{emoji} <b>Dismissed {len(jobs)} Job(s):</b>\n\n"
            elif not jobs and adhoc_comps:
                header = f"{emoji} <b>Dismissed {len(adhoc_comps)} Company Target(s):</b>\n\n"
            else:
                total = len(jobs) + len(adhoc_comps)
                header = f"{emoji} <b>Dismissed {total} Target(s) ({len(jobs)} job(s), {len(adhoc_comps)} company/companies):</b>\n\n"
        else:
            emoji = "🔄"
            header = f"{emoji} <b>Restored {len(jobs)} Job(s) to NEW:</b>\n\n"

        items = []
        for j in jobs:
            link = (
                f' • <a href="{html.escape(str(j.get("apply_url")))}">Apply Link</a>'
                if j.get("apply_url")
                else ""
            )
            sub_label = " <i>(Job Dismissed)</i>" if action in ("dismiss", "hide") else ""
            items.append(
                f"• <b>#{j.get('id')}. {html.escape(j.get('company', 'Unknown'))}</b> — {html.escape(j.get('title', 'Role'))}{sub_label}{link}"
            )

        for a in adhoc_comps:
            cname = html.escape(str(a.get("company", "Company")))
            items.append(
                f"• 🏢 <b>{cname}</b> — <i>Current opening dismissed (future roles will still be monitored)</i>"
            )

        notes = result.get("notes")
        notes_str = f"\n\n📝 <b>Notes:</b> <i>{html.escape(notes)}</i>" if notes else ""
        footer = "\n\n<i>💡 These specific roles won't be shown again, but we will continue searching and alert you whenever these companies post new openings matching your criteria!</i>" if action in ("dismiss", "hide") else ""
        return (header + "\n".join(items) + notes_str + footer).strip()

    if name == "get_applied_jobs":
        jobs = result.get("jobs", [])
        if not jobs:
            return (
                "ℹ️ <b>No Applied Roles Recorded</b>\n\n"
                "You haven't marked any roles as applied yet in the tracker database.\n"
                "Use <code>/apply &lt;id or company&gt;</code> to track your applications!"
            )
        return format_jobs_html(jobs, f"Your Applied Listings ({len(jobs)})")

    if name in ("get_stale_applications", "get_followups"):
        stale = result.get("stale_jobs", [])
        days = result.get("days_threshold", 7)
        if not stale:
            return (
                f"🎉 <b>No Stale Applications!</b>\n\n"
                f"• <b>Status:</b> All your tracked applications are active and under {days} days old.\n"
                f"• <b>Pipeline:</b> Keep applying to active roles using <code>/latest</code>."
            )
        lines = [
            f"⏳ <b>Stale Applications Summary (Pending ≥ {days} Days) — {len(stale)} Total</b>\n",
            "<b>Action Required:</b>",
        ]
        for j in stale[:8]:
            comp = html.escape(str(j.get("company", "Company")))
            title = html.escape(str(j.get("title", "Role")))
            app_days = j.get("applied_days", days)
            lines.append(f"☐ <b>{comp} ({title})</b> — Stale for {app_days} days with no status change.")
        lines.append("\n<b>Recommended Next Steps:</b>")
        lines.append("• <b>Reach out on LinkedIn</b> — Send a polite check-in to recruiters or engineering managers.")
        lines.append("• <b>Update Tracker</b> — Log your contact touchpoint with <code>/apply &lt;id&gt; -n \"Followed up\"</code>.")
        return "\n".join(lines)

    if name == "sync_email_jobs":
        jobs = result.get("jobs", [])
        accounts = result.get("accounts_checked", [])
        if not jobs:
            acc_list = "\n".join(f"• <code>{html.escape(acc)}</code>" for acc in accounts)
            return (
                f"ℹ️ <b>Email Alert Ingestion Complete</b>\n\n"
                f"Scanned {len(accounts)} configured email account(s):\n{acc_list}\n\n"
                f"No new unrecorded entry-level tech job alerts found in the specified window."
            )
        return format_jobs_html(jobs, f"New Openings from Email Alerts ({len(jobs)})")

    if name in ("tailor_job_resume", "tailor_resume"):
        if result.get("status") == "success":
            comp = html.escape(str(result.get("company", "Company")))
            title = html.escape(str(result.get("title", "Role")))
            tex_path = html.escape(str(result.get("tex_path") or ""))
            pdf_path = result.get("pdf_path")
            pdf_str = f"\n📄 <b>PDF:</b> <code>{html.escape(str(pdf_path))}</code>" if pdf_path else ""
            return (
                f"✅ <b>Tailored Resume Ready!</b>\n\n"
                f"• <b>Company:</b> {comp}\n"
                f"• <b>Role:</b> {title}\n"
                f"• <b>LaTeX Source:</b> <code>{tex_path}</code>"
                f"{pdf_str}\n\n"
                f"<i>Your resume has been tailored and saved to the <code>tailored/</code> workspace directory.</i>"
            )
        return f"⚠️ {html.escape(str(result.get('message', 'Could not tailor resume.')))}"

    if "jobs" in result:
        return format_jobs_html(result["jobs"], f"Results for {name}")
    if "companies" in result:
        total = result.get("total_count", len(result.get("companies", [])))
        custom = result.get("custom_career_scrapers", [])
        ats_counts = result.get("ats_provider_breakdown", {})
        if custom:
            lines = [
                f"🏢 <b>Tracked Company Registry ({total:,} Total)</b>\n",
                f"🛠️ <b>Custom Career Portal Scrapers ({len(custom)} Companies):</b>",
                "<i>Dedicated custom API/DOM scrapers for non-ATS career portals:</i>",
            ]
            for c in custom:
                lines.append(f"• <b>{html.escape(c)}</b>")
            lines.append("\n📡 <b>Standard ATS Platform Integrations:</b>")
            ats_labels = {
                "greenhouse": "Greenhouse",
                "ashby": "Ashby",
                "smartrecruiters": "SmartRecruiters",
                "lever": "Lever",
                "workday": "Workday",
            }
            for prov_key, label in ats_labels.items():
                if prov_key in ats_counts:
                    lines.append(f"• <b>{label}</b> — {ats_counts[prov_key]:,} companies")
            return "\n".join(lines).strip()

        include_all = result.get("include_all", False)
        by_provider: dict[str, list[str]] = {}
        for c in result["companies"]:
            p = c.get("provider", "OTHER").upper()
            by_provider.setdefault(p, []).append(c.get("name", ""))
        title_str = f"All Configured GCC Companies ({len(result['companies'])} total)" if include_all else f"Active Configured GCC Companies ({len(result['companies'])})"
        text = f"🏢 <b>{title_str}:</b>\n\n"
        for provider, names in sorted(by_provider.items()):
            prominent = [n for n in ["Celonis", "Databricks", "Snowflake", "BT Group", "Google", "Microsoft"] if n in names]
            other = [n for n in sorted(names) if n not in prominent]
            sample_list = prominent + other[: max(1, 8 - len(prominent))]
            sample = ", ".join(sample_list)
            text += f"• <b>{provider}</b> ({len(names)} boards): e.g. <i>{sample}...</i>\n"
        if not include_all:
            text += "\n💡 <i>Applied or dismissed companies are hidden. Ask for 'all companies' to view the full directory.</i>"
        else:
            text += "\n💡 <i>Use <code>/check &lt;name&gt;</code> to scan any company live!</i>"
        return text.strip()
    if "stats" in result:
        stats = result["stats"]
        total = stats.get("total_tracked", 0)
        breakdown = stats.get("company_breakdown", {})
        text = f"📊 <b>Database Stats:</b>\n\n• <b>Total Roles:</b> {total}\n"
        if breakdown:
            text += "\n<b>Top Tracked Companies:</b>\n"
            for comp, count in list(breakdown.items())[:6]:
                text += f"• {html.escape(comp)}: {count}\n"
        return text.strip()
    return f"ℹ️ <b>{html.escape(name)}</b>: {html.escape(str(result.get('message', 'Completed')))}"


# Smart Rule-Based Fallback


async def _fallback_response(
    query: str, db_path: Optional[Path] = None, as_markdown: bool = False
) -> str:
    """Rule-based natural language parsing and intent matching when no LLM key is configured."""
    q = query.lower().strip()

    # 0. Stale applications / follow-ups summary intent
    is_stale_query = (
        any(phrase in q for phrase in [
            "stale", "followup", "follow-up", "followups", "follow ups", "pending followup",
            "pending followups", "stale application", "stale applications", "stale job", "stale jobs"
        ])
    )
    if is_stale_query:
        from gcc_job_radar.db import get_stale_applications
        stale = get_stale_applications(days=7, db_path=db_path)
        if as_markdown:
            if not stale:
                return (
                    "### Stale Applications Summary\n\n"
                    "• **All Applications Current** — No applications pending follow-up (≥ 7 days without status update).\n"
                    "• **Active Pipeline** — Keep submitting targeted applications using `/latest` or `/scan`.\n"
                    "• **Automatic Tracking** — Mark submissions with `/apply <id/company>` to enable automatic stale alerts."
                )
            lines = [
                f"### Stale Applications Summary (Pending ≥ 7 Days) — {len(stale)} Total\n",
                "**High-Priority Follow-ups:**",
            ]
            for j in stale[:8]:
                comp = j.get("company", "Company")
                title = j.get("title", "Role")
                app_days = j.get("applied_days", 7)
                lines.append(f"- [ ] **{comp} ({title})** — Stale for {app_days} days with no status change.")
            lines.append("\n**Recommended Next Steps:**")
            lines.append("• **Reach out on LinkedIn** — Message 1–2 technical recruiters or engineering managers.")
            lines.append("• **Check ATS Portal** — Confirm application status has not silently updated to in-review.")
            lines.append("• **Update Tracker** — Log contact date with `/apply <id> -n \"Followed up\"`.")
            return "\n".join(lines)
        else:
            if not stale:
                return (
                    "<b>Stale Applications Summary</b>\n\n"
                    "• <b>All Applications Current</b> — No applications pending follow-up (≥ 7 days without status update).\n"
                    "• <b>Active Pipeline</b> — Keep submitting targeted applications using <code>/latest</code> or <code>/scan</code>.\n"
                    "• <b>Automatic Tracking</b> — Mark submissions with <code>/apply &lt;id/company&gt;</code> to enable automatic stale alerts."
                )
            lines = [
                f"⏳ <b>Stale Applications Summary (Pending ≥ 7 Days) — {len(stale)} Total</b>\n",
                "<b>High-Priority Follow-ups:</b>",
            ]
            for j in stale[:8]:
                comp = html.escape(str(j.get("company", "Company")))
                title = html.escape(str(j.get("title", "Role")))
                app_days = j.get("applied_days", 7)
                lines.append(f"☐ <b>{comp} ({title})</b> — Stale for {app_days} days with no status change.")
            lines.append("\n<b>Recommended Next Steps:</b>")
            lines.append("• <b>Reach out on LinkedIn</b> — Message 1–2 technical recruiters or engineering managers.")
            lines.append("• <b>Check ATS Portal</b> — Confirm application status has not silently updated to in-review.")
            lines.append("• <b>Update Tracker</b> — Log contact date with <code>/apply &lt;id&gt; -n \"Followed up\"</code>.")
            return "\n".join(lines)

    # 0a0. Priorities & weekly action plan intent
    is_priority_query = (
        any(phrase in q for phrase in [
            "prioritize", "priority", "priorities", "focus on", "action plan",
            "what should i do", "what to do", "next step", "next steps", "weekly plan", "weekly priorities"
        ])
    )
    if is_priority_query:
        from gcc_job_radar.db import get_stale_applications
        stale = get_stale_applications(days=7, db_path=db_path)
        if as_markdown:
            lines = [
                "### Weekly Priorities & Action Plan\n",
                "**Immediate Action Items:**",
            ]
            if stale:
                for j in stale[:3]:
                    comp = j.get("company", "Company")
                    title = j.get("title", "Role")
                    app_days = j.get("applied_days", 7)
                    lines.append(f"- [ ] **Follow up on {comp}** — Stale for {app_days} days ({title}).")
            else:
                lines.append("- [ ] **Scan email job alerts** — Run `/email` to sync fresh alerts from your configured mailboxes.")
                lines.append("- [ ] **Explore new verified GCC roles** — Run `/latest` for new entry-level roles.")
            lines.append("- [ ] **Tailor resume for top match** — Select a high-relevance role and run `/tailor <id>`.\n")
            lines.append("**Strategic Focus:**")
            lines.append("• **Target Core Stack** — Prioritize Java, Spring Boot, and Backend roles (20–45 relevance pts).")
            lines.append("• **Pipeline Momentum** — Aim for 3–5 high-fit applications weekly for consistent interview momentum.")
            lines.append("• **Follow-up Discipline** — Re-engage recruiters 7–10 days post-submission.")
            return "\n".join(lines)
        else:
            lines = [
                "<b>Weekly Priorities &amp; Action Plan</b>\n",
                "<b>Immediate Action Items:</b>",
            ]
            if stale:
                for j in stale[:3]:
                    comp = html.escape(str(j.get("company", "Company")))
                    title = html.escape(str(j.get("title", "Role")))
                    app_days = j.get("applied_days", 7)
                    lines.append(f"☐ <b>Follow up on {comp}</b> — Stale for {app_days} days ({title}).")
            else:
                lines.append("☐ <b>Scan email job alerts</b> — Run <code>/email</code> to sync fresh alerts from your configured mailboxes.")
                lines.append("☐ <b>Explore new verified GCC roles</b> — Run <code>/latest</code> for new entry-level roles.")
            lines.append("☐ <b>Tailor resume for top match</b> — Select a high-relevance role and run <code>/tailor &lt;id&gt;</code>.\n")
            lines.append("<b>Strategic Focus:</b>")
            lines.append("• <b>Target Core Stack</b> — Prioritize Java, Spring Boot, and Backend roles (20–45 relevance pts).")
            lines.append("• <b>Pipeline Momentum</b> — Aim for 3–5 high-fit applications weekly for consistent interview momentum.")
            lines.append("• <b>Follow-up Discipline</b> — Re-engage recruiters 7–10 days post-submission.")
            return "\n".join(lines)

    # 0a0b. Filter rules & explanation intent
    is_filter_query = (
        ("filter" in q or "filtered" in q or "filtering" in q)
        and any(w in q for w in [
            "why", "how", "what", "criteria", "rule", "rules", "out", "exclude", "excluded", "drop", "dropped", "explain"
        ])
    )
    if is_filter_query:
        if as_markdown:
            return (
                "### GCC Job Radar Filtering Rules\n\n"
                "**Mandatory Inclusion Criteria:**\n"
                "• **Entry-Level Seniority** — Must match graduate, associate, junior, intern, or entry-level titles (0–2 years experience).\n"
                "• **Technical Roles** — Software engineering, backend, frontend, data, cloud, DevOps, QA, or cybersecurity.\n"
                "• **Location Verification** — Must be located in India (Bengaluru, Hyderabad, Pune, Gurgaon, etc.) or fully remote worldwide.\n\n"
                "**Automatic Exclusion Triggers:**\n"
                "• **Senior Titles** — Senior, Lead, Principal, Architect, Staff, Manager, Director (requires 3+ years experience).\n"
                "• **Foreign Locations** — Roles exclusively outside India without India/remote placement.\n"
                "• **Non-Tech Functions** — HR, sales, legal, marketing, administrative, and operations.\n"
                "• **Staffing Agencies** — Non-GCC third-party recruitment agencies and aggregators."
            )
        else:
            return (
                "<b>GCC Job Radar Filtering Rules</b>\n\n"
                "<b>Mandatory Inclusion Criteria:</b>\n"
                "• <b>Entry-Level Seniority</b> — Must match graduate, associate, junior, intern, or entry-level titles (0–2 years experience).\n"
                "• <b>Technical Roles</b> — Software engineering, backend, frontend, data, cloud, DevOps, QA, or cybersecurity.\n"
                "• <b>Location Verification</b> — Must be located in India (Bengaluru, Hyderabad, Pune, Gurgaon, etc.) or fully remote worldwide.\n\n"
                "<b>Automatic Exclusion Triggers:</b>\n"
                "• <b>Senior Titles</b> — Senior, Lead, Principal, Architect, Staff, Manager, Director (requires 3+ years experience).\n"
                "• <b>Foreign Locations</b> — Roles exclusively outside India without India/remote placement.\n"
                "• <b>Non-Tech Functions</b> — HR, sales, legal, marketing, administrative, and operations.\n"
                "• <b>Staffing Agencies</b> — Non-GCC third-party recruitment agencies and aggregators."
            )

    # 0. Question about points / score / pts (e.g. "what does these points means", "what is pts")
    is_points_query = (
        any(phrase in q for phrase in [
            "what does these points mean", "what do these points mean", "what does this point mean",
            "what does the points mean", "what do the points mean", "what is pts", "what are pts",
            "what are the points", "what does points mean", "what do points mean", "explain points",
            "how are points calculated", "relevance score", "score meaning", "points meaning",
            "what is the meaning of points", "what is the meaning of pts"
        ])
        or ("point" in q and any(w in q for w in ["what", "how", "mean", "why", "explain", "pts"]))
        or ("pts" in q and any(w in q for w in ["what", "mean", "why", "explain"]))
    )
    if is_points_query:
        if as_markdown:
            return (
                "### Personal Tech-Stack Relevance Score Explained\n\n"
                "**Relevance Point Breakdown (0 to 100):**\n"
                "• **Core Stack (+20 to +25 pts each):** Java, Spring Boot 3, MERN (MongoDB, Express, React, Node.js), Apache Kafka, MySQL\n"
                "• **Architecture & DevOps (+10 to +15 pts each):** Docker, JWT Authentication, GitHub Actions, CI/CD, Microservices / REST APIs\n"
                "• **Supporting Tech (+5 to +10 pts each):** TypeScript, SQL/PostgreSQL, Redis, Git, Linux\n"
                "• **Title Affinity Bonus (+10 to +20 pts):** Backend Developer, Full Stack, SDE / Software Engineer\n\n"
                "**Scoring Verdict:**\n"
                "• **Higher Points = Stronger Fit:** 30+ pts indicates direct technical alignment with your profile.\n"
                "• **Baseline Openings:** Roles with 0 pts are still verified entry-level tech openings, but without explicit keywords from your primary tech stack."
            )
        else:
            return (
                "🎯 <b>What do the points (e.g. <code>[15 pts]</code>, <code>[10 pts]</code>) mean?</b>\n\n"
                "The points represent your <b>Personal Tech-Stack Relevance Score</b> (0 to 100), calculated automatically for each role based on how strongly it matches your target skillset:\n\n"
                "• <b>Core Stack (+20 to +25 pts each):</b> Java, Spring Boot 3, MERN (MongoDB, Express, React, Node.js), Apache Kafka, MySQL\n"
                "• <b>Architecture & DevOps (+10 to +15 pts each):</b> Docker, JWT Authentication, GitHub Actions, CI/CD, Microservices / REST APIs\n"
                "• <b>Supporting Tech (+5 to +10 pts each):</b> TypeScript, SQL/PostgreSQL, Redis, Git, Linux\n"
                "• <b>Title Affinity Bonus (+10 to +20 pts):</b> Backend Developer, Full Stack, SDE / Software Engineer\n\n"
                "🏆 <b>Higher points = Stronger match</b> for your technical profile! Roles with 0 pts are still verified entry-level tech openings, but without explicit keywords from your primary tech stack in the job title/description."
            )

    # 0a. Query applied jobs intent (e.g. "pull out the applied sheet", "applied list", "show applied roles", "where are rest")
    is_applied_query = (

        q in ("applied", "applications", "applied list", "applied sheet", "my applications")
        or any(phrase in q for phrase in [
            "applied sheet", "applied list", "applied jobs", "applied roles",
            "show applied", "view applied", "my applications", "roles applied",
            "what did i apply", "where are rest", "where are the rest", "applications list"
        ])
    )
    if is_applied_query:
        res = await execute_tool("get_applied_jobs", {}, db_path=db_path)
        jobs = res.get("jobs", [])
        if not jobs:
            return (
                "ℹ️ <b>No Applied Roles Recorded</b>\n\n"
                "You haven't marked any roles as applied yet in the tracker database.\n"
                "Use <code>/apply &lt;id or company&gt;</code> or tell me which roles you applied to!"
            )
        return format_jobs_html(jobs, f"Your Applied Listings ({len(jobs)})")

    # 0a2. Query dismissed jobs intent (e.g. "dismissed list", "show dismissed roles", "name of all", "total dismissed companies")
    is_dismissed_query = (
        q in (
            "dismissed list", "dismissed jobs", "dismissed roles", "dismissed companies",
            "show dismissed", "view dismissed", "hidden jobs", "list dismissed",
            "name of all", "names of all", "name of all companies", "names of all companies",
            "total dismissed companies", "total dismissed", "all dismissed companies",
            "which companies are dismissed", "dismissed"
        )
        or any(phrase in q for phrase in [
            "dismissed list", "dismissed jobs", "dismissed roles", "dismissed companies",
            "show dismissed", "view dismissed", "hidden jobs", "hidden roles", "what did i dismiss",
            "which jobs are dismissed", "which companies are dismissed", "list of dismissed",
            "total dismissed", "dismissed company", "dismissed companies", "names of all dismissed",
            "name of all dismissed"
        ])
    )
    if is_dismissed_query:
        res = await execute_tool("get_dismissed_jobs", {}, db_path=db_path)
        total = res.get("total_dismissed_count", 0)
        companies = res.get("all_dismissed_companies", [])
        jobs = res.get("recent_dismissed_jobs", [])
        if total == 0:
            return (
                "ℹ️ <b>No Dismissed Roles Recorded</b>\n\n"
                "You haven't dismissed any roles or companies yet in the tracker database.\n"
                "Use <code>/dismiss &lt;id or company&gt;</code> or tell me to dismiss roles you're not interested in!"
            )

        comp_summary = ", ".join(companies)

        cards = []
        for j in jobs[:20]:
            jid = j.get("id")
            cname = html.escape(j.get("company", "Unknown"))
            title = html.escape(j.get("title", "Role"))
            loc = html.escape(j.get("location", ""))
            loc_str = f" • {loc}" if loc else ""
            cards.append(f"• <b>#{jid}. {cname}</b> — {title}{loc_str}")

        return (
            f"🗑️ <b>Dismissed Roles ({total} total across {len(companies)} companies):</b>\n\n"
            f"🏢 <b>All {len(companies)} Dismissed Companies:</b>\n"
            f"<i>{html.escape(comp_summary)}</i>\n\n"
            f"<b>Recent Dismissed Roles ({len(cards)} shown):</b>\n"
            + "\n".join(cards)
            + "\n\n💡 <i>To bring any role back to your active tracker, use <code>/restore &lt;id or company&gt;</code>.</i>"
        )


    # 0b. Job status management intent (dismiss, apply, restore)
    action_match = None
    target_match = None

    # Check for "mark <target> as applied"
    m_mark = re.match(r"^mark(?:ed)?\s+(.+?)\s+as\s+applied\b", q)
    if m_mark:
        action_match = "apply"
        target_match = query[m_mark.start(1) : m_mark.end(1)].strip()
    elif re.search(r"\b(?:applied|aoplies|applies)(?:\s+opening)?(?:\s+so\s+mark\s+.*)?$", q):
        action_match = "apply"
        clean_target = re.sub(r"\s+so\s+mark\s+.*$", "", query, flags=re.IGNORECASE)
        clean_target = re.sub(r"\s+(?:applied|aoplies|applies)(?:\s+opening)?$", "", clean_target, flags=re.IGNORECASE)
        target_match = clean_target.strip()
    else:
        m_lead = re.match(r"^(?:i\s+mean\s+)?(?:i\s+)?(?:have\s+|already\s+)?(?:applied|applies|apply)\s+(?:to\s+|for\s+)?", q)
        if m_lead and m_lead.end() < len(q):
            action_match = "apply"
            target_match = query[m_lead.end() :].strip()
        else:
            for verb, act in [
                ("applied to", "apply"),
                ("apply to", "apply"),
                ("mark applied", "apply"),
                ("applied", "apply"),
                ("apply", "apply"),
                ("dismiss", "dismiss"),
                ("hide", "dismiss"),
                ("restore", "restore"),
                ("undismiss", "restore"),
            ]:
                if q.startswith(verb + " "):
                    action_match = act
                    target_match = query[len(verb) :].strip()
                    break

    if action_match and target_match:
        notes = None
        m_note = re.search(r"(?:-n|--notes|notes?:)\s+(.+)$", target_match, flags=re.IGNORECASE)
        if m_note:
            notes = m_note.group(1).strip().strip('"').strip("'")
            target_match = target_match[: m_note.start()].strip()

        res = await execute_tool(
            "manage_job_status",
            {"action": action_match, "target": target_match, "notes": notes},
            db_path=db_path,
        )
        return format_tool_result_summary("manage_job_status", res)

    # 0c. General open jobs query (e.g. "all openings", "all jobs", "show all openings", "open positions")
    is_general_jobs_query = (
        q in ("all openings", "all jobs", "openings", "jobs", "all roles", "open roles")
        or any(phrase in q for phrase in [
            "all openings", "all the openings", "all jobs", "show all jobs", "list all jobs",
            "available jobs", "current openings", "every opening", "every job", "what openings",
            "what jobs", "show openings", "open positions"
        ])
    )
    if is_general_jobs_query:
        include_all = any(w in q for w in ["all", "every", "complete", "full"])
        res = await execute_tool("query_jobs", {"status": "NEW", "limit": 10, "include_all": include_all}, db_path=db_path)
        jobs = res.get("jobs", [])
        if jobs:
            header = "Active Verified GCC Openings"
            msg = format_jobs_html(jobs, header)
            msg += "\n\n💡 <i>Showing 10 recent active roles across 4,800+ GCCs. Filter by role or city (e.g. 'backend in Pune' or 'Python in Bangalore') or use <code>/latest</code>.</i>"
            return msg
        else:
            return (
                "ℹ️ <b>No Active Openings Found</b>\n\n"
                "There are currently no active roles marked as NEW in the local database.\n"
                "Run <code>/scan</code> to initiate a fresh scan across 4,800+ GCC career portals!"
            )

    # 0d. Email accounts scan intent (e.g. "go through all 3 email accounts", "check my emails", "scan email accounts", "check email for jobs")
    is_email_query = (
        any(phrase in q for phrase in [
            "email", "emails", "inbox", "mail", "mailbox", "mailboxes"
        ])
        and any(word in q for word in [
            "check", "scan", "go through", "pull", "fetch", "read", "sync", "look", "search", "find", "new", "relevant"
        ])
    )
    if is_email_query:
        res = await execute_tool("sync_email_jobs", {"days": 7, "limit": 15, "unread_only": False}, db_path=db_path)
        return format_tool_result_summary("sync_email_jobs", res)

    # 0e. Resume tailoring intent (e.g. "tailor resume for Flipkart", "generate resume for job 5", "tailor my resume for Amazon SDE-1")
    is_tailor_query = bool(re.search(r"\b(tailor|resume|cv)\b", q, re.IGNORECASE)) and any(
        w in q for w in ["tailor", "generate", "create", "build", "make", "custom", "align", "for", "download"]
    )
    if is_tailor_query:
        m_id = re.search(r"\b(?:job|id|#)?\s*(\d+)\b", q)
        target = m_id.group(1) if m_id else ""
        if not target:
            clean_q = re.sub(
                r"\b(tailor|my|resume|cv|for|the|a|an|generate|create|build|job|opening|please|can|you|now|me)\b",
                " ",
                q,
                flags=re.IGNORECASE,
            )
            target = clean_q.strip()
        if target:
            res = await execute_tool("tailor_job_resume", {"job_id": target, "company": target}, db_path=db_path)
            return format_tool_result_summary("tailor_job_resume", res)

    # 1. Greetings & capabilities
    if any(q.startswith(g) or q == g for g in ["hi", "hello", "hey", "who are you", "what can you do", "help"]):
        return (
            "👋 <b>Hello! I'm your GCC Job Radar Assistant.</b>\n\n"
            "I help you track entry-level tech roles in India across 4,800+ foreign GCCs, enterprise tech hubs, and your configured email job alerts.\n\n"
            "💡 <b>You can ask me:</b>\n"
            "• <i>\"Go through all 3 email accounts for new relevant jobs\"</i>\n"
            "• <i>\"Any Python or backend roles in Bangalore?\"</i>\n"
            "• <i>\"List all tracked companies\"</i>\n"
            "• <i>\"Show me entry-level jobs at Celonis\"</i>\n"
            "• <i>\"Check Databricks live\"</i>\n"
            "• <i>\"How many jobs are currently tracked?\"</i>\n\n"
            "Or use slash commands like <code>/scan</code>, <code>/email</code>, <code>/latest</code>, or <code>/stats</code>."
        )
    # 1b. Custom career scrapers / Non-ATS portal breakdown intent
    is_custom_scraper_query = (
        any(phrase in q for phrase in [
            "custom scraper", "custom scrapers", "not present on any ats", "not on any ats",
            "not on ats", "non ats", "non-ats", "without ats", "scrap of careers page",
            "scrape of careers page", "scrap of career page", "scrape of career page",
            "scrap career page", "scrape career page", "custom career", "career page scraper",
            "career page scrapers", "scrap of careers", "scrape of careers",
            "not present on ats", "not present in ats", "not in ats",
        ])
        or (
            any(name in q for name in ["flipkart", "apple", "cisco", "amazon", "microsoft", "ea"])
            and any(phrase in q for phrase in ["ats", "board", "scrap", "scrape", "how many", "present", "tracked"])
        )
    )
    if is_custom_scraper_query:
        if as_markdown:
            return (
                "### Career Page Scraper Architecture\n\n"
                "**Custom Career Portal Scrapers (6 Companies):**\n"
                "These companies do not use standard ATS platforms and are scraped via dedicated proprietary API/DOM scrapers:\n"
                "• **Flipkart** — Custom Turbohire API/DOM portal scraper\n"
                "• **Apple** — Official Apple Jobs Search API scraper\n"
                "• **Amazon** — Amazon.jobs Search API scraper\n"
                "• **Microsoft** — Microsoft Careers Search API scraper\n"
                "• **Electronic Arts (EA)** — EA Careers internal API scraper\n"
                "• **Majid Al Futtaim** — Phenom / SuccessFactors career API scraper\n"
                "*(Note: Cisco GCC is integrated via Workday ATS, not a custom portal scraper).*\n\n"
                "**Standard ATS Platform Integrations (5,307 Companies):**\n"
                "• **Greenhouse** — 2,044 companies\n"
                "• **Ashby** — 1,499 companies\n"
                "• **SmartRecruiters** — 999 companies\n"
                "• **Lever** — 704 companies\n"
                "• **Workday** — 61 companies\n\n"
                "**Total Tracked Companies:** 5,313"
            )
        else:
            return (
                "🛠️ <b>Career Page Scraper Architecture</b>\n\n"
                "<b>Custom Career Portal Scrapers (6 Companies):</b>\n"
                "These companies do not use standard ATS boards and are scraped via dedicated proprietary API/DOM scrapers:\n"
                "• <b>Flipkart</b> — Custom Turbohire portal scraper\n"
                "• <b>Apple</b> — Official Apple Jobs Search API scraper\n"
                "• <b>Amazon</b> — Amazon.jobs Search API scraper\n"
                "• <b>Microsoft</b> — Microsoft Careers Search API scraper\n"
                "• <b>Electronic Arts (EA)</b> — EA Careers internal API scraper\n"
                "• <b>Majid Al Futtaim</b> — Phenom / SuccessFactors career API scraper\n"
                "<i>(Note: Cisco GCC is integrated via Workday ATS, not a custom portal scraper).</i>\n\n"
                "📡 <b>Standard ATS Platform Integrations (5,307 Companies):</b>\n"
                "• <b>Greenhouse</b> — 2,044 companies\n"
                "• <b>Ashby</b> — 1,499 companies\n"
                "• <b>SmartRecruiters</b> — 999 companies\n"
                "• <b>Lever</b> — 704 companies\n"
                "• <b>Workday</b> — 61 companies\n\n"
                "🌐 <b>Total Tracked Registry:</b> 5,313 companies"
            )

    # 2. Company directory intent
    is_company_list_query = (
        any(phrase in q for phrase in [
            "list companies", "name all companies", "show gccs", "all companies",
            "company list", "list of companies", "show companies", "tracked companies",
            "list gccs", "supported companies", "which companies", "what companies",
        ])
        or (
            ("companies" in q or "gcc" in q or "gccs" in q)
            and any(w in q for w in ["which", "what", "list", "name", "show", "all", "track", "who"])
        )
    )
    if is_company_list_query:
        include_all = any(w in q for w in ["all", "every", "complete", "full"])
        comps = get_configured_companies(include_all=include_all, db_path=db_path)
        by_provider: dict[str, list[str]] = {}
        for c in comps:
            p_name = c["provider"].upper()
            if p_name not in by_provider:
                by_provider[p_name] = []
            by_provider[p_name].append(c["name"])

        title_str = f"All Tracked GCCs & Enterprise Tech Hubs ({len(comps)} total)" if include_all else f"Active Tracked GCCs & Enterprise Tech Hubs ({len(comps)})"
        text = f"🏢 <b>{title_str}:</b>\n\n"
        for provider, names in sorted(by_provider.items()):
            prominent = [n for n in ["Celonis", "Databricks", "Snowflake", "BT Group", "Google", "Microsoft"] if n in names]
            other = [n for n in sorted(names) if n not in prominent]
            sample_list = prominent + other[: max(1, 8 - len(prominent))]
            sample = ", ".join(sample_list)
            text += f"• <b>{provider}</b> ({len(names)} boards): e.g. <i>{sample}...</i>\n"
        if not include_all:
            applied_comps, dismissed_comps = get_applied_and_dismissed_companies(db_path)
            hidden_cnt = len(applied_comps | dismissed_comps)
            text += (
                f"\n💡 <i>{hidden_cnt} company/companies you already applied to or dismissed were hidden. "
                "Ask for 'all companies' to see the complete list.</i>"
            )
        else:
            text += (
                "\n💡 <i>All 4,800+ boards are monitored automatically. "
                "Use <code>/check &lt;name&gt;</code> (e.g. <code>/check celonis</code>) to scan any company live!</i>"
            )
        return text.strip()

    # 3. Database Stats intent
    if any(term in q for term in ["stats", "statistics", "how many", "count", "metrics", "total jobs"]):
        res = await execute_tool("get_tracking_stats", {}, db_path=db_path)
        stats = res.get("stats", {})
        total = stats.get("total_tracked", 0)
        breakdown = stats.get("company_breakdown", {})
        text = f"📊 <b>Database Stats:</b>\n\n• <b>Total Roles Tracked:</b> {total}\n"
        if breakdown:
            text += "\n<b>Top Tracked Companies:</b>\n"
            for comp, count in list(breakdown.items())[:6]:
                text += f"• {html.escape(comp)}: {count}\n"
        return text.strip()

    # 4. Check live intent
    matched_companies = [
        c
        for c in COMPANIES
        if re.search(r"\b" + re.escape(c.name.lower()) + r"\b", q)
        or re.search(r"\b" + re.escape(c.board_token.lower()) + r"\b", q)
    ]
    is_live_request = any(term in q for term in ["live", "check", "scan", "fresh", "now", "update"])

    if matched_companies and is_live_request:
        company = matched_companies[0]
        res = await execute_tool("check_company_live", {"company_name": company.name}, db_path=db_path)
        jobs = res.get("jobs", [])
        return format_jobs_html(jobs, f"Live Scan Results for {company.name}")

    # 5. Job query intent: match location, title keywords, or company
    locations = [
        "bangalore", "bengaluru", "hyderabad", "pune", "gurgaon", "gurugram",
        "noida", "chennai", "mumbai", "delhi", "remote"
    ]
    matched_location = next((loc for loc in locations if loc in q), None)

    title_keywords = [
        "software", "sde", "engineer", "developer", "backend", "frontend",
        "fullstack", "python", "java", "data", "machine learning", "ai",
        "cloud", "intern", "graduate", "analyst", "qa", "devops", "security"
    ]
    matched_title = next((kw for kw in title_keywords if kw in q), None)
    matched_comp_name = matched_companies[0].name if matched_companies else None

    if matched_location or matched_title or matched_comp_name:
        res = await execute_tool(
            "query_jobs",
            {
                "title_keyword": matched_title,
                "location": matched_location,
                "company": matched_comp_name,
                "limit": 5,
            },
            db_path=db_path,
        )
        jobs = res.get("jobs", [])
        header = "Matching Entry-Level Openings"
        criteria = []
        if matched_comp_name:
            criteria.append(matched_comp_name)
        if matched_title:
            criteria.append(matched_title.title())
        if matched_location:
            criteria.append(matched_location.title())
        if criteria:
            header += f" ({', '.join(criteria)})"

        if jobs:
            return format_jobs_html(jobs, header)
        else:
            return (
                f"ℹ️ <b>{html.escape(header)}</b>\n\n"
                f"No entry-level postings currently found in the local database matching your query.\n"
                f"Try <code>/scan</code> to refresh all 150+ boards or <code>/check {matched_comp_name or 'company'}</code> for a live scan."
            )

    # 6. Generic helpful response
    if as_markdown:
        return (
            "### GCC Job Radar Assistant Guidance\n\n"
            "I couldn't find an exact match for your request.\n\n"
            "**Recommended Career Commands & Queries:**\n"
            "• **Role Search** — e.g. \"Find Python jobs in Bangalore\"\n"
            "• **Live ATS Verification** — e.g. \"Check Celonis live\" or \"Scan Databricks\"\n"
            "• **Stale Applications** — e.g. \"Summarize my stale applications\"\n"
            "• **Weekly Priorities** — e.g. \"What should I prioritize this week?\"\n"
            "• **Email Alert Sync** — e.g. \"Go through all 3 email accounts for jobs\"\n"
            "• **Resume Tailoring** — e.g. \"Tailor resume for Celonis\""
        )
    return (
        "🤖 <b>GCC Job Radar Assistant Guidance</b>\n\n"
        "<i>I couldn't find an exact match for your request.</i>\n\n"
        "<b>Recommended Career Commands &amp; Queries:</b>\n"
        "• <b>Role Search</b> — e.g. \"Find Python jobs in Bangalore\"\n"
        "• <b>Live ATS Verification</b> — e.g. \"Check Celonis live\" or \"Scan Databricks\"\n"
        "• <b>Stale Applications</b> — e.g. \"Summarize my stale applications\"\n"
        "• <b>Weekly Priorities</b> — e.g. \"What should I prioritize this week?\"\n"
        "• <b>Email Alert Sync</b> — e.g. \"Go through all 3 email accounts for jobs\"\n"
        "• <b>Resume Tailoring</b> — e.g. \"Tailor resume for Celonis\""
    )


# LLM Callers (Gemini & OpenAI)


async def _call_gemini(
    prompt: str,
    history: list[dict[str, str]],
    api_key: str,
    client: httpx.AsyncClient,
    db_path: Optional[Path] = None,
    as_markdown: bool = False,
) -> Optional[str]:
    """Call Google Gemini REST API with multi-turn tool calling and conversational synthesis."""
    model = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    # Build multi-turn contents (keep recent turns to protect token budget)
    recent_history = history[-4:] if len(history) > 4 else history
    contents: list[dict[str, Any]] = []
    for turn in recent_history:
        role = "user" if turn["role"] == "user" else "model"
        turn_text = turn["content"]
        if len(turn_text) > 1500:
            turn_text = turn_text[:1500] + "..."
        contents.append({"role": role, "parts": [{"text": turn_text}]})
    contents.append({"role": "user", "parts": [{"text": prompt}]})

    payload: dict[str, Any] = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": contents,
        "tools": GEMINI_TOOLS,
    }

    last_tool_name = ""
    last_tool_result: dict[str, Any] = {}

    for _ in range(3):
        try:
            resp = await client.post(url, json=payload, timeout=20.0)
        except Exception as exc:
            err_msg = f"[AI Agent Error] Gemini request failed (connection/timeout): {exc}"
            _safe_print(err_msg, file=sys.stderr)
            logger.error(err_msg)
            break

        # If model is unavailable, rate-limited, or overloaded (503/429/404), attempt quick fallback
        if resp.status_code in (503, 429, 404):
            logger.warning("Gemini model %s returned HTTP %s. Retrying with fallback model...", model, resp.status_code)
            fallback_model = "gemini-flash-latest" if model != "gemini-flash-latest" else "gemini-3.1-flash-lite"
            url_fb = f"https://generativelanguage.googleapis.com/v1beta/models/{fallback_model}:generateContent?key={api_key}"
            try:
                await asyncio.sleep(1.0)
                resp = await client.post(url_fb, json=payload, timeout=20.0)
            except Exception as fb_exc:
                logger.error("Fallback Gemini request failed: %s", fb_exc)

        if resp.status_code != 200:
            err_msg = f"[AI Agent Error] Gemini API error (HTTP {resp.status_code}): {resp.text}"
            _safe_print(err_msg, file=sys.stderr)
            logger.error(err_msg)
            break

        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            err_msg = f"[AI Agent Error] Gemini API returned no candidates: {data}"
            _safe_print(err_msg, file=sys.stderr)
            logger.error(err_msg)
            break

        content = candidates[0].get("content", {})
        parts = content.get("parts", [])

        function_calls = [p["functionCall"] for p in parts if "functionCall" in p]
        if not function_calls:
            # Model provided direct text response without tool invocation (e.g. general questions or 2+2)
            text = "".join(p.get("text", "") for p in parts if "text" in p)
            if text:
                return text if as_markdown else markdown_to_telegram_html(text)
            err_msg = f"[AI Agent Error] Gemini candidate had no text and no functionCall: {parts}"
            _safe_print(err_msg, file=sys.stderr)
            logger.error(err_msg)
            break

        # Append model turn with tool call parts
        contents.append({"role": "model", "parts": parts})
        response_parts = []
        for fc in function_calls:
            tool_name = fc.get("name", "")
            tool_args = fc.get("args", {})
            tool_result = await execute_tool(tool_name, tool_args, db_path=db_path)
            last_tool_name = tool_name
            last_tool_result = tool_result
            response_parts.append({
                "functionResponse": {
                    "name": tool_name,
                    "response": tool_result,
                }
            })

        # Append user turn with functionResponse parts and loop back to model
        contents.append({"role": "user", "parts": response_parts})
        payload["contents"] = contents

    # If the LLM loop terminated without producing conversational text, summarize gracefully
    if last_tool_result:
        return format_tool_result_summary(last_tool_name, last_tool_result)
    return None


async def _call_openai_compatible(
    prompt: str,
    history: list[dict[str, str]],
    api_key: str,
    client: httpx.AsyncClient,
    base_url: str,
    model: str,
    provider_name: str = "OpenAI",
    db_path: Optional[Path] = None,
    as_markdown: bool = False,
) -> Optional[str]:
    """Call OpenAI-compatible REST API (OpenAI, Groq, etc.) with multi-turn tool calling."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    # Prune history to last 4 turns (2 conversational turns) and cap length to preserve token budget
    recent_history = history[-4:] if len(history) > 4 else history
    for turn in recent_history:
        turn_content = turn["content"]
        if len(turn_content) > 1500:
            turn_content = turn_content[:1500] + "..."
        messages.append({"role": turn["role"], "content": turn_content})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model,
        "messages": messages,
        "tools": OPENAI_TOOLS,
        "tool_choice": "auto",
    }

    last_tool_name = ""
    last_tool_result: dict[str, Any] = {}

    for _ in range(3):
        try:
            resp = await client.post(url, json=payload, headers=headers, timeout=20.0)
        except Exception as exc:
            err_msg = f"[AI Agent Error] {provider_name} request failed (connection/timeout): {exc}"
            _safe_print(err_msg, file=sys.stderr)
            logger.error(err_msg)
            return None

        # Handle rate limits or payload too large (e.g. Groq 413 / 429 TPM exhaustion)
        if resp.status_code in (413, 429):
            err_msg = f"[AI Agent Error] {provider_name} rate/size limit reached (HTTP {resp.status_code}): {resp.text}"
            _safe_print(err_msg, file=sys.stderr)
            logger.warning(err_msg)
            return None

        if resp.status_code != 200:
            err_msg = f"[AI Agent Error] {provider_name} API error (HTTP {resp.status_code}): {resp.text}"
            _safe_print(err_msg, file=sys.stderr)
            logger.error(err_msg)
            return None

        data = resp.json()
        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})
        tool_calls = msg.get("tool_calls", [])

        if not tool_calls:
            text = msg.get("content", "")
            if text:
                return text if as_markdown else markdown_to_telegram_html(text)
            err_msg = f"[AI Agent Error] {provider_name} response had no content and no tool_calls: {msg}"
            _safe_print(err_msg, file=sys.stderr)
            logger.error(err_msg)
            return None

        # Append assistant tool calls turn
        messages.append(msg)
        for tc in tool_calls:
            fn = tc.get("function", {})
            fn_name = fn.get("name", "")
            try:
                fn_args = json.loads(fn.get("arguments", "{}"))
            except Exception:
                fn_args = {}

            res = await execute_tool(fn_name, fn_args, db_path=db_path)
            last_tool_name = fn_name
            last_tool_result = res
            res_str = json.dumps(res)
            # Ensure tool result does not blow TPM budget (cap at 3500 chars)
            if len(res_str) > 3500:
                if isinstance(res, dict) and "jobs" in res and len(res["jobs"]) > 8:
                    res["jobs"] = res["jobs"][:8]
                    res["note"] = "Results capped to 8 for token budget."
                    res_str = json.dumps(res)
                if len(res_str) > 3500:
                    res_str = res_str[:3450] + '..."}'

            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id"),
                "content": res_str,
            })

        payload["messages"] = messages

    if last_tool_result:
        return format_tool_result_summary(last_tool_name, last_tool_result)
    return None


async def _call_groq(
    prompt: str,
    history: list[dict[str, str]],
    api_key: str,
    client: httpx.AsyncClient,
    db_path: Optional[Path] = None,
    as_markdown: bool = False,
) -> Optional[str]:
    """Call Groq REST API using high-performance open models (e.g. openai/gpt-oss-120b)."""
    base_url = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    return await _call_openai_compatible(
        prompt=prompt,
        history=history,
        api_key=api_key,
        client=client,
        base_url=base_url,
        model=model,
        provider_name="Groq",
        db_path=db_path,
        as_markdown=as_markdown,
    )


async def _call_openai(
    prompt: str,
    history: list[dict[str, str]],
    api_key: str,
    client: httpx.AsyncClient,
    db_path: Optional[Path] = None,
    as_markdown: bool = False,
) -> Optional[str]:
    """Call OpenAI REST API with multi-turn tool calling."""
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    return await _call_openai_compatible(
        prompt=prompt,
        history=history,
        api_key=api_key,
        client=client,
        base_url=base_url,
        model=model,
        provider_name="OpenAI",
        db_path=db_path,
        as_markdown=as_markdown,
    )


# Public Interface


async def ask_ai_agent(
    prompt: str,
    chat_id: str | int = "cli",
    db_path: Optional[Path] = None,
    client: Optional[httpx.AsyncClient] = None,
    as_markdown: bool = False,
) -> str:
    """Ask the conversational AI agent a question, returning formatted reply.

    Uses smart provider shifting:
    1. Primary: Gemini (if GEMINI_API_KEY is configured).
    2. Secondary: Groq (if GROQ_API_KEY is configured and Gemini fails or is unconfigured).
    3. Tertiary: OpenAI (if OPENAI_API_KEY is configured).
    4. Local Fallback: Rule-based deterministic NLP engine.
    """
    history = _chat_manager.get_history(chat_id)

    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    groq_key = os.getenv("GROQ_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")

    detected = []
    if gemini_key:
        detected.append("Gemini")
    if groq_key:
        detected.append("Groq")
    if openai_key:
        detected.append("OpenAI")

    if detected:
        _safe_print(f"[AI Agent] Available LLM providers: {', '.join(detected)} for prompt: '{prompt}'")
        logger.info("Available LLM providers: %s", ", ".join(detected))
    else:
        _safe_print(f"[AI Agent] Neither GEMINI_API_KEY nor GROQ_API_KEY nor OPENAI_API_KEY detected (using rule-based fallback) for prompt: '{prompt}'")
        logger.info("No LLM keys detected; using rule-based fallback")

    own_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=45.0)
        own_client = True

    try:
        response: Optional[str] = None

        primary_pref = os.getenv("PRIMARY_LLM_PROVIDER", "gemini").strip().lower()

        if primary_pref == "groq" and groq_key:
            # 1. Primary: Groq (ultra-fast ~2s LPU inference)
            _safe_print("[AI Agent] Attempting primary provider: Groq...")
            response = await _call_groq(prompt, history, groq_key, client, db_path=db_path, as_markdown=as_markdown)

            # 2. Smart shift to Gemini if Groq failed
            if not response and gemini_key:
                _safe_print("[AI Agent] [Shift] Smart shifting to Gemini (Groq unavailable or failed)...")
                logger.info("Smart shifting to Gemini")
                response = await _call_gemini(prompt, history, gemini_key, client, db_path=db_path, as_markdown=as_markdown)

            # 3. Tertiary: OpenAI
            if not response and openai_key:
                _safe_print("[AI Agent] [Shift] Shifting to OpenAI provider...")
                logger.info("Shifting to OpenAI provider")
                response = await _call_openai(prompt, history, openai_key, client, db_path=db_path, as_markdown=as_markdown)
        else:
            # Default primary: Gemini (with fast gemini-3.1-flash-lite and smart shifting)
            # 1. Primary: Gemini
            if gemini_key:
                _safe_print("[AI Agent] Attempting primary provider: Gemini...")
                response = await _call_gemini(prompt, history, gemini_key, client, db_path=db_path, as_markdown=as_markdown)

            # 2. Smart shift to Groq if Gemini failed or was unconfigured
            if not response and groq_key:
                if gemini_key:
                    _safe_print("[AI Agent] [Shift] Smart shifting to Groq (Gemini unavailable or failed)...")
                    logger.info("Smart shifting to Groq")
                else:
                    _safe_print("[AI Agent] Attempting provider: Groq...")
                    logger.info("Attempting provider: Groq")
                response = await _call_groq(prompt, history, groq_key, client, db_path=db_path, as_markdown=as_markdown)

            # 3. Tertiary: OpenAI
            if not response and openai_key:
                _safe_print("[AI Agent] [Shift] Shifting to OpenAI provider...")
                logger.info("Shifting to OpenAI provider")
                response = await _call_openai(prompt, history, openai_key, client, db_path=db_path, as_markdown=as_markdown)

        # 4. Final: Deterministic NLP fallback
        if not response:
            if detected:
                _safe_print("[AI Agent] [Notice] All configured LLMs failed; falling back to rule-based NLP engine")
                logger.warning("All LLMs failed; falling back to rule-based engine")
            response = await _fallback_response(prompt, db_path=db_path, as_markdown=as_markdown)

        # Update memory on success
        _chat_manager.add_turn(chat_id, "user", prompt)
        _chat_manager.add_turn(chat_id, "assistant", response)
        return response

    except Exception as exc:
        err_msg = f"[AI Agent Error] Exception during ask_ai_agent: {exc}"
        _safe_print(err_msg, file=sys.stderr)
        logger.error(err_msg)
        return await _fallback_response(prompt, db_path=db_path, as_markdown=as_markdown)

    finally:
        if own_client:
            await client.aclose()

