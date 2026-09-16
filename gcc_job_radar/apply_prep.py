"""Semi-Automated Job Application Preparation Engine.

Pre-fills ATS application forms (resume, name, email, phone, LinkedIn, standard screening questions)
for high-scoring roles (⭐ Best Fit) and halts strictly before the final submit button.
Supports Tier A automation (Greenhouse, Lever, Ashby) and Tier B draft-only fallback (Workday, etc.).
"""

import asyncio
from dataclasses import dataclass, field
import html
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Optional, Sequence, Union

import httpx

from gcc_job_radar.db import (
    get_db_path,
    has_prepped_application,
    is_company_dismissed,
    record_apply_prep_attempt,
)
from gcc_job_radar.dormant_companies import is_dormant_company
from gcc_job_radar.models import ATSProvider, JobPosting

logger = logging.getLogger(__name__)

# Strict blacklist of submit button selectors that MUST NEVER be clicked
SUBMIT_BUTTON_SELECTORS = [
    "button[type='submit']",
    "input[type='submit']",
    "#submit_app",
    "button#submit_app",
    "button.template-btn-submit",
    "button:has-text('Submit Application')",
    "button:has-text('Submit')",
    "button:has-text('Apply Now')",
    "input[value*='Submit']",
    "input[value*='Apply']",
]

TIER_A_PROVIDERS = {
    ATSProvider.GREENHOUSE,
    ATSProvider.LEVER,
    ATSProvider.ASHBY,
}


@dataclass
class FormPrepResult:
    """Outcome of an application preparation attempt."""

    job_id: str
    company: str
    title: str
    apply_url: str
    status: str  # 'READY_LOCAL', 'READY_REMOTE', 'FALLBACK_DRAFT', 'ALREADY_PREPPED', 'FAILED'
    prefilled_fields: list[str] = field(default_factory=list)
    drafted_questions: list[dict[str, Any]] = field(default_factory=list)
    manual_fields: list[str] = field(default_factory=list)
    interactive_url: Optional[str] = None
    message: str = ""
    error: Optional[str] = None
    tailored_pdf_path: Optional[str] = None
    tailored_tex_path: Optional[str] = None


def load_candidate_profile() -> dict[str, Any]:
    """Load candidate details from profile.json or environment variables."""
    profile: dict[str, Any] = {
        "name": "Chinmay Maheshwari",
        "first_name": "Chinmay",
        "last_name": "Maheshwari",
        "email": "chinmaymaheshwari.it27@gmail.com",
        "phone": "+91 9460449962",
        "location": "Udaipur, India",
        "linkedin": "https://linkedin.com/in/chinmay8064/",
        "github": "https://github.com/Chinmay0608",
        "portfolio": "https://portfolio-olive-nine-39.vercel.app",
        "degree": "Bachelor of Technology in Information Technology",
        "college": "Jaipur Engineering College and Research Centre",
        "graduation": "Expected May/June 2027",
        "cgpa": "8.8",
        "work_authorization": "Yes, legally authorized to work in India",
        "visa_sponsorship": "No visa sponsorship required in India",
        "notice_period": "Immediate / Available upon graduation May 2027",
    }

    candidates = [
        Path(os.getenv("CANDIDATE_PROFILE_PATH", "")),
        Path("builder/job_toolkit/profile.json"),
        Path(__file__).resolve().parent.parent / "builder" / "job_toolkit" / "profile.json",
        Path("profile.json"),
    ]
    for p in candidates:
        if p and p.is_file():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        profile.update(data)
                        if "name" in data and "first_name" not in data:
                            parts = str(data["name"]).strip().split()
                            profile["first_name"] = parts[0] if parts else ""
                            profile["last_name"] = " ".join(parts[1:]) if len(parts) > 1 else ""
                        break
            except Exception as exc:
                logger.debug("Failed loading profile from %s: %s", p, exc)

    # Environment overrides
    if os.getenv("CANDIDATE_NAME"):
        profile["name"] = os.environ["CANDIDATE_NAME"]
        parts = profile["name"].split()
        profile["first_name"] = parts[0] if parts else ""
        profile["last_name"] = " ".join(parts[1:]) if len(parts) > 1 else ""
    if os.getenv("CANDIDATE_FIRST_NAME"):
        profile["first_name"] = os.environ["CANDIDATE_FIRST_NAME"]
    if os.getenv("CANDIDATE_LAST_NAME"):
        profile["last_name"] = os.environ["CANDIDATE_LAST_NAME"]
    if os.getenv("CANDIDATE_EMAIL"):
        profile["email"] = os.environ["CANDIDATE_EMAIL"]
    if os.getenv("CANDIDATE_PHONE"):
        profile["phone"] = os.environ["CANDIDATE_PHONE"]
    if os.getenv("CANDIDATE_LINKEDIN"):
        profile["linkedin"] = os.environ["CANDIDATE_LINKEDIN"]
    if os.getenv("CANDIDATE_GITHUB"):
        profile["github"] = os.environ["CANDIDATE_GITHUB"]
    if os.getenv("CANDIDATE_PORTFOLIO"):
        profile["portfolio"] = os.environ["CANDIDATE_PORTFOLIO"]

    return profile


def resolve_candidate_resume_pdf(tailored_pdf: Optional[str] = None) -> Optional[Path]:
    """Locate best candidate resume PDF (tailored PDF if exists, or master resume PDF)."""
    if tailored_pdf:
        p = Path(tailored_pdf)
        if p.is_file():
            return p.resolve()

    candidates = [
        Path("Chinmay_Resume.pdf"),
        Path("D:/Chinmay_Resume.pdf"),
        Path("master_resume.pdf"),
        Path("builder/master_resume.pdf"),
        Path("tailored/master_resume.pdf"),
    ]
    for p in candidates:
        if p.is_file():
            return p.resolve()

    # Search in tailored/ directory for any recent pdf
    tailored_dir = Path("tailored")
    if tailored_dir.is_dir():
        pdfs = list(tailored_dir.glob("*.pdf"))
        if pdfs:
            pdfs.sort(key=lambda f: f.stat().st_mtime, reverse=True)
            return pdfs[0].resolve()

    return None


async def draft_screening_answer(
    question_text: str,
    job: JobPosting,
    profile: dict[str, Any],
    client: Optional[httpx.AsyncClient] = None,
) -> tuple[str, bool]:
    """Draft an answer for a job screening question.

    Returns:
        (answer_text, is_ai_drafted)
    Guardrails:
    - Never invent factual credentials (experience years, past employers, degrees).
    - If answer cannot be deduced from candidate profile, marks [NEEDS MANUAL INPUT].
    """
    q = (question_text or "").strip().lower()

    # 1. Deterministic standard factual questions
    if any(k in q for k in ["authorized to work", "legally authorized", "work authorization", "eligible to work in india"]):
        return "Yes, I am an Indian citizen and legally authorized to work in India.", False

    if any(k in q for k in ["visa", "sponsorship", "require sponsorship", "future require"]):
        return "No, I do not require visa sponsorship to work in India.", False

    if any(k in q for k in ["notice period", "availability", "how soon can you start", "earliest start", "when can you start"]):
        return "Available immediately for internships / upon graduation in May 2027.", False

    if any(k in q for k in ["graduation year", "year of graduation", "batch", "passing year"]):
        return "2027 (Expected May/June 2027, B.Tech in Information Technology).", False

    if any(k in q for k in ["cgpa", "gpa", "percentage", "marks"]):
        cgpa = profile.get("cgpa", "8.8")
        return f"{cgpa} CGPA (Jaipur Engineering College and Research Centre).", False

    if any(k in q for k in ["current salary", "expected salary", "ctc", "expected ctc", "compensation"]):
        return "[NEEDS MANUAL INPUT — As per industry standard / open to discussion]", False

    if any(k in q for k in ["university", "college", "institute", "school"]):
        return profile.get("college", "Jaipur Engineering College and Research Centre"), False

    if any(k in q for k in ["degree", "major", "field of study"]):
        return profile.get("degree", "Bachelor of Technology in Information Technology"), False

    # 2. AI-assisted drafting for motivation / technical questions
    if any(k in q for k in ["why do you want", "why are you interested", "why should we hire", "cover letter", "about yourself", "relevant experience", "tell us about"]):
        prompt = (
            f"Candidate: {profile.get('name', 'Chinmay Maheshwari')}, 3rd-year B.Tech IT student (graduating 2027). "
            f"Core stack: Java, Spring Boot, React, Node.js, REST APIs, Kafka, MongoDB. "
            f"Projects: Skill-Bridge (Job portal) and MindVault (Distributed journal system). "
            f"Job: {job.title} at {job.company}. "
            f"Question: \"{question_text}\"\n"
            f"Task: Write a concise, professional 2-3 sentence answer (under 50 words). "
            f"STRICT RULES: Do NOT invent fake previous jobs or years of experience. Focus on eagerness to contribute, relevant technical foundations, and quick learning. Return only the raw text response."
        )
        try:
            from gcc_job_radar.ai_agent import ask_ai_agent
            draft = await ask_ai_agent(prompt, client=client, chat_id="apply_prep_draft", as_markdown=False)
            clean_draft = re.sub(r"^(Answer|Draft):\s*", "", draft.strip(), flags=re.IGNORECASE)
            if clean_draft and len(clean_draft) > 10:
                return clean_draft, True
        except Exception as exc:
            logger.debug("Failed AI drafting for question '%s': %s", question_text, exc)

    # 3. Fallback placeholder for unrecognized custom questions
    return f"[NEEDS MANUAL INPUT for: {question_text[:60]}]", False


async def prep_greenhouse_form(
    page: Any,
    job: JobPosting,
    profile: dict[str, Any],
    resume_pdf: Optional[Path],
    client: Optional[httpx.AsyncClient] = None,
) -> dict[str, Any]:
    """Fill standard Greenhouse form inputs, strictly stopping before submit button."""
    prefilled = []
    drafted_qs = []
    manual_fields = []

    # First Name & Last Name
    first_name = profile.get("first_name", "Chinmay")
    last_name = profile.get("last_name", "Maheshwari")

    for sel in ["#first_name", "input[id*='first_name']", "input[name*='first_name']"]:
        if await page.locator(sel).count() > 0:
            await page.fill(sel, first_name)
            prefilled.append(f"First Name: {first_name}")
            break

    for sel in ["#last_name", "input[id*='last_name']", "input[name*='last_name']"]:
        if await page.locator(sel).count() > 0:
            await page.fill(sel, last_name)
            prefilled.append(f"Last Name: {last_name}")
            break

    # Email
    email = profile.get("email", "")
    for sel in ["#email", "input[id*='email']", "input[type='email']", "input[name*='email']"]:
        if await page.locator(sel).count() > 0:
            await page.fill(sel, email)
            prefilled.append(f"Email: {email}")
            break

    # Phone
    phone = profile.get("phone", "")
    for sel in ["#phone", "input[id*='phone']", "input[type='tel']", "input[name*='phone']"]:
        if await page.locator(sel).count() > 0:
            await page.fill(sel, phone)
            prefilled.append(f"Phone: {phone}")
            break

    # Resume Upload
    if resume_pdf and resume_pdf.is_file():
        file_inputs = page.locator("input[type='file']")
        if await file_inputs.count() > 0:
            try:
                await file_inputs.first.set_input_files(str(resume_pdf))
                prefilled.append(f"Resume: {resume_pdf.name}")
            except Exception as exc:
                logger.debug("Greenhouse file input set failed: %s", exc)
                manual_fields.append("Resume Upload")
    else:
        manual_fields.append("Resume Upload (no PDF found)")

    # LinkedIn / Website / GitHub
    linkedin = profile.get("linkedin", "")
    website = profile.get("portfolio", "")
    github = profile.get("github", "")

    inputs = page.locator("form input[type='text'], form textarea")
    input_count = await inputs.count()
    for i in range(input_count):
        inp = inputs.nth(i)
        name_attr = (await inp.get_attribute("name") or "").lower()
        id_attr = (await inp.get_attribute("id") or "").lower()

        # Skip if already filled
        try:
            val = await inp.input_value()
            if val:
                continue
        except Exception:
            pass

        if "linkedin" in name_attr or "linkedin" in id_attr:
            await inp.fill(linkedin)
            prefilled.append("LinkedIn Profile")
        elif "github" in name_attr or "github" in id_attr:
            await inp.fill(github)
            prefilled.append("GitHub Profile")
        elif "website" in name_attr or "portfolio" in name_attr or "website" in id_attr:
            await inp.fill(website)
            prefilled.append("Portfolio Website")
        else:
            try:
                is_vis = await inp.is_visible()
            except Exception:
                is_vis = True
            if is_vis:
                label_el = page.locator(f"label[for='{id_attr}']") if id_attr else None
                label_text = await label_el.inner_text() if label_el and await label_el.count() > 0 else ""
                if not label_text:
                    label_text = name_attr

                if label_text:
                    answer, is_ai = await draft_screening_answer(label_text, job, profile, client=client)
                    await inp.fill(answer)
                    drafted_qs.append({"question": label_text, "answer": answer, "is_ai": is_ai})
                    if "[NEEDS MANUAL INPUT" in answer:
                        manual_fields.append(label_text)

    return {
        "prefilled": prefilled,
        "drafted_questions": drafted_qs,
        "manual_fields": manual_fields,
    }


async def prep_lever_form(
    page: Any,
    job: JobPosting,
    profile: dict[str, Any],
    resume_pdf: Optional[Path],
    client: Optional[httpx.AsyncClient] = None,
) -> dict[str, Any]:
    """Fill standard Lever form inputs, strictly stopping before submit button."""
    prefilled = []
    drafted_qs = []
    manual_fields = []

    # Navigate to /apply if currently on the job description view
    apply_button = page.locator("a.postings-btn[href*='/apply'], a:has-text('Apply for this job')")
    if await apply_button.count() > 0:
        try:
            await apply_button.first.click()
            await page.wait_for_load_state("networkidle")
        except Exception:
            pass

    # Full Name
    name = profile.get("name", "Chinmay Maheshwari")
    if await page.locator("input[name='name']").count() > 0:
        await page.fill("input[name='name']", name)
        prefilled.append(f"Full Name: {name}")

    # Email
    email = profile.get("email", "")
    if await page.locator("input[name='email']").count() > 0:
        await page.fill("input[name='email']", email)
        prefilled.append(f"Email: {email}")

    # Phone
    phone = profile.get("phone", "")
    if await page.locator("input[name='phone']").count() > 0:
        await page.fill("input[name='phone']", phone)
        prefilled.append(f"Phone: {phone}")

    # Current Org / University
    org = profile.get("college", "Jaipur Engineering College and Research Centre")
    if await page.locator("input[name='org']").count() > 0:
        await page.fill("input[name='org']", org)
        prefilled.append(f"Current Org / University: {org}")

    # Social links
    if await page.locator("input[name='urls[LinkedIn]']").count() > 0:
        await page.fill("input[name='urls[LinkedIn]']", profile.get("linkedin", ""))
        prefilled.append("LinkedIn Profile")

    if await page.locator("input[name='urls[GitHub]']").count() > 0:
        await page.fill("input[name='urls[GitHub]']", profile.get("github", ""))
        prefilled.append("GitHub Profile")

    if await page.locator("input[name='urls[Portfolio]']").count() > 0:
        await page.fill("input[name='urls[Portfolio]']", profile.get("portfolio", ""))
        prefilled.append("Portfolio Website")
    elif await page.locator("input[name='urls[Other]']").count() > 0:
        await page.fill("input[name='urls[Other]']", profile.get("portfolio", ""))
        prefilled.append("Other Link: Portfolio")

    # Resume Upload
    if resume_pdf and resume_pdf.is_file():
        file_input = page.locator("input[type='file'][name='resume'], input[type='file']")
        if await file_input.count() > 0:
            try:
                await file_input.first.set_input_files(str(resume_pdf))
                prefilled.append(f"Resume: {resume_pdf.name}")
            except Exception as exc:
                logger.debug("Lever resume upload failed: %s", exc)
                manual_fields.append("Resume Upload")
    else:
        manual_fields.append("Resume Upload (no PDF found)")

    # Custom screening questions (.application-question)
    questions = page.locator(".application-question")
    q_count = await questions.count()
    for i in range(q_count):
        q_el = questions.nth(i)
        label_el = q_el.locator(".text, label")
        label_text = await label_el.inner_text() if await label_el.count() > 0 else ""
        textarea = q_el.locator("textarea, input[type='text']")
        if await textarea.count() > 0 and label_text:
            ans, is_ai = await draft_screening_answer(label_text, job, profile, client=client)
            await textarea.first.fill(ans)
            drafted_qs.append({"question": label_text, "answer": ans, "is_ai": is_ai})
            if "[NEEDS MANUAL INPUT" in ans:
                manual_fields.append(label_text)

    return {
        "prefilled": prefilled,
        "drafted_questions": drafted_qs,
        "manual_fields": manual_fields,
    }


async def prep_ashby_form(
    page: Any,
    job: JobPosting,
    profile: dict[str, Any],
    resume_pdf: Optional[Path],
    client: Optional[httpx.AsyncClient] = None,
) -> dict[str, Any]:
    """Fill standard Ashby form inputs, strictly stopping before submit button."""
    prefilled = []
    drafted_qs = []
    manual_fields = []

    # Ashby forms might be full-name or first+last name
    if await page.locator("input[name='name']").count() > 0:
        await page.fill("input[name='name']", profile.get("name", "Chinmay Maheshwari"))
        prefilled.append(f"Full Name: {profile.get('name')}")
    else:
        if await page.locator("input[name='firstName']").count() > 0:
            await page.fill("input[name='firstName']", profile.get("first_name", "Chinmay"))
            prefilled.append(f"First Name: {profile.get('first_name')}")
        if await page.locator("input[name='lastName']").count() > 0:
            await page.fill("input[name='lastName']", profile.get("last_name", "Maheshwari"))
            prefilled.append(f"Last Name: {profile.get('last_name')}")

    # Email
    if await page.locator("input[name='email']").count() > 0:
        await page.fill("input[name='email']", profile.get("email", ""))
        prefilled.append(f"Email: {profile.get('email')}")

    # Phone
    phone_sel = "input[name='phone'], input[name='phoneNumber']"
    if await page.locator(phone_sel).count() > 0:
        await page.locator(phone_sel).first.fill(profile.get("phone", ""))
        prefilled.append(f"Phone: {profile.get('phone')}")

    # Resume Upload
    if resume_pdf and resume_pdf.is_file():
        file_input = page.locator("input[type='file']")
        if await file_input.count() > 0:
            try:
                await file_input.first.set_input_files(str(resume_pdf))
                prefilled.append(f"Resume: {resume_pdf.name}")
            except Exception as exc:
                logger.debug("Ashby resume upload failed: %s", exc)
                manual_fields.append("Resume Upload")
    else:
        manual_fields.append("Resume Upload (no PDF found)")

    # Links
    links = [
        ("linkedin", profile.get("linkedin", ""), "LinkedIn Profile"),
        ("github", profile.get("github", ""), "GitHub Profile"),
        ("website", profile.get("portfolio", ""), "Portfolio Website"),
    ]
    for key, val, label in links:
        inp = page.locator(f"input[name*='{key}'], input[id*='{key}']")
        if await inp.count() > 0:
            await inp.first.fill(val)
            prefilled.append(label)

    return {
        "prefilled": prefilled,
        "drafted_questions": drafted_qs,
        "manual_fields": manual_fields,
    }


async def _launch_browser_session():
    """Launch Playwright browser session supporting local headful or remote CDP."""
    from playwright.async_api import async_playwright

    playwright = await async_playwright().start()

    remote_ws = os.getenv("PLAYWRIGHT_WS_ENDPOINT")
    browserbase_key = os.getenv("BROWSERBASE_API_KEY")

    if browserbase_key:
        try:
            async with httpx.AsyncClient(timeout=15.0) as http:
                resp = await http.post(
                    "https://api.browserbase.com/v1/sessions",
                    headers={"X-BB-API-Key": browserbase_key, "Content-Type": "application/json"},
                    json={"projectId": os.getenv("BROWSERBASE_PROJECT_ID", "")},
                )
                if resp.status_code in (200, 201):
                    session_data = resp.json()
                    connect_url = session_data.get("connectUrl")
                    live_url = session_data.get("liveUrls", {}).get("userUrl") or session_data.get("liveUrl")
                    browser = await playwright.chromium.connect_over_cdp(connect_url)
                    return playwright, browser, live_url, "READY_REMOTE"
        except Exception as exc:
            logger.warning("Failed connecting to Browserbase: %s; attempting local fallback...", exc)

    if remote_ws:
        try:
            browser = await playwright.chromium.connect(remote_ws)
            return playwright, browser, remote_ws, "READY_REMOTE"
        except Exception as exc:
            logger.warning("Failed connecting to remote WS endpoint: %s", exc)

    # Local headful desktop session
    browser = await playwright.chromium.launch(
        headless=False,
        args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
    )
    return playwright, browser, None, "READY_LOCAL"


async def prep_job_application(
    job: JobPosting,
    tailored_pdf_path: Optional[str] = None,
    tailored_tex_path: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
    db_path: Optional[Path] = None,
    force: bool = False,
) -> FormPrepResult:
    """Execute application prep flow for a job posting.

    Guardrails:
    1. Rejects dismissed roles and dormant companies.
    2. Rate limits (only preps once per job unless force=True).
    3. Never clicks final submit button.
    4. Handles Tier A (automation) and Tier B (draft-only fallback).
    5. Never invents facts; flags AI-drafted questions.
    """
    job_id = job.numeric_id if job.numeric_id is not None else job.id
    target_db = get_db_path(db_path)

    # 1. Guardrail: Dormant or Dismissed check
    if is_dormant_company(job.company):
        return FormPrepResult(
            job_id=str(job_id),
            company=job.company,
            title=job.title,
            apply_url=str(job.apply_url),
            status="FAILED",
            message=f"⛔ Application prep skipped: {job.company} is marked as dormant.",
        )

    if is_company_dismissed(job.company, db_path=target_db):
        return FormPrepResult(
            job_id=str(job_id),
            company=job.company,
            title=job.title,
            apply_url=str(job.apply_url),
            status="FAILED",
            message=f"⛔ Application prep skipped: {job.company} has been dismissed.",
        )

    # 2. Guardrail: Rate limit (already prepped)
    if not force and has_prepped_application(job_id, db_path=target_db):
        return FormPrepResult(
            job_id=str(job_id),
            company=job.company,
            title=job.title,
            apply_url=str(job.apply_url),
            status="ALREADY_PREPPED",
            message=f"Application for {job.company} — {job.title} was already prepared. Check your existing session or click Applied when finished.",
            tailored_pdf_path=tailored_pdf_path,
            tailored_tex_path=tailored_tex_path,
        )

    # Load profile and resume PDF
    profile = load_candidate_profile()
    resume_pdf = resolve_candidate_resume_pdf(tailored_pdf_path)

    # 3. Tier B (Draft-Only Fallback for Workday, SmartRecruiters, custom scrapers)
    if job.provider not in TIER_A_PROVIDERS:
        details_msg = f"Draft-only fallback for {job.provider.value.upper()}"
        record_apply_prep_attempt(
            job_id=job_id,
            company=job.company,
            title=job.title,
            provider=job.provider.value,
            status="FALLBACK",
            details=details_msg,
            db_path=target_db,
        )
        return FormPrepResult(
            job_id=str(job_id),
            company=job.company,
            title=job.title,
            apply_url=str(job.apply_url),
            status="FALLBACK_DRAFT",
            prefilled_fields=[
                f"Candidate Name: {profile.get('name')}",
                f"Candidate Email: {profile.get('email')}",
                f"Candidate Phone: {profile.get('phone')}",
                f"LinkedIn: {profile.get('linkedin')}",
                f"GitHub: {profile.get('github')}",
            ],
            drafted_questions=[
                {
                    "question": "Work Authorization in India",
                    "answer": "Yes, legally authorized to work in India without sponsorship.",
                    "is_ai": False,
                },
                {
                    "question": "Availability / Graduation",
                    "answer": "Expected May/June 2027 (available immediately for internships).",
                    "is_ai": False,
                },
            ],
            manual_fields=["Account Login / Portal Registration required on target ATS"],
            message=f"ℹ️ Form automation is in <b>Draft-Only</b> mode for <b>{job.provider.value.upper()}</b> (multi-step authentication portal). Your tailored resume and direct apply link are ready below.",
            tailored_pdf_path=str(resume_pdf) if resume_pdf else None,
            tailored_tex_path=tailored_tex_path,
        )

    # 4. Tier A Automation via Playwright
    playwright_ctx = None
    browser = None
    try:
        playwright_ctx, browser, live_url, mode = await _launch_browser_session()
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await context.new_page()

        # Navigate to apply URL
        target_url = str(job.apply_url)
        await page.goto(target_url, timeout=35000, wait_until="domcontentloaded")
        await asyncio.sleep(2.0)

        fill_summary = {}
        if job.provider == ATSProvider.GREENHOUSE:
            fill_summary = await prep_greenhouse_form(page, job, profile, resume_pdf, client=client)
        elif job.provider == ATSProvider.LEVER:
            fill_summary = await prep_lever_form(page, job, profile, resume_pdf, client=client)
        elif job.provider == ATSProvider.ASHBY:
            fill_summary = await prep_ashby_form(page, job, profile, resume_pdf, client=client)

        prefilled = fill_summary.get("prefilled", [])
        drafted_qs = fill_summary.get("drafted_questions", [])
        manual = fill_summary.get("manual_fields", [])

        # Audit log success
        record_apply_prep_attempt(
            job_id=job_id,
            company=job.company,
            title=job.title,
            provider=job.provider.value,
            status="SUCCESS",
            details=f"Mode: {mode}, Prefilled {len(prefilled)} fields, Drafted {len(drafted_qs)} questions",
            db_path=target_db,
        )

        return FormPrepResult(
            job_id=str(job_id),
            company=job.company,
            title=job.title,
            apply_url=str(job.apply_url),
            status=mode,
            prefilled_fields=prefilled,
            drafted_questions=drafted_qs,
            manual_fields=manual,
            interactive_url=live_url,
            message="Application pre-filled successfully! Paused before final submission.",
            tailored_pdf_path=str(resume_pdf) if resume_pdf else None,
            tailored_tex_path=tailored_tex_path,
        )

    except Exception as exc:
        logger.warning("Form automation failed for %s (%s): %s", job.company, job.provider.value, exc)
        # Record failure and fall back to draft mode
        record_apply_prep_attempt(
            job_id=job_id,
            company=job.company,
            title=job.title,
            provider=job.provider.value,
            status="FAILED",
            details=str(exc),
            db_path=target_db,
        )
        return FormPrepResult(
            job_id=str(job_id),
            company=job.company,
            title=job.title,
            apply_url=str(job.apply_url),
            status="FALLBACK_DRAFT",
            prefilled_fields=[
                f"Candidate Name: {profile.get('name')}",
                f"Candidate Email: {profile.get('email')}",
                f"Candidate Phone: {profile.get('phone')}",
            ],
            drafted_questions=[],
            manual_fields=["Review and complete manually on career site"],
            message=f"⚠️ Browser automation could not complete ({exc}). Here is your tailored resume and direct apply link.",
            error=str(exc),
            tailored_pdf_path=str(resume_pdf) if resume_pdf else None,
            tailored_tex_path=tailored_tex_path,
        )
