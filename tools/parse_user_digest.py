#!/usr/bin/env python3
"""Evaluate, filter, score, and rank jobs from user's pasted digest."""

import json
import os
import re
import sys
from pathlib import Path

# Ensure project root is in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from gcc_job_radar.clients.acciojob import parse_acciojob_digest_table
from gcc_job_radar.filters import matches_target_title
from gcc_job_radar.presentation import evaluate_job_relevance
from gcc_job_radar.link_resolver import build_direct_careers_search_url, build_direct_search_url
from gcc_job_radar.company_tracker import is_known_company, register_hiring_company
from gcc_job_radar.db import get_applied_and_dismissed_companies

def run(raw_text: str):
    parsed = parse_acciojob_digest_table(raw_text)
    print(f"Total raw items parsed: {len(parsed)}")

    applied_comps, dismissed_comps = get_applied_and_dismissed_companies()

    # Deduplicate by (company, title)
    unique_jobs = {}
    for p in parsed:
        c = p.get("company", "").strip()
        t = p.get("title", "").strip()
        key = (c.lower(), t.lower())
        if key not in unique_jobs:
            unique_jobs[key] = p

    print(f"Unique jobs count: {len(unique_jobs)}")

    qualified = []
    filtered_out = []

    for key, p in unique_jobs.items():
        comp = p.get("company", "").strip()
        title = p.get("title", "").strip()
        s_raw = p.get("skills") or []
        if isinstance(s_raw, str):
            skills = [s.strip() for s in s_raw.split(",") if s.strip()]
        else:
            skills = list(s_raw)
        snippet = " ".join(skills) + " " + title
        source = p.get("source_platform") or "Direct"
        date_str = p.get("date_str") or "Recent"

        # Check if non-tech / disqualified title
        title_lower = title.lower()
        non_tech_words = [
            "data analytics", "data analyst", "business analyst", "operations",
            "operations performance", "risk & compliance", "mis", "business intelligence",
            "logistics", "p&o reporting", "junior research analyst", "engagement analyst",
            "application support", "technical support", "fast track placement"
        ]
        
        is_non_tech = any(w in title_lower for w in non_tech_words) or ("analyst" in title_lower and "software" not in title_lower and "developer" not in title_lower)

        # Check if already applied or dismissed
        status_note = ""
        if comp.lower() in applied_comps:
            status_note = "ALREADY APPLIED"
        elif comp.lower() in dismissed_comps:
            status_note = "DISMISSED"

        # Calculate tech relevance score
        score, matched_skills, why = evaluate_job_relevance(
            title=title,
            description=snippet,
        )

        apply_link = p.get("apply_url")
        if not apply_link or "google.com/search" in apply_link:
            apply_link = build_direct_careers_search_url(comp, title)

        item = {
            "company": comp,
            "title": title,
            "skills": skills,
            "date": date_str,
            "source": source,
            "score": score,
            "reasons": matched_skills,
            "why": why,
            "status_note": status_note,
            "apply_url": apply_link,
            "is_non_tech": is_non_tech,
        }

        if is_non_tech:
            filtered_out.append(item)
        else:
            qualified.append(item)

    # Sort qualified jobs by score descending
    qualified.sort(key=lambda x: (x["score"], len(x["skills"])), reverse=True)

    print("\n" + "="*80)
    print(f"QUALIFIED TECH ROLES: {len(qualified)}")
    print("="*80)
    for q in qualified:
        st = f" [{q['status_note']}]" if q['status_note'] else ""
        print(f"[{q['score']} pts] {q['company']} — {q['title']}{st}")
        print(f"   Skills: {', '.join(q['skills'])}")
        print(f"   Why: {' • '.join(q['reasons'])}")
        print(f"   Apply: {q['apply_url']}")
        print()

    print("\n" + "="*80)
    print(f"FILTERED OUT (NON-TECH / ANALYST / OPS): {len(filtered_out)}")
    print("="*80)
    for f in filtered_out:
        print(f"- {f['company']} — {f['title']} (Skills: {', '.join(f['skills'])})")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            raw = f.read()
    elif not sys.stdin.isatty():
        raw = sys.stdin.read()
    else:
        with open("tools/user_raw_jobs.txt", "r", encoding="utf-8") as f:
            raw = f.read()
    run(raw)
