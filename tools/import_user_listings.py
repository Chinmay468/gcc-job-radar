#!/usr/bin/env python3
"""
import_user_listings.py — Ingest user-provided job listings into gcc_jobs.db.
"""

import re
import sqlite3
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from gcc_job_radar.db import get_db_path, init_db

LISTINGS = [
    {
        "company": "Qualys, Inc.",
        "title": "Software Engineer",
        "skills": "React.js, Java, SpringBoot",
        "date": "2026-09-29",
        "source": "LinkedIn",
        "score": 95,
        "is_tier1": True,
    },
    {
        "company": "Amazon India",
        "title": "SDE-I",
        "skills": "Java, Algorithm, DataStructures",
        "date": "2026-09-29",
        "source": "Company Career Page",
        "score": 95,
        "is_tier1": True,
    },
    {
        "company": "Hewlett Packard Enterprise",
        "title": "Graduate Software Engineer",
        "skills": "Java, JavaScript",
        "date": "2026-09-29",
        "source": "LinkedIn",
        "score": 92,
        "is_tier1": True,
    },
    {
        "company": "NEC Software Solutions",
        "title": "Software Engineer - Full Stack Developer",
        "skills": "Java, React",
        "date": "2026-09-29",
        "source": "LinkedIn",
        "score": 92,
        "is_tier1": True,
    },
    {
        "company": "Signzy",
        "title": "Java Software Engineer",
        "skills": "SpringBoot, Java, PostgreSQL, MySQL",
        "date": "2026-09-29",
        "source": "LinkedIn",
        "score": 92,
        "is_tier1": True,
    },
    {
        "company": "MUFG",
        "title": "Full Stack Java Developer",
        "skills": "Java, SQL, JavaScript",
        "date": "2026-09-29",
        "source": "LinkedIn",
        "score": 90,
        "is_tier1": True,
    },
    {
        "company": "Texas Instruments",
        "title": "Application Developer",
        "skills": "Java, JavaScript, HTML, J2EE, SQL",
        "date": "2026-09-29",
        "source": "Company Career Page",
        "score": 90,
        "is_tier1": True,
    },
    {
        "company": "Creospan Private Limited",
        "title": "Java Backend Developer",
        "skills": "Java, SpringBoot, SQL",
        "date": "2026-09-29",
        "source": "LinkedIn",
        "score": 88,
        "is_tier1": True,
    },
    {
        "company": "Keploy",
        "title": "SDE Intern",
        "skills": "OOP, API Testing",
        "date": "2026-09-29",
        "source": "Company Career Page",
        "score": 88,
        "is_tier1": False,
    },
    {
        "company": "Langslide | Auroscale",
        "title": "Full Stack Engineer Intern",
        "skills": "React.js, Node.js, JavaScript, HTML, CSS",
        "date": "2026-09-29",
        "source": "Wellfound",
        "score": 88,
        "is_tier1": False,
    },
    {
        "company": "Apex Solutions",
        "title": "Full Stack Developer – AI-Assisted Development",
        "skills": "Java, JavaScript, AI-Assisted",
        "date": "2026-09-29",
        "source": "LinkedIn",
        "score": 88,
        "is_tier1": False,
    },
    {
        "company": "Hitbullseye",
        "title": "Software Engineer Intern",
        "skills": "Java, DataStructures, Algorithms",
        "date": "2026-09-29",
        "source": "LinkedIn",
        "score": 85,
        "is_tier1": False,
    },
    {
        "company": "Mastek",
        "title": "Full Stack Engineer",
        "skills": "Node.js, Java",
        "date": "2026-09-29",
        "source": "LinkedIn",
        "score": 85,
        "is_tier1": False,
    },
    {
        "company": "SysCloud",
        "title": "Full Stack Engineer",
        "skills": "Node.js, JavaScript, Python, PostgreSQL, React.js",
        "date": "2026-09-29",
        "source": "LinkedIn",
        "score": 85,
        "is_tier1": False,
    },
    {
        "company": "Netopsys AI Pvt Ltd",
        "title": "Full Stack Software Engineer",
        "skills": "MERN, React.js, Node.js, JavaScript, PostgreSQL, MySQL",
        "date": "2026-09-29",
        "source": "LinkedIn",
        "score": 85,
        "is_tier1": False,
    },
    {
        "company": "Noventiq India",
        "title": "Software Developer",
        "skills": "HTML5, JavaScript, React.js, Java",
        "date": "2026-09-29",
        "source": "LinkedIn",
        "score": 82,
        "is_tier1": False,
    },
    {
        "company": "Cohere Health",
        "title": "Associate Software Engineer",
        "skills": "CoreJava",
        "date": "2026-09-29",
        "source": "LinkedIn",
        "score": 82,
        "is_tier1": False,
    },
    {
        "company": "Cognizant",
        "title": "Java Developer",
        "skills": "CoreJava",
        "date": "2026-09-29",
        "source": "LinkedIn",
        "score": 80,
        "is_tier1": False,
    },
    {
        "company": "Stackbinary",
        "title": "Forward-Deployed Engineer Intern",
        "skills": "Java, JavaScript, SQL",
        "date": "2026-09-29",
        "source": "LinkedIn",
        "score": 80,
        "is_tier1": False,
    },
    {
        "company": "Gritsa Technologies",
        "title": "Full Stack Development - Internship",
        "skills": "HTML, JavaScript, Node.js, PostgreSQL, React",
        "date": "2026-09-29",
        "source": "LinkedIn",
        "score": 80,
        "is_tier1": False,
    },
    {
        "company": "Kayla - apps & ai",
        "title": "Back End Developer",
        "skills": "Node.js, PostgreSQL, MongoDB, MySQL",
        "date": "2026-09-29",
        "source": "LinkedIn",
        "score": 80,
        "is_tier1": False,
    },
    {
        "company": "Graystone Capital",
        "title": "Back End Developer",
        "skills": "Node.js, Java",
        "date": "2026-09-29",
        "source": "LinkedIn",
        "score": 80,
        "is_tier1": False,
    },
    {
        "company": "Lepton Software",
        "title": "Full Stack Engineer",
        "skills": "Node.js, ExpressJS, JavaScript, HTML5",
        "date": "2026-09-29",
        "source": "LinkedIn",
        "score": 78,
        "is_tier1": False,
    },
    {
        "company": "Ucanly",
        "title": "Full Stack Developer",
        "skills": "HTML, CSS, JavaScript, React.js, Node.js",
        "date": "2026-09-29",
        "source": "LinkedIn",
        "score": 75,
        "is_tier1": False,
    },
    {
        "company": "Techindo Systems",
        "title": "Full Stack Developer",
        "skills": "JavaScript, HTML, CSS, React",
        "date": "2026-09-29",
        "source": "LinkedIn",
        "score": 75,
        "is_tier1": False,
    },
    {
        "company": "Capturous Infotech",
        "title": "Web Developer Intern",
        "skills": "HTML, CSS, JavaScript",
        "date": "2026-09-29",
        "source": "LinkedIn",
        "score": 75,
        "is_tier1": False,
    },
    {
        "company": "Seva Enterprise LLP",
        "title": "IT Intern",
        "skills": "SDLC, Software Engineering",
        "date": "2026-09-29",
        "source": "Company Career Page",
        "score": 72,
        "is_tier1": False,
    },
    {
        "company": "Nathaniel School Of Music - India",
        "title": "Full Stack Development",
        "skills": "JavaScript, Node.js, React",
        "date": "2026-09-29",
        "source": "Internshala",
        "score": 72,
        "is_tier1": False,
    },
    {
        "company": "QualityAI",
        "title": "QA Automation Engineer (Technical)",
        "skills": "Java, SQL, Automation Testing",
        "date": "2026-09-29",
        "source": "LinkedIn",
        "score": 70,
        "is_tier1": False,
    },
]


def generate_apply_url(company: str, title: str, source: str) -> str:
    query = f"{company} {title} jobs"
    if "linkedin" in source.lower():
        encoded = urllib.parse.quote(f"{company} {title}")
        return f"https://www.linkedin.com/jobs/search/?keywords={encoded}&location=India"
    elif "wellfound" in source.lower() or "angellist" in source.lower():
        encoded = urllib.parse.quote(f"{company} {title}")
        return f"https://wellfound.com/jobs?q={encoded}"
    elif "internshala" in source.lower():
        encoded = urllib.parse.quote(f"{company} {title}")
        return f"https://internshala.com/internships/keywords-{encoded}/"
    else:
        encoded = urllib.parse.quote_plus(f"{company} {title} careers India")
        return f"https://www.google.com/search?q={encoded}"


def main():
    db_path = get_db_path()
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    inserted = 0
    updated = 0

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    for item in LISTINGS:
        comp = item["company"].strip()
        tit = item["title"].strip()
        skills = item["skills"].strip()
        score = item["score"]
        src = item["source"]
        apply_url = generate_apply_url(comp, tit, src)

        comp_slug = re.sub(r"[^a-zA-Z0-9]+", "_", comp.lower()).strip("_")
        tit_slug = re.sub(r"[^a-zA-Z0-9]+", "_", tit.lower())[:25].strip("_")
        job_id = f"lead_{comp_slug}_{tit_slug}"

        provider_norm = re.sub(r"[^a-zA-Z0-9_]+", "_", src.lower()).strip("_") or "manual"
        notes = f"Skills: {skills} | Source: {src} ({item['date']})"

        # Check if already exists by company & title
        cur.execute(
            "SELECT id, status FROM seen_jobs WHERE lower(trim(company)) = lower(trim(?)) AND lower(trim(title)) = lower(trim(?))",
            (comp, tit),
        )
        existing = cur.fetchone()

        if existing:
            # Preserve applied/interviewing status if already set
            existing_id, existing_status = existing
            if existing_status not in ("APPLIED", "INTERVIEWING", "DISMISSED"):
                cur.execute(
                    """
                    UPDATE seen_jobs SET
                        apply_url = COALESCE(apply_url, ?),
                        notes = ?,
                        relevance_score = ?,
                        last_seen_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (apply_url, notes, score, existing_id),
                )
                updated += 1
        else:
            cur.execute(
                """
                INSERT INTO seen_jobs (
                    id, company, title, location, apply_url, provider, published_date,
                    is_active, is_remote, status, notes, relevance_score, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0, 'NEW', ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    job_id, comp, tit, "India", apply_url, provider_norm, item["date"],
                    notes, score,
                ),
            )
            inserted += 1

    conn.commit()
    conn.close()

    print(f"[OK] Ingestion complete: {inserted} inserted, {updated} updated into {db_path.name}")


if __name__ == "__main__":
    main()
