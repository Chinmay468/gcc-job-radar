from datetime import date, datetime, timezone
import html
import re
from typing import Any, Optional, Union
from gcc_job_radar.config import (
    EXCLUDE_TITLE_PATTERN,
    INCLUDE_TITLE_PATTERN,
    LOCATION_PATTERN,
)

# Pattern to mask 'member of technical staff' before checking exclusions so 'staff' is not falsely triggered
_MTS_MASK_PATTERN = re.compile(r"(?i)\bmember\s+of\s+technical\s+staff\b")

# ---------------------------------------------------------------------------
# Tech-role gate: used by the harvester to reject non-software ATS boards
# ---------------------------------------------------------------------------

_TECH_ROLE_PATTERN = re.compile(
    r"""(?ix)
    \b(
        software | sde | swe | developer | coder |
        full[\s\-]?stack | backend | back[\s\-]?end |
        front[\s\-]?end | frontend |
        data\s+(?:engineer|scientist|analyst|science) |
        machine\s+learning | ml\s+engineer | ai\s+engineer |
        deep\s+learning | nlp\s+engineer | computer\s+vision |
        cloud\s+(?:engineer|architect|developer) |
        devops | devsecops | mlops |
        site\s+reliability | sre |
        platform\s+engineer(?:ing)? |
        infrastructure\s+engineer(?:ing)? |
        it\s+engineer(?:ing)? |
        systems?\s+engineer(?!\s+(?:mech|civil|struct|elec)) |
        engineer\s+(?:trainee|intern) |
        qa\s+(?:engineer|automation) | sdet | test\s+automation |
        security\s+engineer | cybersecurity |
        firmware\s+engineer | embedded\s+software |
        network\s+(?:software|automation)\s+engineer
    )\b
    """,
)

_NON_TECH_DISCIPLINE_PATTERN = re.compile(
    r"""(?ix)
    \b(
        mechanical | civil | structural | electrical |
        autocad | \bcad\b | \bnx\b | teamcenter |
        piping | hvac | instrumentation |
        recruiter | talent(?:\s+acquisition)? | back\s+office |
        field\s+executive | customer\s+support | telecaller |
        operations(?:\s+executive)? | sales | marketing | hr |
        nursing | bdr | sdr |
        (?:field|sales)\s+engineer(?:ing)?
    )\b
    """,
)

_GET_PATTERN = re.compile(
    r"""(?ix)
    \b(?:graduate\s+engineer\s+trainee|\bget\b|engineering\s+trainee)\b
    """,
)

_TECH_DISCIPLINE_PATTERN = re.compile(
    r"""(?ix)
    \b(
        software | sde | swe | develop(?:er|ment|ing)? | coding | programming |
        it | information\s+technology | computer\s+science | cs |
        data | cloud | ai | ml | machine\s+learning |
        backend | frontend | full[\s\-]?stack | devops | sre |
        qa | test | automation | systems? | network |
        python | java | react | node | sql | web | app
    )\b
    """,
)


def is_valid_get_role(title: str, description: str = "") -> bool:
    """Validate that a Graduate Engineer Trainee (GET) role is strictly for software/tech.

    If title contains 'GET' or 'Graduate Engineer Trainee', it requires an explicit
    tech discipline (Software, IT, Computer Science, Cloud, Data, etc.) in the title
    or description, and strictly discards roles containing non-software disciplines
    (mechanical, civil, structural, electrical, autocad, cad, nx, piping, hvac, instrumentation).
    """
    text = f"{title} {description}".strip()
    if not text:
        return False
    if _NON_TECH_DISCIPLINE_PATTERN.search(text):
        return False
    if _GET_PATTERN.search(title):
        return bool(_TECH_DISCIPLINE_PATTERN.search(text))
    return True


def is_tech_role(title: str, department: str = "") -> bool:
    """Return True only if the title/department belongs to a software/data/AI/ML/cloud/DevOps discipline.

    Explicitly rejects non-software engineering disciplines (mechanical, civil, HVAC, etc.)
    regardless of whether the word 'engineer' appears in the title.
    """
    text = f"{title} {department}".strip()
    if not text:
        return False
    if _NON_TECH_DISCIPLINE_PATTERN.search(text):
        return False
    if _GET_PATTERN.search(title):
        return bool(_TECH_DISCIPLINE_PATTERN.search(text))
    return bool(_TECH_ROLE_PATTERN.search(text))

# Disqualify roles requiring 3+ or more years of experience or experienced mid-level ranges (e.g. 2-4+ yrs)
EXPERIENCE_DISQUALIFY_PATTERN: re.Pattern[str] = re.compile(
    r"""
    (?ix)
    \b(?:
        2\s*(?:-|–|—|to)\s*(?:[4-9]|\d{2,})\+?\s*(?:years?|yrs?)(?:\s+of)?(?:\s+(?:relevant|hands[- ]on|work|professional|industry|technical|software|engineering|coding|\w+)){0,3}\s+experience |
        (?:[3-9]|\d{2,})\+?\s*(?:-\s*\d+\s*)?(?:years?|yrs?)(?:\s+of)?(?:\s+(?:relevant|hands[- ]on|work|professional|industry|technical|software|engineering|coding|\w+)){0,3}\s+experience |
        (?:minimum|at\s+least)\s+(?:of\s+)?(?:[3-9]|\d{2,})\s*(?:years?|yrs?)(?:\s+of)?(?:\s+\w+){0,3}\s+experience |
        experience\s*:\s*(?:[3-9]|\d{2,})\+?\s*(?:years?|yrs?) |
        (?:[3-9]|\d{2,})\s*(?:to|-|–|—)\s*\d+\s*(?:years?|yrs?)(?:\s+of)?(?:\s+\w+){0,3}\s+experience
    )\b
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Explicit exclusion of past graduation batches when role restricts to earlier passouts (2020-2025 only)
PAST_BATCH_EXCLUSION_PATTERN: re.Pattern[str] = re.compile(
    r"""
    (?ix)
    \b(?:
        (?:only\s+for\s+)?(?:202[0-5]|2025|2024|2023)\s*(?:batch|pass[- ]?outs?|graduates?)\s+only |
        (?:202[0-5]|2025|2024|2023)\s*(?:batch|pass[- ]?outs?)\s+(?:only|eligible) |
        batches?\s+(?:eligible\s*:\s*)?(?:202[0-5]|2024|2025)\b(?!\s*[,-/&]\s*2027) |
        (?:must\s+have\s+)?graduated\s+in\s+(?:202[0-5]|2024|2025)\b |
        (?:must\s+have\s+)?completed\s+(?:graduation|degree)\s+(?:in|by)\s+(?:202[0-5]|2024|2025)\b |
        (?:candidates?\s+from\s+)?(?:202[0-5]|2024|2025)\s+(?:batch\s+only|passouts?\s+only)
    )\b
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Positive eligibility pattern for Class of 2027 (Pre-final, Penultimate, Internships, 2027 Batch)
STUDENT_2027_ELIGIBILITY_PATTERN: re.Pattern[str] = re.compile(
    r"""
    (?ix)
    \b(?:
        (?:class|batch|graduating(?:\s+year)?|pass[- ]?out)\s*(?:of|in)?\s*[:=-]?\s*2027\b |
        2027\s*(?:batch|graduates?|pass[- ]?outs?|passouts?|cohort)\b |
        pre[- ]?final\s+year\b |
        penultimate\s+year\b |
        (?:3rd|third)\s+year\s+(?:students?|undergrads?|engineering)\b |
        summer\s+202[67]\s+(?:intern|internship)\b |
        (?:2026|2027)\s+summer\s+intern\b |
        (?:intern|internship|co[- ]?op|apprentice|trainee)\b
    )\b
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Immediate full-time constraints that disqualify currently enrolled 2027 students
IMMEDIATE_FULLTIME_JOIN_PATTERN: re.Pattern[str] = re.compile(
    r"""
    (?ix)
    \b(?:
        immediate\s+joiners?\s+(?:only|required|preferred) |
        (?:must\s+be\s+available\s+to\s+join\s+immediately) |
        (?:degree|graduation)\s+in\s+hand\s+required |
        (?:must\s+already\s+have\s+completed\s+degree) |
        (?:no\s+pursuing\s+students|not\s+for\s+currently\s+enrolled) |
        (?:must\s+have\s+provisional\s+degree\s+certificate)
    )\b
    """,
    re.VERBOSE | re.IGNORECASE,
)

_ENTRY_PREFIX_PATTERN = re.compile(r"(?i)[0-2]\s*(?:-|to|–|—)\s*$")
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")

# Disqualify roles requiring fractional/ranged experience (>= 2.5 yrs) or mid-level ranges
_EXPERIENCE_RANGE_PATTERN: re.Pattern[str] = re.compile(
    r"(?i)\b(\d+(?:\.\d+)?)\s*(?:-|to|–|—)\s*(\d+(?:\.\d+)?)\s*(?:\+?\s*)?(?:years?|yoe|yrs?)\b"
)
_MIN_EXPERIENCE_PATTERN: re.Pattern[str] = re.compile(
    r"(?i)\b(?:min|minimum|at\s+least)\s+(?:of\s+)?(\d+(?:\.\d+)?)\s*(?:\+?\s*)?(?:years?|yoe|yrs?)\b"
)
_PLUS_EXPERIENCE_PATTERN: re.Pattern[str] = re.compile(
    r"(?i)\b(\d+(?:\.\d+)?)\s*\+\s*(?:years?|yoe|yrs?)\b"
)


def requires_experienced_candidate(content: str) -> bool:
    """Check if job description demands experienced candidates (>= 2.5 years experience).

    Returns True if description indicates candidate must have >= 2.5 years experience.
    Genuine entry-level/fresher roles (0-1 yrs, 0-2 yrs, 1-3 yrs, degrees) return False.
    """
    if not content or not content.strip():
        return False
    unescaped = html.unescape(content)
    clean_text = _HTML_TAG_PATTERN.sub(" ", unescaped)

    # 1. Check ranged experience patterns (e.g. "2.6 – 5 Years", "3 - 6 yrs", "2 - 4 years")
    for m in _EXPERIENCE_RANGE_PATTERN.finditer(clean_text):
        try:
            min_y = float(m.group(1))
            max_y = float(m.group(2))
            if min_y >= 2.5:
                return True
            if min_y >= 2.0 and max_y >= 4.0:
                return True
        except (ValueError, TypeError):
            pass

    # 2. Check minimum experience patterns (e.g. "min 3 years", "at least 3 years", "minimum 4 years")
    for m in _MIN_EXPERIENCE_PATTERN.finditer(clean_text):
        try:
            y = float(m.group(1))
            if y >= 2.5:
                return True
        except (ValueError, TypeError):
            pass

    # 3. Check plus patterns (e.g. "3+ YOE", "3+ years", "2.5+ yrs")
    for m in _PLUS_EXPERIENCE_PATTERN.finditer(clean_text):
        try:
            y = float(m.group(1))
            if y >= 2.5:
                start = m.start()
                prefix = clean_text[max(0, start - 10):start]
                if _ENTRY_PREFIX_PATTERN.search(prefix):
                    continue
                return True
        except (ValueError, TypeError):
            pass

    # 4. Check existing comprehensive pattern
    for m in EXPERIENCE_DISQUALIFY_PATTERN.finditer(clean_text):
        start = m.start()
        prefix = clean_text[max(0, start - 10):start]
        # Ignore if part of an entry-level range like 0-3 years or 1-3 years
        if _ENTRY_PREFIX_PATTERN.search(prefix):
            continue
        return True
    return False


def matches_target_title(title: str) -> bool:
    """Check if job title is strictly an entry-level tech position.

    Exclusion rules take strict precedence over inclusion rules.
    """
    if not title or not title.strip():
        return False

    clean_title = title.strip()

    # Mask "Member of Technical Staff" temporarily so "Staff" exclusion rule doesn't falsely flag MTS 1
    sanitized_for_exclusion = _MTS_MASK_PATTERN.sub("mts_role", clean_title)

    # If any exclusion keyword/numeral matches, immediately disqualify
    if EXCLUDE_TITLE_PATTERN.search(sanitized_for_exclusion):
        return False

    # Check if positive entry-level pattern matches
    return bool(INCLUDE_TITLE_PATTERN.search(clean_title))


INDIA_LOCATION_KEYWORDS: tuple[str, ...] = (
    "india",
    "bangalore",
    "bengaluru",
    "hyderabad",
    "pune",
    "noida",
    "gurgaon",
    "gurugram",
    "delhi",
    "ncr",
    "mumbai",
    "chennai",
    "secunderabad",
    "madras",
    "thane",
)

REMOTE_KEYWORDS: tuple[str, ...] = (
    "remote",
    "anywhere in india",
    "distributed",
    "work from home",
    "remote - india",
    "india - remote",
    "apac - remote",
    "remote, india",
    "wfh",
    "virtual",
    "telecommute",
)

FOREIGN_REMOTE_EXCLUSIONS: tuple[str, ...] = (
    "us remote",
    "remote - us",
    "remote (us)",
    "remote, us",
    "remote - usa",
    "remote - north america",
    "emea remote",
    "remote - emea",
    "remote - europe",
    "uk remote",
    "remote - uk",
    "canada remote",
    "remote - canada",
    "germany remote",
    "australia remote",
    "latam remote",
    "remote - latam",
)

# Positive regex check for fresher and 0-2 years of experience indicators
FRESHER_EXPERIENCE_PATTERN: re.Pattern[str] = re.compile(
    r"""
    (?ix)
    \b(
        (?:0\s*(?:-|–|—|to)\s*[1-2]|1\s*(?:-|–|—|to)\s*2|\b0\b|\b1\b|\b2\b)\s*(?:years?|yrs?)(?:\s+of)?(?:\s+\w+){0,3}\s+experience |
        (?:0\s*(?:-|–|—|to)\s*1|0\s*(?:-|–|—|to)\s*2|1\s*(?:-|–|—|to)\s*2)\s*(?:years?|yrs?) |
        freshers?\s+(?:are\s+)?(?:eligible|welcome) |
        (?:2024|2025|2026|2027)\s+batch |
        (?:class|graduating|pass[- ]?out)\s+of\s+(?:2024|2025|2026|2027) |
        (?:0\s*years?|no(?:\s+prior)?)\s+experience\s+(?:required|needed) |
        (?:0\s*yoe|1\s*yoe|2\s*yoe|0-1\s*yoe|0-2\s*yoe|1-2\s*yoe)
    )\b
    """,
    re.VERBOSE | re.IGNORECASE,
)


def is_foreign_remote_location(location: str) -> bool:
    """Check if location explicitly restricts remote work to a non-Indian region."""
    if not location or not location.strip():
        return False
    loc_lower = location.lower()
    # If explicitly mentioning India or Indian hubs, it is not an exclusively foreign remote role
    if "india" in loc_lower or any(
        city in loc_lower
        for city in (
            "bengaluru",
            "bangalore",
            "hyderabad",
            "pune",
            "delhi",
            "gurgaon",
            "gurugram",
            "noida",
            "mumbai",
            "chennai",
            "secunderabad",
        )
    ):
        return False
    return any(ex in loc_lower for ex in FOREIGN_REMOTE_EXCLUSIONS)


def is_remote_location(location: str) -> bool:
    """Check if location string denotes a remote work arrangement valid for India."""
    if not location or not location.strip():
        return False
    loc_lower = location.lower()
    if is_foreign_remote_location(loc_lower):
        return False
    return any(kw in loc_lower for kw in REMOTE_KEYWORDS)


def is_remote_opening(job: object) -> bool:
    """Determine if a job opening is a remote role eligible for candidates in India.

    Inspects job.location and raw payload metadata (e.g. is_remote, workplace_type).
    """
    if job is None:
        return False

    # Check is_remote attribute if set directly
    if getattr(job, "is_remote", False):
        loc = getattr(job, "location", "")
        if loc and is_foreign_remote_location(str(loc)):
            return False
        return True

    # Extract location string from object, dict, or string
    loc = getattr(job, "location", None)
    if loc is None and isinstance(job, dict):
        loc = job.get("location")
        if isinstance(loc, dict):
            loc = loc.get("name") or loc.get("text") or ""
    if loc is None and isinstance(job, str):
        loc = job

    if loc and isinstance(loc, str):
        if is_remote_location(loc):
            return True

    # Check payload metadata (workplace_type, workplaceType, etc.)
    metadata = getattr(job, "extra", None) or (job if isinstance(job, dict) else {})
    if isinstance(metadata, dict):
        workplace_type = str(
            metadata.get("workplace_type")
            or metadata.get("workplaceType")
            or metadata.get("telecommute")
            or ""
        ).lower()
        if workplace_type in {"remote", "virtual", "telecommute", "distributed"}:
            if loc and is_foreign_remote_location(str(loc)):
                return False
            return True

    return False


def is_potential_india_location(location: str) -> bool:
    """Fast short-circuit check: discard non-India locations via string check before regex evaluation."""
    if not location or not location.strip():
        return False
    loc_lower = location.lower()

    # Reject foreign-restricted remote locations (e.g. US Remote, Remote - EMEA)
    if is_foreign_remote_location(loc_lower):
        return False

    # Match Indian tech cities or India
    if any(kw in loc_lower for kw in INDIA_LOCATION_KEYWORDS):
        return True

    # Regional or global remote roles (e.g. APAC - Remote, Global Remote, Anywhere in India)
    if any(rk in loc_lower for rk in ("remote", "distributed", "anywhere", "wfh")):
        if any(reg in loc_lower for reg in ("apac", "asia", "global", "worldwide")):
            return True

    return False


def matches_india_location(location: str) -> bool:
    """Check if location string matches target Indian tech hubs or India remote."""
    if not is_potential_india_location(location):
        return False

    clean_location = location.strip()
    if LOCATION_PATTERN.search(clean_location):
        return True

    # Allow regional remote locations paired with global/APAC eligibility
    loc_lower = clean_location.lower()
    if any(rk in loc_lower for rk in ("remote", "distributed", "anywhere", "wfh")):
        if any(reg in loc_lower for reg in ("apac", "asia", "global", "worldwide")):
            return True

    return False


def is_entry_level(
    job_or_title: object,
    content: str = "",
    target_grad_year: Optional[int] = None,
) -> bool:
    """Check if a job role targets freshers / entry-level / 0-2 YOE candidates.

    High-recall matching for terms like 'Associate Software Engineer', 'Graduate
    Engineer Trainee', 'GET', 'SDE 1', 'SDE-1', 'Software Engineer 1', 'MTS 1',
    'Junior Software Engineer', 'Analyst', and 0-2 years of experience requirements.
    When target_grad_year is specified (e.g. 2027), checks for batch and student constraints.
    """
    if isinstance(job_or_title, str):
        title = job_or_title
    elif hasattr(job_or_title, "title") and not callable(getattr(job_or_title, "title")):
        title = str(getattr(job_or_title, "title", ""))
        if not content:
            content = getattr(job_or_title, "content", "") or getattr(job_or_title, "description", "")
    else:
        title = str(job_or_title)

    if not title or not title.strip():
        return False

    clean_title = title.strip()
    sanitized = _MTS_MASK_PATTERN.sub("mts_role", clean_title)

    # If title contains explicit senior/staff exclusions, reject immediately
    if EXCLUDE_TITLE_PATTERN.search(sanitized):
        return False

    title_matches = matches_target_title(clean_title)

    # High-recall matching for additional entry-level / fresher / analyst title terms
    if not title_matches:
        if re.search(
            r"(?i)(?:^\s*analyst\b|\b(?:data\s+|software\s+|technology\s+|systems?\s+)?analyst\s*[-–—]?\s*(?:1|i)\b|\bassociate\s+(?:software\s+|data\s+)?(?:engineer|analyst)\b|\bjunior\s+(?:software\s+|data\s+)?(?:engineer|developer|analyst)\b|\bgraduate\s+engineer\s+trainee\b|\bget\b|\bsde\s*[-–—]?\s*1\b|\bsoftware\s+engineer\s+1\b|\bmts\s*[-–—]?\s*1\b)",
            clean_title,
        ):
            title_matches = True

    if not title_matches:
        # If title is generic (e.g. "Software Engineer"), check if content explicitly specifies 0-2 YOE / freshers eligible
        if content and FRESHER_EXPERIENCE_PATTERN.search(content) and not requires_experienced_candidate(content):
            title_matches = True
        else:
            return False

    # For GET / Trainee roles, discard if non-tech disciplines are mentioned in title or content
    if _GET_PATTERN.search(clean_title):
        full_text = f"{clean_title} {content}".strip()
        if _NON_TECH_DISCIPLINE_PATTERN.search(full_text):
            return False
        if content and content.strip() and not _TECH_DISCIPLINE_PATTERN.search(full_text):
            return False

    # If title matches, verify content doesn't require experienced candidate (3+ years)
    if content and content.strip():
        if requires_experienced_candidate(content):
            return False

        # If evaluating for a specific student graduation year (e.g. 2027)
        if target_grad_year == 2027:
            if PAST_BATCH_EXCLUSION_PATTERN.search(content):
                return False
            # If immediate fulltime degree in hand required and not an internship
            is_intern = bool(re.search(r"(?i)\b(?:intern|internship|co[- ]?op|apprentice)\b", clean_title))
            if not is_intern and IMMEDIATE_FULLTIME_JOIN_PATTERN.search(content):
                return False

    return True


# ---------------------------------------------------------------------------
# Application Date & Deadline Extraction & Expiration Helpers
# ---------------------------------------------------------------------------

MONTH_MAP: dict[str, int] = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "september": 9, "oct": 10, "october": 10,
    "nov": 11, "november": 11, "dec": 12, "december": 12,
}

_DEADLINE_PATTERNS = [
    re.compile(
        r"""(?ix)
        \b(?:
            last\s+date(?:\s+to\s+apply)? |
            application\s+deadline |
            apply\s+(?:before|by) |
            closing\s+date |
            registration\s+ends? |
            closes?\s+on |
            deadline\s*(?:to\s+apply)? |
            end\s+date
        )\b
        \s*[:\-–—]?\s*
        ([^\n\r;<|•]+)
        """
    ),
    re.compile(
        r"""(?ix)
        \b(?:apply\s+by|closes\s+on|registration\s+ends)\s+
        ([0-9]{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+[,\s\-]+[0-9]{4}|[A-Za-z]+\s+[0-9]{1,2}(?:st|nd|rd|th)?[,\s\-]+[0-9]{4}|[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}[-/][0-9]{1,2}[-/][0-9]{4})
        """
    ),
]

_START_DATE_PATTERNS = [
    re.compile(
        r"""(?ix)
        \b(?:
            start\s+date |
            posted\s+(?:on|date)? |
            registration\s+starts? |
            opening\s+date |
            published\s+(?:on|date)? |
            applications?\s+open
        )\b
        \s*[:\-–—]?\s*
        ([^\n\r;<|•]+)
        """
    ),
]


def normalize_date_str(val: Any, default_year: Optional[int] = None) -> Optional[str]:
    """Normalize various date representations into ISO YYYY-MM-DD format.

    Handles:
    - ISO timestamps: '2026-09-30', '2026-09-30T18:30:00.000Z'
    - Slash/dash dates: '30/09/2026', '30-09-2026', '09/30/2026'
    - Verbal dates: '30 Sep 2026', 'September 30, 2026', '30th September, 2026', '15-Oct-2026'
    - Dates without explicit year: 'Sep 30', '30 Sep' (defaults to current year)
    """
    if not val:
        return None
    val_str = str(val).strip()
    if val_str.lower() in ("recent", "active", "none", "n/a", "null", ""):
        return None

    # 1. ISO format: 2026-09-30 or 2026-09-30T...
    m_iso = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", val_str)
    if m_iso:
        try:
            d = date(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)))
            return d.isoformat()
        except ValueError:
            pass

    # 2. DD/MM/YYYY or DD-MM-YYYY or MM/DD/YYYY
    m_dmy = re.search(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b", val_str)
    if m_dmy:
        p1, p2, yr = int(m_dmy.group(1)), int(m_dmy.group(2)), int(m_dmy.group(3))
        if p1 > 12 and p2 <= 12:
            d_val, m_val = p1, p2
        elif p2 > 12 and p1 <= 12:
            d_val, m_val = p2, p1
        else:
            d_val, m_val = p1, p2
        try:
            return date(yr, m_val, d_val).isoformat()
        except ValueError:
            pass

    clean = re.sub(r"(\d+)(?:st|nd|rd|th)\b", r"\1", val_str, flags=re.IGNORECASE)

    # 3. Month DD, YYYY or Month DD YYYY
    m_mdy = re.search(r"\b([A-Za-z]{3,9})\s+(\d{1,2})[,\s\-]+(\d{4})\b", clean)
    if m_mdy:
        m_name = m_mdy.group(1).lower()
        if m_name in MONTH_MAP:
            try:
                return date(int(m_mdy.group(3)), MONTH_MAP[m_name], int(m_mdy.group(2))).isoformat()
            except ValueError:
                pass

    # 4. DD Month YYYY or DD-Month-YYYY
    m_dmy_verbal = re.search(r"\b(\d{1,2})[- \s]+([A-Za-z]{3,9})[,\s\-]+(\d{4})\b", clean)
    if m_dmy_verbal:
        m_name = m_dmy_verbal.group(2).lower()
        if m_name in MONTH_MAP:
            try:
                return date(int(m_dmy_verbal.group(3)), MONTH_MAP[m_name], int(m_dmy_verbal.group(1))).isoformat()
            except ValueError:
                pass

    cur_year = default_year or datetime.now(timezone.utc).year

    # 5. Month DD without year: 'Sep 30', 'October 5'
    m_md_noyear = re.search(r"\b([A-Za-z]{3,9})\s+(\d{1,2})\b", clean)
    if m_md_noyear:
        m_name = m_md_noyear.group(1).lower()
        if m_name in MONTH_MAP:
            try:
                return date(cur_year, MONTH_MAP[m_name], int(m_md_noyear.group(2))).isoformat()
            except ValueError:
                pass

    # 6. DD Month without year: '30 Sep', '15 October'
    m_dm_noyear = re.search(r"\b(\d{1,2})[- \s]+([A-Za-z]{3,9})\b", clean)
    if m_dm_noyear:
        m_name = m_dm_noyear.group(2).lower()
        if m_name in MONTH_MAP:
            try:
                return date(cur_year, MONTH_MAP[m_name], int(m_dm_noyear.group(1))).isoformat()
            except ValueError:
                pass

    return None


def is_date_expired(
    date_val: Optional[Union[str, date, datetime]],
    ref_date: Optional[Union[str, date, datetime]] = None,
) -> bool:
    """Check if an application closing date or deadline has strictly passed."""
    if not date_val:
        return False

    if isinstance(date_val, datetime):
        d = date_val.date()
    elif isinstance(date_val, date):
        d = date_val
    else:
        norm = normalize_date_str(date_val)
        if not norm:
            return False
        try:
            d = date.fromisoformat(norm)
        except ValueError:
            return False

    if ref_date is None:
        target_ref = datetime.now(timezone.utc).date()
    elif isinstance(ref_date, datetime):
        target_ref = ref_date.date()
    elif isinstance(ref_date, date):
        target_ref = ref_date
    else:
        ref_norm = normalize_date_str(ref_date)
        if ref_norm:
            try:
                target_ref = date.fromisoformat(ref_norm)
            except ValueError:
                target_ref = datetime.now(timezone.utc).date()
        else:
            target_ref = datetime.now(timezone.utc).date()

    return d < target_ref


def extract_application_dates(
    text: Optional[str],
    published_date: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Extract (application_start_date, application_end_date) in ISO format (YYYY-MM-DD).

    Scans text/notes/descriptions for start dates, registration dates, posted dates,
    and application deadlines/closing dates.
    Falls back to published_date for application_start_date when not explicitly found in text.
    """
    start_date: Optional[str] = None
    end_date: Optional[str] = None

    if text and text.strip():
        for pat in _DEADLINE_PATTERNS:
            m = pat.search(text)
            if m:
                cand = normalize_date_str(m.group(1))
                if cand:
                    end_date = cand
                    break

        for pat in _START_DATE_PATTERNS:
            m = pat.search(text)
            if m:
                cand = normalize_date_str(m.group(1))
                if cand:
                    start_date = cand
                    break

    if not start_date and published_date:
        start_date = normalize_date_str(published_date)

    return (start_date, end_date)


