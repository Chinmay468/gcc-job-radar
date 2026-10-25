#!/usr/bin/env python3
"""
sync_knowledge_base.py — Centralized Knowledge Base Synchronizer for GCC Job Radar.

Maintains `builder/knowledge_base.json` as the authoritative single source of truth
for candidate profile, technical skills, and project portfolio.
Provides:
  1. Synchronized export to `builder/projects.json`, `builder/github_dossier.md`, and `data/candidate_profile.json`.
  2. Optional GitHub API fetching to inspect repo activity, description, stars, and README changes.
  3. Safe programmatic API for the Extension Bridge and CLI usage.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional

logger = logging.getLogger("sync_knowledge_base")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BUILDER_DIR = PROJECT_ROOT / "builder"
KB_PATH = BUILDER_DIR / "knowledge_base.json"
PROJECTS_JSON_PATH = BUILDER_DIR / "projects.json"
DOSSIER_MD_PATH = BUILDER_DIR / "github_dossier.md"
PROFILE_JSON_PATH = PROJECT_ROOT / "data" / "candidate_profile.json"


def load_knowledge_base(path: Optional[Path] = None) -> Dict[str, Any]:
    """Loads knowledge_base.json, returning empty structure if not found."""
    target = path or KB_PATH
    if not target.exists():
        raise FileNotFoundError(f"Knowledge base file not found at: {target}")
    with open(target, "r", encoding="utf-8") as f:
        return json.load(f)


def save_knowledge_base(data: Dict[str, Any], path: Optional[Path] = None) -> None:
    """Saves updated knowledge base JSON with clean 2-space indentation."""
    target = path or KB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    logger.info(f"Saved knowledge base to {target}")


def export_projects_json(kb: Dict[str, Any], out_path: Optional[Path] = None) -> Path:
    """Exports projects array from knowledge base to builder/projects.json."""
    target = out_path or PROJECTS_JSON_PATH
    projects = kb.get("projects", [])
    clean_projects = []
    for p in projects:
        clean_p = {
            "id": p.get("id"),
            "title": p.get("title", ""),
            "github_url": p.get("github_url", ""),
            "tech_stack": p.get("tech_stack", ""),
            "keywords": p.get("keywords", []),
            "bullets": p.get("bullets", []),
        }
        clean_projects.append(clean_p)

    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        json.dump(clean_projects, f, indent=2, ensure_ascii=False)
        f.write("\n")
    logger.info(f"Exported {len(clean_projects)} projects to {target}")
    return target


def export_github_dossier(kb: Dict[str, Any], out_path: Optional[Path] = None) -> Path:
    """Generates builder/github_dossier.md from knowledge base projects and candidate facts."""
    target = out_path or DOSSIER_MD_PATH
    candidate = kb.get("candidate", {})
    name = candidate.get("name", "Chinmay Maheshwari")
    gh_handle = candidate.get("links", {}).get("github_primary", "").rstrip("/").split("/")[-1] or "Chinmay0608"

    lines = [
        f"# VERIFIED GITHUB PROJECT DOSSIER — {name} (@{gh_handle})",
        "",
        "The following technical facts, features, dependencies, and architectures are extracted directly",
        "from the candidate's real GitHub repositories and verified knowledge base.",
        "When tailoring or refining resume points, draw ONLY from these verified facts.",
        "NEVER invent outside systems, fake libraries, or unsupported architectures.",
        "",
    ]

    for proj in kb.get("projects", []):
        p_id = proj.get("id", "")
        title = proj.get("title", "")
        gh_url = proj.get("github_url", "")
        live_url = proj.get("live_url", "")
        stack = proj.get("tech_stack", "")
        bullets = proj.get("bullets", [])
        arch = proj.get("architecture_highlights", [])

        lines.append(f"## Project: {p_id}")
        lines.append(f"- **Title**: {title}")
        lines.append(f"- **Repository**: {gh_url}")
        if live_url:
            lines.append(f"- **Live Demo**: {live_url}")
        lines.append(f"- **Primary Tech**: {stack}")
        lines.append("")

        if arch:
            lines.append("**Key Architectural Highlights:**")
            for a in arch:
                lines.append(f"- {a}")
            lines.append("")

        if bullets:
            lines.append("**Verified ATS Impact Bullets:**")
            for b in bullets:
                lines.append(f"- {b}")
            lines.append("")
        lines.append("---")
        lines.append("")

    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).strip() + "\n")
    logger.info(f"Exported GitHub dossier to {target}")
    return target


def export_candidate_profile(kb: Dict[str, Any], out_path: Optional[Path] = None) -> Path:
    """Updates data/candidate_profile.json from candidate section in knowledge base."""
    target = out_path or PROFILE_JSON_PATH
    candidate = kb.get("candidate", {})
    edu = candidate.get("education", {})
    links = candidate.get("links", {})
    name_parts = candidate.get("name", "Chinmay Maheshwari").split()
    first_name = name_parts[0] if name_parts else "Chinmay"
    last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else "Maheshwari"

    profile_data = {
        "first_name": first_name,
        "last_name": last_name,
        "full_name": candidate.get("name", "Chinmay Maheshwari"),
        "email": candidate.get("email", "chinmaymaheshwari.it27@gmail.com"),
        "phone": candidate.get("phone", "+91 9460449962"),
        "phone_national": candidate.get("phone_national", "9460449962"),
        "city": candidate.get("city", "Jaipur"),
        "state": candidate.get("state", "Rajasthan"),
        "country": candidate.get("country", "India"),
        "location": candidate.get("location", "Jaipur, India"),
        "address": f"{candidate.get('city', 'Jaipur')}, {candidate.get('state', 'Rajasthan')}, {candidate.get('country', 'India')}",
        "linkedin": links.get("linkedin", "https://www.linkedin.com/in/chinmay8064/"),
        "github": links.get("github_primary", "https://github.com/Chinmay0608"),
        "portfolio": links.get("github_secondary", "https://github.com/Chinmay468"),
        "degree": edu.get("degree", "B.Tech in Information Technology"),
        "degree_type": "Bachelor's Degree",
        "field_of_study": "Information Technology",
        "school": edu.get("institution", "Jaipur Engineering College and Research Centre"),
        "university": edu.get("institution", "Jaipur Engineering College and Research Centre"),
        "graduation_year": str(edu.get("graduation_batch", "2027")),
        "graduation_month": "June",
        "gpa": str(edu.get("cgpa", "8.8")),
        "gpa_max": str(edu.get("cgpa_scale", "10.0")),
        "current_company": "Self-Employed / Student",
        "current_title": "Software Engineer Intern",
        "total_experience_years": "1",
        "notice_period": "Immediate",
        "notice_period_days": "0",
        "authorized_in_country": "Yes",
        "visa_sponsorship_needed": "No",
        "willing_to_relocate": "Yes",
        "skills_summary": ", ".join(kb.get("skills", {}).get("languages", []) + kb.get("skills", {}).get("backend", [])[:4]),
    }

    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        json.dump(profile_data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    logger.info(f"Exported candidate profile to {target}")
    return target


def fetch_github_repo_metadata(owner: str, repo: str, token: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Fetches public repo metadata from GitHub API."""
    import httpx
    url = f"https://api.github.com/repos/{owner}/{repo}"
    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "gcc-job-radar-kb-sync"}
    if token:
        headers["Authorization"] = f"token {token}"
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code == 200:
                return resp.json()
            logger.debug(f"GitHub API {owner}/{repo} returned status {resp.status_code}")
    except Exception as e:
        logger.warning(f"Failed to fetch GitHub metadata for {owner}/{repo}: {e}")
    return None


def sync_github_repos(kb: Dict[str, Any], token: Optional[str] = None) -> int:
    """Checks and enriches projects in knowledge base with live GitHub metadata."""
    auth_token = token or os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    updated_count = 0
    projects = kb.get("projects", [])

    for p in projects:
        gh_url = p.get("github_url", "")
        m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/?", gh_url)
        if not m:
            continue
        owner, repo = m.group(1), m.group(2)
        meta = fetch_github_repo_metadata(owner, repo, token=auth_token)
        if meta:
            p["github_stars"] = meta.get("stargazers_count", 0)
            p["github_description"] = meta.get("description", "")
            p["github_pushed_at"] = meta.get("pushed_at", "")
            p["github_default_branch"] = meta.get("default_branch", "main")
            updated_count += 1

    return updated_count


def sync_knowledge_base(fetch_github: bool = False, export_all: bool = True) -> Dict[str, Any]:
    """
    Main entry point for syncing the knowledge base.
    Optionally enriches from GitHub API and exports to projects.json, dossier, and profile.
    """
    kb = load_knowledge_base()
    gh_updated = 0
    if fetch_github:
        gh_updated = sync_github_repos(kb)
        if gh_updated > 0:
            save_knowledge_base(kb)

    if export_all:
        export_projects_json(kb)
        export_github_dossier(kb)
        export_candidate_profile(kb)

    return {
        "status": "success",
        "projects_count": len(kb.get("projects", [])),
        "github_repos_checked": gh_updated,
        "candidate": kb.get("candidate", {}).get("name", "Unknown"),
        "version": kb.get("version", "1.0.0"),
    }


def main():
    parser = argparse.ArgumentParser(description="Synchronize and export candidate knowledge base.")
    parser.add_argument("--fetch-github", action="store_true", help="Query GitHub API to enrich repository metadata")
    parser.add_argument("--export-only", action="store_true", help="Regenerate projects.json, dossier, and profile without querying GitHub")
    parser.add_argument("--status", action="store_true", help="Display summary of current knowledge base")

    args = parser.parse_args()

    if args.status:
        try:
            kb = load_knowledge_base()
            cand = kb.get("candidate", {})
            edu = cand.get("education", {})
            metrics = cand.get("metrics", {})
            print("=" * 60)
            print("CANDIDATE KNOWLEDGE BASE STATUS")
            print("=" * 60)
            print(f"Candidate:    {cand.get('name')} <{cand.get('email')}>")
            print(f"Education:    {edu.get('degree')} ({edu.get('graduation_years')}, CGPA: {edu.get('cgpa')})")
            print(f"LeetCode:     {metrics.get('leetcode_dsa_count')} solved")
            print(f"Projects ({len(kb.get('projects', []))}):")
            for idx, p in enumerate(kb.get("projects", []), 1):
                print(f"  {idx}. {p.get('title')} [{p.get('tech_stack')}]")
            print("=" * 60)
            return
        except Exception as e:
            sys.exit(f"Failed to load knowledge base: {e}")

    fetch_gh = args.fetch_github
    res = sync_knowledge_base(fetch_github=fetch_gh, export_all=True)
    print(f"[+] Knowledge base synchronized successfully: {res['projects_count']} projects active.")


if __name__ == "__main__":
    main()
