"""Email application status and rejection detector across configured IMAP mailboxes."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import difflib
import email
from email import policy
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from enum import Enum
import html
import imaplib
import logging
from pathlib import Path
import re
import sqlite3
from typing import Any, List, Optional, Tuple

from .db import get_applied_jobs, get_db_path, init_db, mark_job_status

logger = logging.getLogger(__name__)


class RevertType(Enum):
    INTERVIEW = "INTERVIEW"
    OA_INVITE = "OA_INVITE"
    REJECTED = "REJECTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    UNKNOWN = "UNKNOWN"


@dataclass
class RevertMatch:
    company: str
    revert_type: RevertType
    subject: str
    sender: str
    date: str
    snippet: str
    matched_job_id: Optional[int] = None
    role: str = ""
    account: str = ""


# Senders that only send job alert digests, newsletters, or platform notifications (never personal rejections)
_NOISE_SENDER_SUBSTRINGS = (
    "jobalerts-noreply@linkedin.com",
    "jobs-noreply@linkedin.com",
    "messages-noreply@linkedin.com",
    "updates-noreply@linkedin.com",
    "notifications-noreply@linkedin.com",
    "newsletters-noreply@linkedin.com",
    "career-interests-noreply@linkedin.com",
    "donotreply@match.indeed.com",
    "donotreply@jobalert.indeed.com",
    "alert@indeed.com",
    "alerts@indeed.com",
    "no-reply@indeed.com",
    "indeedapply@indeed.com",
    "alerts@naukri.com",
    "alert@naukri.com",
    "jobalerts@naukri.com",
    "info@naukri.com",
    "infonc@naukri.com",
    "recommendationnc@naukri.com",
    "contentnc@naukri.com",
    "info@minis.naukri.com",
    "noreply@glassdoor.com",
    "jobalerts@glassdoor.com",
    "alerts@glassdoor.com",
    "noreply@unstop.news",
    "no-reply@propeers.in",
    "noreply@instahyre.com",
    "notify@cutshort.io",
    "info@hiddenjobs.dev",
    "notifications@github.com",
    "noreply@github.com",
    "support@gitguardian.com",
    "support@jobscan.co",
    "mailer@angelbroking.com",
    "noreply@digest.groww.in",
    "springboard@infosys.com",
    "english-quora-digest@quora.com",
    "googledev-noreply@google.com",
    "no-reply@discuss.google.com",
    "noreply-accounts@google.com",
    "android-developers-noreply@google.com",
    "developer@groq.co",
    "learn@itr.mail.codecademy.com",
    "hi@16personalities.com",
    "noreply@zenfy.ac",
    "support@match.jobgether.com",
    "noreply@email.seek.com.au",
    "aadhaar@uidai.gov.in",
    "proteantech.in",
    "idfcfirstbank.com",
    "in.mpms.mufg.com",
    "bigshareonline.com",
    "cameoindia.com",
    "3scale.redhat.com",
    "updates.coursiv.co",
    "nextleap.app",
    "airtm.com",
    "careers@recruitment.americanexpress.com",
    "talent@careers.mastercard.com",
    "opportunities@telusinternational.ai",
    "handshake@g.joinhandshake.com",
)

_NOISE_SUBJECTS = (
    "digest",
    "top jobs",
    "openings for you",
    "job alert",
    "new jobs:",
    "new jobs similar to",
    "is hiring for",
    "hiring now",
    "picked for you",
    "great fit for these",
    "handpicked new jobs",
    "top openings for",
    "urgent requirement for",
    "immediate requirement for",
    "urgently hiring for",
    "top companies are hiring",
    "apply now to",
    "your latest oracle jobs",
    "new job opportunities at",
    "stay safe from recruitment scams",
    "codevita",
    "imagination challenge",
    "your feedback matters",
    "login email id verification",
    "verify your candidate account",
    "verify your email",
    "confirm your identity",
    "you've withdrawn your",
    "withdrawn your amazon job application",
    "finish your interview",
    "upload your resume to be eligible",
    "open for application -",
    "[reminder] open for application",
    "application submitted:",
    "application submitted -",
    "application submitted by college",
    "successfully submitted",
    "we have received your application",
    "we've received your job application",
    "we've received your application",
    "we received your application",
    "stay up-to-date on future career opportunities",
    "follow-up on your application to junior data engineer",
    "sitting arrangement",
    "pre-placement talk",
    "ppt scheduled",
)

_REJECTED_SUBJECT_PATTERNS = [
    r"\bapplication\s+not\s+selected\b",
    r"\bposition\s+closed\b",
    r"\bjob\s+application\s+update:.*\(filled\)",
]

_REJECTED_PATTERNS = [
    r"decided\s+not\s+to\s+move\s+forward",
    r"won't\s+be\s+moving\s+forward\s+with\s+(?:your\s+application|it\b)",
    r"will\s+not\s+be\s+moving\s+forward\s+with\s+(?:your\s+application|it\b)",
    r"not\s+to\s+move\s+forward\s+with\s+your\s+(?:application|candidacy|candidature)",
    r"unable\s+to\s+move\s+forward\s+with\s+your",
    r"not\s+moving\s+forward\s+with\s+your",
    r"decided\s+to\s+move\s+forward\s+with\s+(?:other|another)\s+(?:candidates?|applicants?)",
    r"moving\s+forward\s+with\s+(?:other\s+)?candidates\s+whose",
    r"decided\s+to\s+progress\s+with\s+other\s+(?:candidates?|applicants?)",
    r"decided\s+not\s+to\s+progress\s+with\s+your\s+application",
    r"won't\s+be\s+progressing\s+with\s+your\s+application",
    r"not\s+be\s+progressing\s+with\s+your\s+application",
    r"decided\s+to\s+proceed\s+with\s+other\s+(?:candidates?|applicants?)",
    r"unable\s+to\s+proceed\s+with\s+your\s+(?:candidature|candidacy|application)",
    r"not\s+(?:be\s+)?proceeding\s+with\s+your\s+(?:application|candidacy|candidature)",
    r"decided\s+not\s+to\s+proceed",
    r"decided\s+to\s+pursue\s+other\s+candidates?",
    r"pursue\s+other\s+candidates?\s+who",
    r"will\s+no\s+longer\s+be\s+pursuing\s+your\s+candidacy",
    r"no\s+longer\s+under\s+consideration",
    r"not\s+been\s+selected\s+for\s+further\s+consideration",
    r"not\s+selected\s+for\s+further\s+consideration",
    r"did\s+not\s+select\s+you\s+for\s+this\s+role",
    r"application\s+was\s+not\s+selected",
    r"\bapplication\s+not\s+selected\b",
    r"regret\s+to\s+inform\s+you\s+that\s+(?:you\s+are\s+not|we\s+have\s+decided|it\s+has\s+not|we\s+are\s+unable|we\s+cannot|we\s+will\s+not)",
    r"not\s+part\s+of\s+final\s+shortlist",
    r"not\s+shortlisted\s+for\s+next\s+stage",
    r"not\s+been\s+shortlisted",
    r"not\s+eligible\s+for\s+this\s+role",
    r"haven't\s+met\s+the\s+requirements\s+for\s+this\s+position",
    r"did\s+not\s+align\s+with\s+our\s+certification\s+criteria",
    r"unfortunately,?\s+it\s+is\s+not\s+a\s+match\s+for\s+this\s+particular\s+role",
    r"unfortunately\s+are\s+going\s+in\s+a\s+direction\s+that\s+better\s+fits\s+our\s+needs",
    r"identified\s+other\s+more\s+qualified\s+candidates",
    r"unable\s+to\s+offer\s+you\s+a\s+(?:position|role)",
]

_INTERVIEW_PATTERNS = [
    r"\bschedule\s+a\s+call\b",
    r"\btechnical\s+interview\b",
    r"\bvideo\s+interview\b",
    r"\bon-site\s+interview\b",
    r"\bscreening\s+call\b",
    r"\bshortlisted\s+for\s+(?:stage|interview|technical|hr)\b",
    r"\binvite\s+you\s+to\s+an?\s+interview\b",
]

_OA_INVITE_PATTERNS = [
    r"\bonline\s+assessment\b",
    r"\bcoding\s+challenge\b",
    r"\btake[-‑]home\s+assignment\b",
    r"\btechnical\s+exercise\b",
    r"\bassessment\s+link\s+for\b",
]

_ACKNOWLEDGED_PATTERNS = [
    r"received\s+your\s+(?:job\s+)?application",
    r"application\s+received",
    r"we\s+have\s+your\s+resume",
    r"thanks\s+for\s+applying",
    r"thank\s+you\s+for\s+applying",
    r"thank\s+you\s+for\s+your\s+(?:online\s+submission|job\s+application|application)",
]


def _clean_text(text: str) -> str:
    if not text:
        return ""
    t = html.unescape(text)
    t = re.sub(r"[\u200b\u200c\u200d\ufeff\u00a0]", " ", t)
    t = re.sub(r"[\u2010\u2011\u2012\u2013\u2014\u2015]", "-", t)
    t = re.sub(r"[\u2018\u2019]", "'", t)
    t = re.sub(r"[\u201c\u201d]", '"', t)
    return " ".join(t.split())


def extract_clean_email_body(msg: EmailMessage) -> str:
    """Extract clean readable text from an EmailMessage (both plain text and HTML parts), stripping CSS/scripts."""
    plain_parts: list[str] = []
    html_parts: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition", ""))
            if "attachment" in disp:
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                decoded = payload.decode(charset, errors="replace")
            except Exception:
                decoded = payload.decode("utf-8", errors="replace")
            if ctype == "text/plain":
                plain_parts.append(decoded)
            elif ctype == "text/html":
                html_parts.append(decoded)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                decoded = payload.decode(charset, errors="replace")
            except Exception:
                decoded = payload.decode("utf-8", errors="replace")
            if msg.get_content_type() == "text/html":
                html_parts.append(decoded)
            else:
                plain_parts.append(decoded)

    combined_plain = _clean_text(" ".join(plain_parts))
    # Many HTML emails have empty or stub plain text; always parse HTML if plain text is short
    if len(combined_plain) >= 80 and not combined_plain.startswith("<!DOCTYPE") and "<html" not in combined_plain.lower():
        return combined_plain

    raw_html = "\n".join(html_parts) if html_parts else " ".join(plain_parts)
    if not raw_html:
        return combined_plain

    # Strip <style>, <script>, <head>, and HTML comments before tag removal
    cleaned_html = re.sub(r"(?is)<(?:style|script|head)[^>]*>.*?</(?:style|script|head)>", " ", raw_html)
    cleaned_html = re.sub(r"(?is)<!--.*?-->", " ", cleaned_html)
    cleaned_html = re.sub(r"<[^>]+>", " ", cleaned_html)
    return _clean_text(cleaned_html)


def _match_patterns(text: str, patterns: List[str]) -> Optional[str]:
    """Return first matching snippet."""
    if not text:
        return None
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            start, end = m.start(), m.end()
            return text[max(0, start - 50) : min(len(text), end + 150)].strip()
    return None


def is_noise_email(subject: str, sender: str) -> bool:
    """Return True if the email is from a job alert newsletter, marketing blast, or non-application notification."""
    sub_l = (subject or "").lower().strip()
    snd_l = (sender or "").lower().strip()

    if any(ns in snd_l for ns in _NOISE_SENDER_SUBSTRINGS):
        return True
    if any(noise in sub_l for noise in _NOISE_SUBJECTS):
        return True
    if "propeers.in" in snd_l or "unstop.news" in snd_l or "acciojob" in snd_l:
        return True
    return False


def classify_email_content(subject: str, sender: str, body: str) -> Tuple[RevertType, str]:
    """Classify email content into RevertType and return a representative snippet."""
    if is_noise_email(subject, sender):
        return RevertType.UNKNOWN, ""

    # 1. Check REJECTED first so rejection emails mentioning "interview" or "thank you for applying"
    # are never misclassified as INTERVIEW or ACKNOWLEDGED.
    subj_rej = _match_patterns(subject, _REJECTED_SUBJECT_PATTERNS)
    body_rej = _match_patterns(body, _REJECTED_PATTERNS) or _match_patterns(subject, _REJECTED_PATTERNS)
    if body_rej or subj_rej:
        return RevertType.REJECTED, (body_rej or subj_rej or "")

    # 2. Check OA_INVITE and INTERVIEW
    for patterns, rtype in [
        (_OA_INVITE_PATTERNS, RevertType.OA_INVITE),
        (_INTERVIEW_PATTERNS, RevertType.INTERVIEW),
        (_ACKNOWLEDGED_PATTERNS, RevertType.ACKNOWLEDGED),
    ]:
        snippet = _match_patterns(body, patterns) or _match_patterns(subject, patterns)
        if snippet:
            return rtype, snippet

    return RevertType.UNKNOWN, ""


_GENERIC_ATS_DOMAINS = {
    "ashbyhq",
    "greenhouse",
    "greenhouse-mail",
    "lever",
    "myworkday",
    "myworkdayjobs",
    "workday",
    "smartrecruiters",
    "joinsuperset",
    "kekamail",
    "collage",
    "icims",
    "taleo",
    "bamboohr",
    "jobvite",
    "recruitee",
    "breezy",
    "jazzhr",
    "workable",
    "oracle",
    "workflow",
}


def _extract_company_from_sender(sender: str) -> str:
    try:
        m = re.search(r"([a-zA-Z0-9._+-]+)@([\w.-]+)", sender)
        local_part = m.group(1).lower() if m else ""
        domain = m.group(2).lower() if m else sender.split("@")[-1].lower().strip("> ")
        for pref in ("mail.", "noreply.", "no-reply.", "notification.", "notifications.", "hire.eu.", "hire.", "us.", "eu.", "email.careers.", "careers.", "recruiting.", "my."):
            if domain.startswith(pref):
                domain = domain[len(pref) :]
        base = domain.split(".")[0]
        if base in _GENERIC_ATS_DOMAINS:
            if base in ("myworkday", "myworkdayjobs", "workday") and local_part and local_part not in ("noreply", "no-reply", "donotreply", "do-not-reply", "workday", "hr", "careers"):
                return local_part
            return ""
        return base
    except Exception:
        return ""


def extract_company_and_role(subject: str, sender: str, body: str) -> Tuple[str, str]:
    """Extract clean human-readable (company, role) from subject, sender display name/domain, and body."""
    subj = _clean_text(subject)
    snd = _clean_text(sender)
    b_head = _clean_text(body[:600])

    company = ""
    role = ""

    # 1. Try explicit subject patterns
    # Pattern A0: "Update on/about your application for <Role>" (Mercor / micro1)
    m = re.match(r"^Update\s+(?:on|about)\s+your\s+application\s+for\s+(.+)$", subj, re.I)
    if m:
        role = m.group(1).strip()

    # Pattern A0b: "Follow up on your application to <Company>"
    if not company:
        m = re.match(r"^Follow[- ]up\s+on\s+your\s+application\s+to\s+(.+)$", subj, re.I)
        if m:
            company = m.group(1).strip()

    # Pattern A: "Thank you for participating in <Company>'s process for <Role>" (Superset)
    if not company:
        m = re.search(r"participating\s+in\s+(.+?)(?:'s|s')\s+process\s+for\s+(.+)$", subj, re.I)
        if m:
            company, role = m.group(1).strip(), m.group(2).strip()

    # Pattern B: "<Company> | Update regarding your application for the <Role> role"
    if not company:
        m = re.match(r"^([A-Za-z0-9 .&-]+?)\s*\|\s*Update\s+(?:regarding|on)\s+your\s+(?:job\s+)?application(?:\s+for\s+(?:the\s+)?(.+?)(?:\s+role)?)?$", subj, re.I)
        if m:
            company = m.group(1).strip()
            if m.group(2):
                role = m.group(2).strip()

    # Pattern C: "Thank you for your interest in <Company> || <Role>"
    if not company:
        m = re.match(r"^Thank\s+you\s+for\s+your\s+interest\s+in\s+(.+?)\s*\|\|\s*(.+)$", subj, re.I)
        if m:
            company, role = m.group(1).strip(), m.group(2).strip()

    # Pattern C2: "Your application to <Role> at <Company>" (Ashby / Greenhouse)
    if not company:
        m = re.match(r"^Your\s+application\s+(?:to|for)\s+(.+?)\s+at\s+([A-Za-z0-9 .&-]+)$", subj, re.I)
        if m:
            role, company = m.group(1).strip(), m.group(2).strip()

    # Pattern D: "Your Application to <Company> | <Role>" or "Your Application to <Company> - <Role>"
    if not company:
        m = re.match(r"^Your\s+Application\s+to\s+([A-Za-z0-9 .&-]+?)\s*(?:\||-)\s*(.+)$", subj, re.I)
        if m:
            company, role = m.group(1).strip(), m.group(2).strip()

    # Pattern E: "Update on your <Company> application: <Role>" or "Update on <Company>'s <Role>"
    if not company and not role:
        m = re.match(r"^Update\s+on\s+(?:your\s+)?([A-Za-z0-9 .&-]+?)(?:'s)?\s+(?:application:\s*)?(.+)$", subj, re.I)
        if m and m.group(1).lower() not in ("your", "application", "your application", "your job"):
            company, role = m.group(1).strip(), m.group(2).strip()

    # Pattern F: "An update on your application with <Company> - <Role>"
    if not company:
        m = re.match(r"^An\s+update\s+on\s+your\s+application\s+with\s+(.+?)\s*-\s*(.+)$", subj, re.I)
        if m:
            company, role = m.group(1).strip(), m.group(2).strip()

    # Pattern G: "<Company> - Feedback on your <Role> application"
    if not company:
        m = re.match(r"^([A-Za-z0-9 .&-]+?)\s*-\s*Feedback\s+on\s+your\s+(.+?)\s+application$", subj, re.I)
        if m:
            company, role = m.group(1).strip(), m.group(2).strip()

    # Pattern H: "Your application for our <Role> role at <Company>"
    if not company:
        m = re.match(r"^Your\s+application\s+for\s+(?:our\s+)?(.+?)\s+role\s+at\s+(.+)$", subj, re.I)
        if m:
            role, company = m.group(1).strip(), m.group(2).strip()

    # Pattern I: "<Company> Job Application Update: <Role>" or "<Company> - Application Update"
    if not company:
        m = re.match(r"^([A-Za-z0-9 .&-]+?)\s+(?:Job\s+Application\s+Update:|-\s*Application\s+Update|application:\s*Status\s+update)(.*)$", subj, re.I)
        if m:
            company = m.group(1).strip()
            if m.group(2):
                role = m.group(2).strip(" -:")

    # Pattern J: "<Company> position closed <Role>"
    if not company:
        m = re.match(r"^([A-Za-z0-9 .&-]+?)\s+position\s+closed\s+(.+)$", subj, re.I)
        if m:
            company, role = m.group(1).strip(), m.group(2).strip()

    # Pattern K: "Chinmay Maheshwari & <Company>-Regarding your application"
    if not company:
        m = re.search(r"&\s*([A-Za-z0-9 .&-]+?)\s*-\s*Regarding\s+your\s+application", subj, re.I)
        if m:
            company = m.group(1).strip()

    # Pattern L: "Your Application with <Company>" or "Your application to <Company>" or "Thanks for your interest in <Company>"
    if not company:
        m = re.match(r"^(?:Your\s+Application\s+(?:with|to)|Thanks\s+for\s+your\s+interest\s+in|Thank\s+you\s+for\s+your\s+interest\s+in)\s+([A-Za-z0-9 .&-]+?)[!.]?$", subj, re.I)
        if m:
            company = m.group(1).strip()

    # 2. Extract company from sender display name if still missing
    if not company:
        disp_m = re.match(r'^"?([^"<]+?)"?\s*<', snd)
        if disp_m:
            disp = disp_m.group(1).strip()
            disp = re.sub(
                r"(?i)\s+(?:Hiring\s+Team|Talent\s+Acquisition\s+Team|Talent\s+Acquisition|Talent\s+Team|Talent|Recruiting|Recruitment|Careers|People\s+Team|People\s+Services|Workday\s+Notifications|HR|HCM)(?:\s*-\s*DO\s+NOT\s+REPLY!?)?$",
                "",
                disp,
            ).strip()
            if disp and disp.lower() not in ("hiring team", "no-reply", "noreply", "do-no-reply", "donotreply", "superset"):
                company = disp

    # 3. Extract company from body if sender was generic (e.g. "Hiring Team <no-reply@ashbyhq.com>" or Workday)
    if not company:
        m_interest_at = re.search(
            r"interest\s+in\s+(?:the\s+|our\s+)?([A-Za-z0-9 (),/._-]+?)\s+at\s+([A-Z][A-Za-z0-9 .&-]{1,30}?)(?:[.,!]|and\s+for)",
            b_head,
        )
        if m_interest_at:
            if not role:
                role = m_interest_at.group(1).strip(" .,-")
            company = m_interest_at.group(2).strip()
        else:
            m_body_co = re.search(
                r"(?:Thanks\s+for\s+considering|interest\s+in\s+(?:joining\s+|purs[u]ing\s+a\s+career\s+at\s+)?|applying\s+to\s+(?:the\s+.+?\s+role\s+at\s+)?)\s*([A-Z][A-Za-z0-9 .&-]{1,30}?)(?:[.,!]|and\s+for|\s+as\s+the)",
                b_head,
            )
            if m_body_co:
                cand = m_body_co.group(1).strip()
                if (
                    cand.lower() not in ("our", "the", "a", "this", "joining")
                    and not re.search(r"\b(engineer|developer|intern|analyst|R-\d+|JR-\d+)\b", cand, re.I)
                ):
                    company = cand

    # 4. Fallback to sender domain
    if not company:
        dom = _extract_company_from_sender(snd)
        domain_map = {
            "oneforma": "OneForma",
            "vanguardhr": "Vanguard",
            "wellsfargo": "Wells Fargo",
            "wellsfargoworkday": "Wells Fargo",
            "hackerrank": "HackerRank",
            "optiver": "Optiver",
            "groww": "Groww",
            "stripe": "Stripe",
            "barclays": "Barclays",
            "amazon": "Amazon",
            "microsoft": "Microsoft",
            "uber": "Uber",
            "mercor": "Mercor",
            "micro1": "micro1",
            "bendingspoons": "Bending Spoons",
            "ea": "Electronic Arts",
        }
        company = domain_map.get(dom.lower(), dom.title() if dom else "Unknown Company")

    # Normalize company name aliases
    comp_aliases = {
        "barclayshcm": "Barclays",
        "wellsfargohr": "Wells Fargo",
        "prima": "Prima",
        "nike": "NIKE",
    }
    if company.lower() in comp_aliases:
        company = comp_aliases[company.lower()]

    # 5. Extract role from subject or body if still empty
    if not role:
        m_subj_role = re.search(
            r"(?:application\s+for(?:\s+the\s+position\s+of)?|update\s+about\s+your\s+application\s+for|application\s+update:\s*)\s*(.+)$",
            subj,
            re.I,
        )
        if m_subj_role:
            role = m_subj_role.group(1).strip()

    if not role:
        body_role_patterns = [
            r"following\s+role:\s*([A-Za-z0-9 (),/._-]+?)(?:\s+While\s+we|\s+We\s+)",
            r"applying\s+(?:to|for)\s+(?:the\s+|our\s+)?(?:position\s+of\s+|role\s+in\s+the\s+)?([A-Za-z0-9 (),|/._-]+?)(?:\s+role|\s+position|\s+in\s+the\s+|\s+through\s+|\s+at\s+|\.)",
            r"application\s+for\s+(?:the\s+)?(?:position\s+of\s+)?([A-Za-z0-9 (),|/._-]+?)(?:\s+role|\s+position|\.)",
            r"interest\s+in\s+(?:our\s+|the\s+)?(?:job\s+as\s+)?([A-Za-z0-9 (),/._-]+?)(?:\s+role|\s+position|\s+at\s+|\.)",
            r"completing\s+your\s+([A-Za-z0-9 (),/._-]+?)\s+AI\s+interview",
        ]
        for pat in body_role_patterns:
            m_br = re.search(pat, b_head, re.I)
            if m_br:
                cand_r = m_br.group(1).strip(" .,-")
                cand_r = re.sub(r"(?i)^the\s+position\s+of\s+", "", cand_r).strip()
                if (
                    2 < len(cand_r) < 90
                    and cand_r.lower() not in ("the", "working", "our", "your", "this", "joining", "employment", "application")
                    and not cand_r.lower().startswith(("joining", "employment", "a career", "this job", "working at", "working with", "the "))
                    and "encourage you" not in cand_r.lower()
                ):
                    role = cand_r
                    break

    role = re.sub(r"(?i)^the\s+position\s+of\s+", "", role).strip()
    if role.lower() in ("the", "working", "our", "your", "this", "joining", "employment"):
        role = ""
    return company, role


def correlate_revert_to_job(
    conn: Optional[sqlite3.Connection],
    company_hint: str,
    sender: str,
    applied_jobs: Optional[list[dict[str, Any]]] = None,
    db_path: Optional[Path] = None,
) -> Optional[int]:
    applied = applied_jobs if applied_jobs is not None else get_applied_jobs(db_path=db_path)
    hint = company_hint.strip().lower()
    best_id = None
    best_score = 0.0
    for job in applied:
        score = difflib.SequenceMatcher(None, job["company"].lower(), hint).ratio()
        if score > best_score:
            best_score = score
            best_id = job["numeric_id"]
    if best_score >= 0.7:
        return best_id
    domain = _extract_company_from_sender(sender).lower()
    if not domain:
        return None
    best_id = None
    best_score = 0.0
    for job in applied:
        score = difflib.SequenceMatcher(None, job["company"].lower(), domain).ratio()
        if score > best_score:
            best_score = score
            best_id = job["numeric_id"]
    return best_id if best_score >= 0.7 else None


def process_email_record(
    uid: str,
    subject: str,
    sender: str,
    date: str,
    body: str,
    db_path: Optional[Path] = None,
    account: str = "",
    applied_jobs: Optional[list[dict[str, Any]]] = None,
) -> Optional[RevertMatch]:
    rtype, snippet = classify_email_content(subject, sender, body)
    if rtype is RevertType.UNKNOWN:
        return None
    company, role = extract_company_and_role(subject, sender, body)
    hint = company if company and company != "Unknown Company" else (subject or sender)
    match_id = correlate_revert_to_job(None, hint, sender, applied_jobs=applied_jobs, db_path=db_path)
    return RevertMatch(
        company=hint,
        revert_type=rtype,
        subject=subject,
        sender=sender,
        date=date,
        snippet=snippet,
        matched_job_id=match_id,
        role=role,
        account=account,
    )


def _is_candidate_status_header(subject: str, sender: str) -> bool:
    """Fast header-level pre-filter so we only fetch full RFC822 bodies for likely status/application emails."""
    if is_noise_email(subject, sender):
        return False
    sl = (subject or "").lower()
    fl = (sender or "").lower()
    if "jecrc-it-" in fl or "tpo it jecrc" in fl or "unstop" in fl:
        return False
    keywords = (
        "application",
        "applied",
        "applying",
        "update",
        "status",
        "candidacy",
        "candidature",
        "feedback on your",
        "thank you for your interest",
        "thanks for your interest",
        "thank you for your time",
        "thank you for participating",
        "position closed",
        "not selected",
        "shortlist",
        "selected for",
        "interview",
        "assessment",
        "regarding your",
        "hiring team",
        "talent acquisition",
        "recruiting",
        "recruitment",
        "workday",
        "greenhouse",
        "ashbyhq",
        "lever.co",
        "joinsuperset",
        "vanguardhr",
        "hackerrank",
        "optiver",
        "groww",
        "stripe",
        "barclays",
        "bendingspoons",
        "mercor",
        "micro1",
        "oneforma",
    )
    return any(k in sl or k in fl for k in keywords)


def _scan_single_mailbox(
    acc_user: str,
    acc_pass: str,
    since_str: str,
    db_path: Optional[Path] = None,
    applied_jobs: Optional[list[dict[str, Any]]] = None,
) -> list[RevertMatch]:
    """Scan a single IMAP mailbox for application status reverts since `since_str`."""
    matches: list[RevertMatch] = []
    imap = imaplib.IMAP4_SSL("imap.gmail.com", timeout=20.0)
    try:
        imap.login(acc_user, acc_pass)
        st, _ = imap.select("INBOX", readonly=True)
        if st != "OK":
            return []

        st_s, data = imap.uid("SEARCH", None, f"SINCE {since_str}")
        if st_s != "OK" or not data or not data[0]:
            return []

        all_uids = [u.decode() if isinstance(u, bytes) else str(u) for u in data[0].split()]
        if not all_uids:
            return []

        # Pass 1: Fast batch header fetch in chunks of 250
        candidate_uids: list[str] = []
        chunk_size = 250
        for i in range(0, len(all_uids), chunk_size):
            chunk = all_uids[i : i + chunk_size]
            st_h, h_data = imap.uid(
                "FETCH",
                ",".join(chunk),
                "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])",
            )
            if st_h != "OK" or not h_data:
                continue
            for item in h_data:
                if not isinstance(item, tuple) or len(item) < 2:
                    continue
                meta = item[0].decode("latin1", errors="ignore") if isinstance(item[0], (bytes, bytearray)) else str(item[0])
                m_u = re.search(r"\bUID\s+(\d+)\b", meta, re.I)
                if not m_u:
                    continue
                uid_val = m_u.group(1)
                msg = email.message_from_bytes(bytes(item[1]), policy=policy.default)
                subj = _clean_text(str(msg.get("Subject", "")))
                frm = _clean_text(str(msg.get("From", "")))
                if _is_candidate_status_header(subj, frm):
                    candidate_uids.append(uid_val)

        if not candidate_uids:
            return []

        # Pass 2: Batch fetch full bodies only for candidate status emails
        body_chunk_size = 30
        for i in range(0, len(candidate_uids), body_chunk_size):
            b_chunk = candidate_uids[i : i + body_chunk_size]
            st_b, b_data = imap.uid("FETCH", ",".join(b_chunk), "(BODY.PEEK[])")
            if st_b != "OK" or not b_data:
                continue
            for item in b_data:
                if not isinstance(item, tuple) or len(item) < 2:
                    continue
                if not isinstance(item[1], (bytes, bytearray)):
                    continue
                meta = item[0].decode("latin1", errors="ignore") if isinstance(item[0], (bytes, bytearray)) else str(item[0])
                m_u = re.search(r"\bUID\s+(\d+)\b", meta, re.I)
                uid_val = m_u.group(1) if m_u else "?"
                msg = email.message_from_bytes(bytes(item[1]), policy=policy.default)
                subj = _clean_text(str(msg.get("Subject", "")))
                frm = _clean_text(str(msg.get("From", "")))
                dt_str = _clean_text(str(msg.get("Date", "")))
                body_text = extract_clean_email_body(msg)

                match = process_email_record(
                    uid=f"{acc_user}:{uid_val}",
                    subject=subj,
                    sender=frm,
                    date=dt_str,
                    body=body_text,
                    db_path=db_path,
                    account=acc_user,
                    applied_jobs=applied_jobs,
                )
                if match:
                    matches.append(match)
    except Exception as exc:
        logger.warning("Failed scanning mailbox %s for reverts: %s", acc_user, exc)
    finally:
        try:
            imap.close()
        except Exception:
            pass
        try:
            imap.logout()
        except Exception:
            pass

    return matches


def _parse_sort_date(date_str: str) -> float:
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.timestamp()
    except Exception:
        return 0.0


def _format_short_date(date_str: str) -> str:
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.strftime("%d %b %Y")
    except Exception:
        return date_str[:16] if date_str else "Recent"


def _format_iso_date(date_str: str) -> str:
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        if re.match(r"^\d{4}-\d{2}-\d{2}", str(date_str or "")):
            return str(date_str)[:10]
        return ""


def scan_email_reverts(
    days: int = 90,
    update_db: bool = True,
    db_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Scan all configured IMAP email accounts concurrently for rejections, interviews, and OA invites."""
    from tools.ingest_email import format_imap_date, get_configured_email_accounts

    init_db(db_path)
    applied_jobs = get_applied_jobs(db_path=db_path)
    accounts = get_configured_email_accounts()
    since_dt = datetime.now(timezone.utc) - timedelta(days=max(1, days))
    since_str = format_imap_date(since_dt)

    all_matches: list[RevertMatch] = []
    with ThreadPoolExecutor(max_workers=max(1, len(accounts))) as pool:
        futures = {
            pool.submit(_scan_single_mailbox, acc_user, acc_pass, since_str, db_path, applied_jobs): acc_user
            for acc_user, acc_pass in accounts
        }
        for fut in as_completed(futures):
            try:
                all_matches.extend(fut.result())
            except Exception as exc:
                logger.warning("Mailbox worker failed: %s", exc)

    # Sort newest first
    all_matches.sort(key=lambda m: _parse_sort_date(m.date), reverse=True)

    # Deduplicate identical (company, role/subject, revert_type, short_date) entries
    deduped: list[RevertMatch] = []
    seen_keys: set[tuple[str, str, str, str]] = set()
    db_updates: list[int] = []
    for m in all_matches:
        short_d = _format_short_date(m.date)
        role_or_subj = (m.role or m.subject).lower().strip()
        key = (m.company.lower().strip(), role_or_subj, m.revert_type.value, short_d)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(m)

        if update_db and m.matched_job_id:
            if m.revert_type == RevertType.REJECTED:
                mark_job_status(m.matched_job_id, "REJECTED", notes=f"Rejection email received ({short_d})", db_path=db_path)
                db_updates.append(m.matched_job_id)
            elif m.revert_type in (RevertType.INTERVIEW, RevertType.OA_INVITE):
                mark_job_status(m.matched_job_id, "INTERVIEWING", notes=f"{m.revert_type.value} email received ({short_d})", db_path=db_path)
                db_updates.append(m.matched_job_id)

    rejections = [
        {
            "status": "REJECTED",
            "company": m.company,
            "role": m.role or m.subject,
            "subject": m.subject,
            "date": _format_short_date(m.date),
            "iso_date": _format_iso_date(m.date),
            "account": m.account,
            "snippet": m.snippet[:160],
            "matched_job_id": m.matched_job_id,
        }
        for m in deduped
        if m.revert_type == RevertType.REJECTED
    ]

    interviews = [
        {
            "status": m.revert_type.value,
            "company": m.company,
            "role": m.role or m.subject,
            "subject": m.subject,
            "date": _format_short_date(m.date),
            "iso_date": _format_iso_date(m.date),
            "account": m.account,
            "type": m.revert_type.value,
        }
        for m in deduped
        if m.revert_type in (RevertType.INTERVIEW, RevertType.OA_INVITE)
    ]

    acknowledged = [
        {
            "status": "ACKNOWLEDGED",
            "company": m.company,
            "role": m.role or m.subject,
            "subject": m.subject,
            "date": _format_short_date(m.date),
            "iso_date": _format_iso_date(m.date),
            "account": m.account,
        }
        for m in deduped
        if m.revert_type == RevertType.ACKNOWLEDGED
    ]

    unique_rejected_companies = sorted({r["company"] for r in rejections}, key=lambda x: x.lower())

    return {
        "status": "success",
        "days_scanned": days,
        "accounts_checked": [u for u, _ in accounts],
        "total_rejections": len(rejections),
        "unique_rejected_companies_count": len(unique_rejected_companies),
        "unique_rejected_companies": unique_rejected_companies,
        "rejections": rejections,
        "interviews_and_oas_count": len(interviews),
        "interviews_and_oas": interviews,
        "reverts": rejections + interviews + acknowledged,
        "db_updates": db_updates,
    }
