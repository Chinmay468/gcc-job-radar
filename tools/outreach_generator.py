"""outreach_generator.py — High-conversion recruiter outreach, InMail, and connection note generator.

Generates 1-click tailored outreach messages for LinkedIn, email, and cold messaging:
1. LinkedIn Connection Note (<300 chars limit)
2. Hiring Manager / Recruiter InMail & Cold Email (Subject + 3 punchy value bullets)
3. Peer Engineer / Referral Request
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CANDIDATE_NAME = "Chinmay Maheshwari"
CANDIDATE_EMAIL = "chinmaymaheshwari.it27@gmail.com"
CANDIDATE_PORTFOLIO = "https://github.com/Chinmay468"
CANDIDATE_LINKEDIN = "https://www.linkedin.com/in/chinmay8064/"


def extract_key_stack(jd_text: str, detected_skills: Optional[List[str]] = None) -> List[str]:
    """Identify top 2-3 matching stack skills from JD or detected skills."""
    if detected_skills and len(detected_skills) >= 2:
        return detected_skills[:3]

    text = (jd_text or "").lower()
    stack = []
    if "java" in text or "spring" in text:
        stack.extend(["Java", "Spring Boot"])
    if "kafka" in text:
        stack.append("Kafka")
    if "react" in text or "frontend" in text:
        stack.append("React.js")
    if "node" in text or "express" in text or "mern" in text:
        stack.extend(["Node.js", "MongoDB"])
    if "docker" in text or "ci/cd" in text:
        stack.append("Docker")

    # Fallback to candidate core strengths
    if not stack:
        stack = ["Java", "Spring Boot", "Distributed Systems"]
    return list(dict.fromkeys(stack))[:3]


def generate_connection_note(
    company: str,
    role: str,
    stack: List[str],
    recipient_type: str = "recruiter",
) -> str:
    """Generate a high-converting LinkedIn Connection Note strictly within 300 characters."""
    comp = company.strip() or "your team"
    r = role.strip() or "Engineering"
    tech_str = " & ".join(stack[:2]) if len(stack) >= 2 else (stack[0] if stack else "Java & Spring Boot")

    if recipient_type == "hiring_manager":
        note = (
            f"Hi! Saw you're hiring for {r} at {comp}. I specialize in {tech_str} "
            f"and built distributed microservices with Kafka & Docker. "
            f"Would love to connect and follow your team's engineering work!"
        )
    elif recipient_type == "peer_engineer":
        note = (
            f"Hi! Came across your work at {comp}. I'm an engineer working with {tech_str} "
            f"and love your team's tech stack. Would be great to connect and learn about "
            f"engineering at {comp}!"
        )
    else:  # recruiter / general
        note = (
            f"Hi! Noticed the {r} opening at {comp}. I have hands-on experience in "
            f"{tech_str} building scalable REST APIs & microservices. "
            f"Would love to connect and stay in touch regarding opportunities!"
        )

    # Strictly guarantee <= 300 chars
    if len(note) > 300:
        note = note[:297].rstrip() + "..."
    return note


def generate_cold_email(
    company: str,
    role: str,
    stack: List[str],
    recipient_type: str = "recruiter",
) -> Dict[str, str]:
    """Generate professional Cold Email / InMail with subject line and 3 punchy bullet points."""
    comp = company.strip() or "Company"
    r = role.strip() or "Software Engineer"
    tech_str = ", ".join(stack) if stack else "Java, Spring Boot, Kafka, Docker"

    subject = f"Application: {CANDIDATE_NAME} — {r} ({tech_str.split(',')[0].strip()})"

    # Select project highlight based on tech stack
    has_java = any("java" in s.lower() or "spring" in s.lower() or "kafka" in s.lower() for s in stack)
    if has_java:
        project_bullet = (
            "Distributed Microservices: Built an asynchronous order processing pipeline with "
            "Spring Boot, Kafka, and Redis; stress-tested with 50-thread concurrent validation "
            "and idempotent processing."
        )
    else:
        project_bullet = (
            "Full Stack Web Systems: Built Skill-Bridge job portal system with React, Node.js, "
            "MongoDB, and JWT auth with optimized queries and modular REST architecture."
        )

    greeting = "Hi [Hiring Manager / Team]," if recipient_type == "hiring_manager" else "Hi [Recruiter / Talent Team],"
    body = (
        f"{greeting}\n\n"
        f"I came across the {r} opening at {comp} and wanted to reach out directly. "
        f"With hands-on experience in {tech_str}, I build clean, reliable backend services and web applications.\n\n"
        f"Key highlights relevant to {comp}:\n"
        f"• Core Stack: Strong foundation in {tech_str}, RESTful APIs, and relational/NoSQL databases.\n"
        f"• {project_bullet}\n"
        f"• Modern Workflow: Production mindset with Docker containerization, CI/CD automation, and fast iteration.\n\n"
        f"I would welcome the opportunity to discuss how my background aligns with {comp}'s goals. "
        f"My code is available at {CANDIDATE_PORTFOLIO} and resume can be shared immediately.\n\n"
        f"Best regards,\n"
        f"{CANDIDATE_NAME}\n"
        f"{CANDIDATE_EMAIL} | {CANDIDATE_LINKEDIN}"
    )

    return {
        "subject": subject,
        "body": body,
    }


def generate_referral_request(
    company: str,
    role: str,
    stack: List[str],
) -> str:
    """Generate a respectful peer engineer referral / informational interview message."""
    comp = company.strip() or "your company"
    r = role.strip() or "Software Engineer"
    tech_str = " & ".join(stack[:2]) if len(stack) >= 2 else (stack[0] if stack else "Java / Spring Boot")

    return (
        f"Hi [Name],\n\n"
        f"Hope you're doing well! I saw that {comp} is actively hiring for a {r}. "
        f"Your team's work caught my eye, especially around {tech_str}.\n\n"
        f"I specialize in {tech_str} and have built production-grade distributed microservices "
        f"(Kafka, Redis, Docker) and full-stack systems. "
        f"I have already reviewed the role requirements and feel it's a strong fit.\n\n"
        f"If you're open to it, could you refer me or share any insights on the team's interview focus? "
        f"Happy to send over my resume and project links.\n\n"
        f"Thanks for your time either way!\n"
        f"Best,\n"
        f"{CANDIDATE_NAME}"
    )


def generate_all_outreach(
    company: str,
    role: str,
    jd_text: str = "",
    detected_skills: Optional[List[str]] = None,
    recipient_type: str = "recruiter",
) -> Dict[str, Any]:
    """Generate full outreach package: LinkedIn note, Cold InMail/Email, and Referral request."""
    stack = extract_key_stack(jd_text, detected_skills)

    connection_note = generate_connection_note(company, role, stack, recipient_type)
    cold_email = generate_cold_email(company, role, stack, recipient_type)
    referral_note = generate_referral_request(company, role, stack)

    return {
        "success": True,
        "company": company,
        "role": role,
        "target_stack": stack,
        "recipient_type": recipient_type,
        "connection_note": connection_note,
        "connection_note_char_count": len(connection_note),
        "connection_note_limit": 300,
        "cold_email_subject": cold_email["subject"],
        "cold_email_body": cold_email["body"],
        "referral_note": referral_note,
    }
