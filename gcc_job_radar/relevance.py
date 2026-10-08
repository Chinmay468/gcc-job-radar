"""Stack-relevance scoring engine for personal job-hunt optimization.

Target Stack:
- Java / Spring Boot 3
- MERN (MongoDB, Express, React, Node.js)
- Apache Kafka
- MySQL & Relational DBs
- JWT (JSON Web Tokens) & Authentication
- Docker & Containerization
- GitHub Actions & CI/CD
"""

import re
from typing import Optional, Sequence

# Tier 1: Core Stack Matches (Highest Priority, +20 to +25 points each)
TIER1_PATTERNS: list[tuple[re.Pattern[str], int, str]] = [
    (re.compile(r"(?i)(?<!javascript\b)\bjava\b(?!script)", re.IGNORECASE), 25, "Java"),
    (re.compile(r"(?i)\bspring(?:\s*boot(?:\s*3)?)?\b", re.IGNORECASE), 25, "Spring Boot"),
    (re.compile(r"(?i)\breact(?:\.js|js)?\b", re.IGNORECASE), 20, "React"),
    (re.compile(r"(?i)\bnode(?:\.js|js)?\b", re.IGNORECASE), 20, "Node.js"),
    (re.compile(r"(?i)\bexpress(?:\.js|js)?\b", re.IGNORECASE), 15, "Express"),
    (re.compile(r"(?i)\bkafka\b", re.IGNORECASE), 20, "Kafka"),
    (re.compile(r"(?i)\bmongo(?:db)?\b", re.IGNORECASE), 20, "MongoDB"),
    (re.compile(r"(?i)\bmysql\b", re.IGNORECASE), 20, "MySQL"),
]

# Tier 2: Essential Architecture & DevOps (High Priority, +10 to +15 points each)
TIER2_PATTERNS: list[tuple[re.Pattern[str], int, str]] = [
    (re.compile(r"(?i)\bdocker\b", re.IGNORECASE), 15, "Docker"),
    (re.compile(r"(?i)\b(?:jwt|json\s+web\s+tokens?)\b", re.IGNORECASE), 15, "JWT"),
    (re.compile(r"(?i)\bgithub\s+actions\b", re.IGNORECASE), 15, "GitHub Actions"),
    (re.compile(r"(?i)\b(?:ci\s*/\s*cd|continuous\s+integration)\b", re.IGNORECASE), 10, "CI/CD"),
    (re.compile(r"(?i)\b(?:rest(?:ful)?(?:\s*apis?)?|microservices?)\b", re.IGNORECASE), 10, "REST/Microservices"),
    (re.compile(r"(?i)\bmern\b", re.IGNORECASE), 25, "MERN"),
]

# Tier 3: Supporting Technologies & Tools (+5 to +10 points each)
TIER3_PATTERNS: list[tuple[re.Pattern[str], int, str]] = [
    (re.compile(r"(?i)\btypescript\b", re.IGNORECASE), 10, "TypeScript"),
    (re.compile(r"(?i)\b(?:sql|postgresql|postgres)\b", re.IGNORECASE), 10, "SQL/PostgreSQL"),
    (re.compile(r"(?i)\bredis\b", re.IGNORECASE), 10, "Redis"),
    (re.compile(r"(?i)\bgit\b", re.IGNORECASE), 5, "Git"),
    (re.compile(r"(?i)\blinux\b", re.IGNORECASE), 5, "Linux"),
]

# Title Affinity Bonus
TITLE_BONUS_PATTERNS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"(?i)(?<!javascript\b)\bjava\b(?!script)"), 20),
    (re.compile(r"(?i)\b(?:backend|back-end)\b"), 15),
    (re.compile(r"(?i)\b(?:full\s*stack|fullstack|mern)\b"), 15),
    (re.compile(r"(?i)\bspring(?:\s*boot)?\b"), 15),
    (re.compile(r"(?i)\b(?:software\s+engineer|sde|mts)\b"), 10),
]

# Non-stack Penalties (roles focused on unrelated ecosystems when target stack is absent)
NON_STACK_DISQUALIFIERS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"(?i)\b(?:ios|swift|android|kotlin|flutter|react\s+native)\b"), 25),
    (re.compile(r"(?i)\b(?:embedded|firmware|hardware|vlsi|verilog)\b"), 30),
    (re.compile(r"(?i)\b(?:salesforce|apex|sap|abap|servicenow)\b"), 30),
    (re.compile(r"(?i)\b(?:ruby|rails|php|laravel|c#|\.net)\b"), 20),
]

# Degree Requirement Risk (strict CS-only degree requirements without 'or related' escape hatch)
DEGREE_RISK_PATTERN = re.compile(
    r"(?i)\b(?:(?:b\.?s\.?|bachelor(?:'s)?(?:\s+degree)?|degree)\s+in\s+computer\s+science|\bcs\s+degree)\b"
    r"(?!\s*[,/]?\s*(?:or\s+|/\s*|,\s*)?(?:related|equivalent|similar|information\s+technology|it\b|a\s+related|an\s+equivalent))",
)


def _has_strict_cs_requirement(description: str) -> bool:
    """True if the posting demands a CS degree with no 'or related field' escape hatch."""
    return bool(DEGREE_RISK_PATTERN.search(description))


# Relocation & Visa Sponsorship Requirement Flag (informational, non-penalizing)
RELOCATION_RISK_PATTERN = re.compile(
    r"(?i)(willing to relocate|relocation (is )?required|must relocate|"
    r"visa sponsorship|work visa|right to work in|in-office \d+ days? per week|"
    r"hybrid.{0,20}\d+ days? per week)",
)


def _has_relocation_requirement(description: str) -> bool:
    """True if the posting requires relocation or has a hard in-office attendance requirement."""
    return bool(RELOCATION_RISK_PATTERN.search(description))


def evaluate_job_relevance(
    title: str,
    description: str = "",
    skills: Optional[Sequence[str]] = None,
    published_date: Optional[str] = None,
    is_remote: bool = False,
) -> tuple[int, list[str], str]:
    """Calculate personal tech-stack score, matched keyword list, and deterministic why rationale.

    Evaluates title, description, and skill tags against the target stack:
    Java/Spring Boot 3, MERN, Kafka, MongoDB, MySQL, JWT, Docker, GitHub Actions.

    Returns:
        tuple[int, list[str], str]: (score, matched_reasons, why_line)
    """
    if not title and not description and not skills:
        return 0, [], "Verified entry-level opening at target tech hub"

    combined_text = f"{title}\n{description}"
    if skills:
        combined_text += f"\n{' '.join(skills)}"

    raw_score = 0
    matched_stack_ordered: list[str] = []
    seen_labels: set[str] = set()

    # 1. Evaluate Tier 1 Core Stack
    for pattern, weight, label in TIER1_PATTERNS:
        if pattern.search(combined_text):
            raw_score += weight
            if label not in seen_labels:
                seen_labels.add(label)
                matched_stack_ordered.append(label)

    # 2. Evaluate Tier 2 Architecture & DevOps
    for pattern, weight, label in TIER2_PATTERNS:
        if pattern.search(combined_text):
            raw_score += weight
            if label not in seen_labels:
                seen_labels.add(label)
                matched_stack_ordered.append(label)

    # 3. Evaluate Tier 3 Supporting Technologies
    for pattern, weight, label in TIER3_PATTERNS:
        if pattern.search(combined_text):
            raw_score += weight
            if label not in seen_labels:
                seen_labels.add(label)
                matched_stack_ordered.append(label)

    # 4. Evaluate Title Affinity Bonus
    for pattern, bonus in TITLE_BONUS_PATTERNS:
        if pattern.search(title):
            raw_score += bonus
            break  # Apply the highest single title bonus

    # 5. Apply Penalties for non-stack roles if no Tier 1 core stack matched
    matched_tier1 = any(pattern.search(combined_text) for pattern, _, _ in TIER1_PATTERNS)
    if not matched_tier1:
        for pattern, penalty in NON_STACK_DISQUALIFIERS:
            if pattern.search(title) or pattern.search(combined_text):
                raw_score -= penalty

    # Degree-requirement risk (soft penalty, not a hard exclude)
    degree_risk = _has_strict_cs_requirement(combined_text)
    if degree_risk:
        raw_score -= 10

    # Normalize to 0 - 100 bounds
    final_score = max(0, min(100, raw_score))

    # Experience indicators
    is_fresher = bool(
        re.search(
            r"(?i)\b(?:0\s*[-–to]\s*[12]\s*(?:years?|yrs?|yoe)|fresher|entry[\s-]level|intern(?:ship)?|graduate\s+engineer|trainee)\b",
            combined_text,
        )
    )

    # Freshness / Staleness detection from published_date
    is_stale = False
    is_fresh = False
    if published_date:
        p_str = str(published_date).strip().lower()
        if re.search(r"(?:[3-9]|\d{2,})\s*weeks?\s*ago|[1-9]\s*months?\s*ago", p_str):
            is_stale = True
        elif re.search(r"hour|today|yesterday|1\s*day\s*ago|2\s*days?\s*ago", p_str):
            is_fresh = True
        else:
            m_date = re.search(r"\b(20\d\d)-(\d{2})-(\d{2})\b", p_str)
            if m_date:
                try:
                    from datetime import date
                    p_dt = date(int(m_date.group(1)), int(m_date.group(2)), int(m_date.group(3)))
                    days_diff = (date.today() - p_dt).days
                    if days_diff >= 21:
                        is_stale = True
                    elif days_diff <= 3:
                        is_fresh = True
                except Exception:
                    pass

    # Synthesize deterministic why rationale
    if final_score >= 40:
        if len(matched_stack_ordered) >= 2:
            top_tech = " + ".join(matched_stack_ordered[:2])
            qualifiers = ["Core target stack"]
            if is_fresher:
                qualifiers.append("0-2 YOE")
            if is_remote:
                qualifiers.append("remote-friendly")
            why = f"Matches {top_tech} • {' • '.join(qualifiers)}"
        elif len(matched_stack_ordered) == 1:
            qualifiers = ["Core target stack"]
            if is_fresher:
                qualifiers.append("0-2 YOE")
            if is_remote:
                qualifiers.append("remote-friendly")
            why = f"Strong match: {matched_stack_ordered[0]} • {' • '.join(qualifiers)}"
        else:
            qualifiers = []
            if is_fresher:
                qualifiers.append("0-2 YOE")
            if is_remote:
                qualifiers.append("remote-friendly")
            suffix = f" • {' • '.join(qualifiers)}" if qualifiers else ""
            why = f"Strong match for target engineering role{suffix}"

    elif final_score >= 20:
        if len(matched_stack_ordered) >= 2:
            top_tech = " + ".join(matched_stack_ordered[:2])
            qualifiers = ["Target stack"]
            if is_remote:
                qualifiers.append("remote-friendly")
            why = f"Good match: {top_tech} • {' • '.join(qualifiers)}"
        elif len(matched_stack_ordered) == 1:
            tech = matched_stack_ordered[0]
            qualifiers = []
            if is_remote:
                qualifiers.append("remote-friendly")
            suffix = f" • {' • '.join(qualifiers)}" if qualifiers else ""
            why = f"Matches {tech}{suffix}"
        else:
            why = "Good match for entry-level tech role"
            if is_remote:
                why += " • Remote-friendly"

    else:
        # Score < 20
        from gcc_job_radar.config import EXCLUDE_TITLE_PATTERN
        from gcc_job_radar.filters import _MTS_MASK_PATTERN, requires_experienced_candidate

        sanitized_t = _MTS_MASK_PATTERN.sub("mts_role", title)
        is_senior = bool(EXCLUDE_TITLE_PATTERN.search(sanitized_t))
        is_experienced = is_senior or requires_experienced_candidate(title) or requires_experienced_candidate(description)

        if is_experienced:
            why = "Experienced tech role (requires prior experience)"
            if is_remote:
                why += " • Remote-friendly"
        elif is_stale:
            if matched_stack_ordered:
                why = f"Borderline: matches {matched_stack_ordered[0]} but posted 3+ weeks ago"
            else:
                why = "Borderline: general tech role • Posted 3+ weeks ago"
        elif is_fresh:
            why = "Fresh posting at target GCC • Entry-level tech role"
            if is_remote:
                why += " • Remote-friendly"
        elif is_remote:
            why = "Verified entry-level opening • Remote-friendly"
        else:
            why = "Verified entry-level opening at target tech hub"

    if degree_risk:
        why += " • ⚠️ Lists CS-only degree req (may be screened)"

    relocation_req = _has_relocation_requirement(combined_text)
    if relocation_req:
        why += " • 📍 Requires relocation/visa sponsorship — confirm before applying"

    return final_score, matched_stack_ordered, why


def calculate_relevance_score(
    title: str,
    description: str = "",
    skills: Optional[Sequence[str]] = None,
) -> int:
    """Calculate personal stack-relevance score (0 to 100) for a job posting.

    Evaluates title, description, and skill tags against the target stack:
    Java/Spring Boot 3, MERN, Kafka, MongoDB, MySQL, JWT, Docker, GitHub Actions.
    """
    score, _, _ = evaluate_job_relevance(title=title, description=description, skills=skills)
    return score


def score_job_posting(job: object) -> int:
    """Compute and attach relevance score, matched_reasons, and why to a JobPosting or job dictionary."""
    title = str(getattr(job, "title", "") if not isinstance(job, dict) else job.get("title", ""))
    desc = str(
        getattr(job, "description", "")
        or getattr(job, "content", "")
        or getattr(job, "notes", "")
        if not isinstance(job, dict)
        else (job.get("description") or job.get("content") or job.get("notes") or "")
    )
    pub_date = getattr(job, "published_date", None) if not isinstance(job, dict) else job.get("published_date")
    is_remote = bool(getattr(job, "is_remote", False) if not isinstance(job, dict) else job.get("is_remote", False))
    skills = getattr(job, "skills", None) if not isinstance(job, dict) else job.get("skills")

    score, reasons, why = evaluate_job_relevance(
        title=title,
        description=desc,
        skills=skills,
        published_date=str(pub_date) if pub_date else None,
        is_remote=is_remote,
    )

    if hasattr(job, "relevance_score"):
        setattr(job, "relevance_score", score)
    if hasattr(job, "matched_reasons"):
        setattr(job, "matched_reasons", reasons)
    if hasattr(job, "why"):
        setattr(job, "why", why)

    if isinstance(job, dict):
        job["relevance_score"] = score
        job["matched_reasons"] = reasons
        job["why"] = why

    return score
