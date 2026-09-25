#!/usr/bin/env python3
"""
build_github_dossier.py — Harvests real codebase data from Chinmay0608's GitHub profile.

Fetches READMEs, package/pom dependencies, file structures, and descriptions from:
  - distributed-order-processing
  - job-portal-system
  - trao-ai-interview-prep-kit
  - portfolio

Saves the verified ground truth to `builder/github_dossier.json` and `builder/github_dossier.md`
so Groq AI is strictly grounded in real project details and never invents fake claims.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("github_dossier")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BUILDER_DIR = PROJECT_ROOT / "builder"
OUTPUT_JSON = BUILDER_DIR / "github_dossier.json"
OUTPUT_MD = BUILDER_DIR / "github_dossier.md"

GITHUB_USERNAME = "Chinmay0608"
KEY_REPOS = [
    "distributed-order-processing",
    "job-portal-system",
    "trao-ai-interview-prep-kit",
    "portfolio",
]


def github_api_get(url: str) -> dict | list | None:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "GCC-Job-Radar-Dossier-Builder/1.0",
            "Accept": "application/vnd.github.v3+json",
        },
    )
    # Support GITHUB_TOKEN if available in environment for higher rate limit
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        logger.warning(f"HTTP {e.code} for {url}: {e.reason}")
        return None
    except Exception as e:
        logger.warning(f"Error fetching {url}: {e}")
        return None


def fetch_file_content(repo: str, path: str) -> str | None:
    data = github_api_get(f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo}/contents/{path}")
    if data and isinstance(data, dict) and "content" in data:
        try:
            return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        except Exception:
            return None
    return None


def fetch_repo_tree(repo: str) -> list[str]:
    data = github_api_get(f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo}/git/trees/main?recursive=1")
    if not data:
        data = github_api_get(f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo}/git/trees/master?recursive=1")
    if data and isinstance(data, dict) and "tree" in data:
        # Return first 60 file paths
        return [item["path"] for item in data["tree"] if item.get("type") == "blob"][:60]
    return []


def build_dossier() -> dict:
    logger.info(f"Harvesting GitHub data for user: {GITHUB_USERNAME}...")
    user_data = github_api_get(f"https://api.github.com/users/{GITHUB_USERNAME}")
    user_repos = github_api_get(f"https://api.github.com/users/{GITHUB_USERNAME}/repos?sort=updated&per_page=30") or []

    dossier = {
        "user": {
            "username": GITHUB_USERNAME,
            "name": (user_data or {}).get("name", "Chinmay Maheshwari"),
            "bio": (user_data or {}).get("bio"),
            "public_repos_count": (user_data or {}).get("public_repos", len(user_repos)),
            "html_url": f"https://github.com/{GITHUB_USERNAME}",
        },
        "projects": {},
    }

    # Map repos by name
    repo_dict = {r["name"]: r for r in user_repos if isinstance(r, dict)}

    for repo_name in KEY_REPOS:
        meta = repo_dict.get(repo_name)
        if not meta:
            # Try fetching repo directly
            meta = github_api_get(f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo_name}")
            if not meta:
                continue

        logger.info(f"Processing repository: {repo_name}...")
        readme = (
            fetch_file_content(repo_name, "README.md")
            or fetch_file_content(repo_name, "readme.md")
            or ""
        )

        pom_xml = fetch_file_content(repo_name, "pom.xml")
        package_json_raw = (
            fetch_file_content(repo_name, "package.json")
            or fetch_file_content(repo_name, "server/package.json")
            or fetch_file_content(repo_name, "backend/package.json")
        )

        package_json = {}
        if package_json_raw:
            try:
                package_json = json.loads(package_json_raw)
            except Exception:
                pass

        file_tree = fetch_repo_tree(repo_name)

        # Parse key technical details
        tech_stack = []
        if meta.get("language"):
            tech_stack.append(meta["language"])

        dependencies = []
        if package_json:
            dependencies.extend(list(package_json.get("dependencies", {}).keys()))
        if pom_xml:
            # Extract artifactIds
            artifacts = re.findall(r"<artifactId>([^<]+)</artifactId>", pom_xml)
            for a in artifacts:
                if a not in ("spring-boot-starter-parent", "maven-compiler-plugin"):
                    dependencies.append(a)

        dossier["projects"][repo_name] = {
            "name": repo_name,
            "description": meta.get("description") or "",
            "homepage": meta.get("homepage") or "",
            "language": meta.get("language") or "",
            "html_url": meta.get("html_url") or f"https://github.com/{GITHUB_USERNAME}/{repo_name}",
            "dependencies": dependencies[:25],
            "key_files": file_tree[:25],
            "readme_summary": readme[:3500] if readme else "",
        }

    # Save JSON
    BUILDER_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(dossier, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved JSON dossier to: {OUTPUT_JSON}")

    # Generate Markdown summary for system prompt ingestion
    md_lines = [
        f"# VERIFIED GITHUB PROJECT DOSSIER — {dossier['user']['name']} (@{GITHUB_USERNAME})",
        "",
        "The following technical facts, features, dependencies, and architectures are extracted directly from the candidate's real GitHub repositories.",
        "When tailoring or refining resume points, USE ONLY these verified facts. NEVER invent outside systems or fake metrics.",
        "",
    ]

    for name, p in dossier["projects"].items():
        md_lines.append(f"## Project: {name}")
        if p["description"]:
            md_lines.append(f"- **Description**: {p['description']}")
        if p["homepage"]:
            md_lines.append(f"- **Live Demo**: {p['homepage']}")
        md_lines.append(f"- **Repository**: {p['html_url']}")
        md_lines.append(f"- **Primary Tech**: {p['language']}")
        if p["dependencies"]:
            md_lines.append(f"- **Verified Dependencies**: {', '.join(p['dependencies'][:15])}")
        if p["key_files"]:
            md_lines.append(f"- **Verified Modules/Files**: {', '.join(p['key_files'][:12])}")
        if p["readme_summary"]:
            md_lines.append("\n**Key Architecture & Code Details (from README):**")
            # Strip badges and extract clean text chunks
            clean_readme = re.sub(r"\[!\[.*?\]\(.*?\)\]\(.*?\)", "", p["readme_summary"])
            clean_readme = re.sub(r"!\[.*?\]\(.*?\)", "", clean_readme).strip()
            md_lines.append("```text")
            md_lines.append(clean_readme[:1800])
            md_lines.append("```")
        md_lines.append("\n" + "-" * 50 + "\n")

    md_content = "\n".join(md_lines)
    with open(OUTPUT_MD, "w", encoding="utf-8") as f:
        f.write(md_content)
    logger.info(f"Saved Markdown dossier to: {OUTPUT_MD}")

    return dossier


if __name__ == "__main__":
    build_dossier()
