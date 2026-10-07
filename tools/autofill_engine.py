#!/usr/bin/env python3
"""
autofill_engine.py — Candidate Profile & ATS Screening Question Assistant.

Provides:
  1. Standardized candidate profile data for 1-click ATS form filling (Greenhouse, Lever, Ashby, Workday).
  2. Question intent classifier & answer generator for job application screening prompts.
  3. Groq-powered contextual screening answers when API key is available, with instant offline heuristic fallback.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("autofill_engine")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROFILE_STORE_PATH = PROJECT_ROOT / "data" / "candidate_profile.json"

DEFAULT_CANDIDATE_PROFILE: Dict[str, Any] = {
    "first_name": "Chinmay",
    "last_name": "Maheshwari",
    "full_name": "Chinmay Maheshwari",
    "email": "chinmaymaheshwari.it27@gmail.com",
    "phone": "+91 9460449962",
    "phone_national": "9460449962",
    "city": "Jaipur",
    "state": "Rajasthan",
    "country": "India",
    "location": "Jaipur, India",
    "address": "Jaipur, Rajasthan, India",
    "postal_code": "302022",
    "linkedin": "https://www.linkedin.com/in/chinmay8064/",
    "github": "https://github.com/Chinmay468",
    "portfolio": "https://github.com/Chinmay468",
    "degree": "B.Tech in Information Technology",
    "degree_type": "Bachelor's Degree",
    "field_of_study": "Information Technology",
    "school": "Jaipur Engineering College and Research Centre",
    "university": "Jaipur Engineering College and Research Centre",
    "graduation_year": "2027",
    "graduation_month": "June",
    "gpa": "8.8",
    "gpa_max": "10",
    "current_company": "Self-Employed / Student",
    "current_title": "Software Engineer",
    "total_experience_years": "1",
    "notice_period": "Immediate",
    "notice_period_days": "0",
    "authorized_in_country": "Yes",
    "visa_sponsorship_needed": "No",
    "willing_to_relocate": "Yes",
    "gender": "Male",
    "veteran": "No",
    "disability": "No",
    "summary": (
        "Final-year B.Tech (IT) student at JECRC with hands-on experience building "
        "production-ready backend services using Java, Spring Boot, Kafka, Docker, and React."
    ),
    "core_stack": ["Java", "Spring Boot", "Kafka", "Docker", "React.js", "Node.js", "MySQL", "MongoDB"],
}


def get_candidate_profile() -> Dict[str, Any]:
    """Returns candidate profile, merging default values with any saved overrides."""
    profile = dict(DEFAULT_CANDIDATE_PROFILE)
    if PROFILE_STORE_PATH.exists():
        try:
            with open(PROFILE_STORE_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
                if isinstance(saved, dict):
                    profile.update(saved)
        except Exception as e:
            logger.warning(f"Could not load custom candidate profile from {PROFILE_STORE_PATH}: {e}")
    return profile


def save_candidate_profile(updates: Dict[str, Any]) -> Dict[str, Any]:
    """Persists candidate profile overrides to data/candidate_profile.json."""
    current = get_candidate_profile()
    current.update(updates)

    PROFILE_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PROFILE_STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2, ensure_ascii=False)

    return current


def classify_question_intent(question: str) -> str:
    """Classifies the category of a screening question."""
    q = question.lower().strip()

    if any(k in q for k in ["why do you want", "why this company", "why join", "why work at", "why us", "interest in"]):
        return "why_company"
    if any(k in q for k in ["notice period", "how soon", "when can you start", "available to join", "start date", "when can you join", "can you join", "joining date", "earliest start"]):
        return "notice_period"
    if any(k in q for k in ["authorized", "work authorization", "visa", "sponsorship", "legally"]):
        return "work_auth"
    if any(k in q for k in ["relocate", "relocation", "willing to move", "open to relocating"]):
        return "relocation"
    if any(k in q for k in ["salary", "compensation", "expected ctc", "current ctc", "remuneration"]):
        return "salary"
    if any(k in q for k in ["years of experience", "experience with", "tell us about a project", "challenging problem", "technical background"]):
        return "experience"
    if any(k in q for k in ["strength", "weakness", "accomplishment", "achievement", "proud of"]):
        return "strengths"

    return "general"


def generate_heuristic_screening_answer(
    question: str,
    company: str = "Company",
    role: str = "Software Engineer",
    intent: Optional[str] = None,
) -> str:
    """Generates an immediate high-quality response based on candidate profile."""
    company_clean = company.strip() or "your team"
    role_clean = role.strip() or "Software Engineer"
    category = intent or classify_question_intent(question)

    if category == "why_company":
        return (
            f"I am eager to join {company_clean} as a {role_clean} because of your focus on building high-scale, reliable systems. "
            f"With hands-on experience architecting distributed services in Java, Spring Boot, and event-driven pipelines with Kafka, "
            f"I am passionate about solving challenging engineering bottlenecks and delivering high-quality, impact-driven software."
        )

    if category == "notice_period":
        return "I am available immediately with 0 days notice period and can start as soon as an offer is finalized."

    if category == "work_auth":
        return "Yes, I am a citizen of India, legally authorized to work, and do not require any visa sponsorship."

    if category == "relocation":
        return f"Yes, I am 100% open and enthusiastic about relocating to join {company_clean} on-site or in a hybrid setting."

    if category == "salary":
        return "I am open to industry standard compensation for this role, aligned with the company's established pay scale and internal equity for entry-level/associate software engineers."

    if category == "experience":
        return (
            f"I have practical engineering experience developing backend and full-stack solutions. "
            f"Notably, I designed a Distributed Order Processing System using Java, Spring Boot, Kafka, and Redis to process asynchronous workloads, "
            f"as well as a full-stack MERN application (Skill-Bridge) with secure JWT authentication and role-based access. "
            f"I am comfortable owning features end-to-end and following rigorous engineering standards."
        )

    if category == "strengths":
        return (
            "My key strengths are deep curiosity for scalable systems architecture, rapid self-directed learning, "
            "and writing clean, well-tested code with modern tools like Docker and CI/CD pipelines."
        )

    # General fallback
    return (
        f"As a software engineer proficient in Java, Spring Boot, and scalable system design, "
        f"I am excited to bring my technical skills and proactive problem-solving mindset to {company_clean}. "
        f"I learn rapidly, communicate transparently, and take pride in shipping robust solutions."
    )


def answer_screening_question(
    question: str,
    company: str = "Company",
    role: str = "Software Engineer",
    jd_text: str = "",
) -> Dict[str, Any]:
    """
    Answers an ATS screening question.
    Attempts Groq LLM generation if configured, otherwise falls back instantly to heuristic templates.
    """
    question = question.strip()
    if not question:
        return {
            "answer": "",
            "intent": "unknown",
            "source": "empty",
        }

    intent = classify_question_intent(question)

    # If it's a simple factual question, heuristic is cleaner and 0 latency
    if intent in ("notice_period", "work_auth", "relocation", "salary"):
        return {
            "answer": generate_heuristic_screening_answer(question, company, role, intent),
            "intent": intent,
            "source": "heuristic",
        }

    # For open-ended questions, try Groq if key is present
    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    if groq_key:
        try:
            import urllib.request
            import urllib.error

            prompt = (
                f"You are Chinmay Maheshwari, a final-year B.Tech IT student at JECRC, graduating in 2027. "
                f"Your core stack is Java, Spring Boot, Kafka, Docker, and React.js. "
                f"You built a Distributed Order Processing System (Java/Spring Boot/Kafka/Redis) and Skill-Bridge (MERN). "
                f"You are applying to {company} for the role '{role}'.\n\n"
                f"Job description excerpt:\n{jd_text[:400] if jd_text else 'Software engineering position.'}\n\n"
                f"Answer the following job application screening question directly in first person. "
                f"Keep your response concise (2-4 sentences max), confident, and professional. No fluff or preamble:\n"
                f"Question: \"{question}\""
            )

            payload = json.dumps({
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": "You write concise, authentic, high-impact job screening answers."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 180,
            }).encode("utf-8")

            req = urllib.request.Request(
                "https://api.groq.com/openai/v1/chat/completions",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {groq_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                res_data = json.loads(resp.read().decode("utf-8"))
                content = res_data["choices"][0]["message"]["content"].strip()
                if content:
                    return {
                        "answer": content,
                        "intent": intent,
                        "source": "groq",
                    }
        except Exception as e:
            logger.warning(f"Groq screening answer generation failed: {e}. Falling back to heuristics.")

    # Heuristic fallback
    return {
        "answer": generate_heuristic_screening_answer(question, company, role, intent),
        "intent": intent,
        "source": "heuristic",
    }
