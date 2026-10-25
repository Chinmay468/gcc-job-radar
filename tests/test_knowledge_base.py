"""Unit tests for the Candidate Knowledge Base and Synchronizer."""

import json
from pathlib import Path
import subprocess
import pytest

from tools.sync_knowledge_base import (
    load_knowledge_base,
    save_knowledge_base,
    export_projects_json,
    export_github_dossier,
    export_candidate_profile,
    sync_knowledge_base,
)
from builder.resume_tailor import load_projects_pool


def test_load_knowledge_base():
    """Verify loading candidate knowledge base returns structured dictionary."""
    kb = load_knowledge_base()
    assert isinstance(kb, dict)
    assert "candidate" in kb
    assert "projects" in kb
    assert "skills" in kb
    assert kb["candidate"]["name"] == "Chinmay Maheshwari"
    assert len(kb["projects"]) >= 5


def test_export_projects_json(tmp_path: Path):
    """Verify export_projects_json generates valid projects array."""
    kb = load_knowledge_base()
    out_file = tmp_path / "projects_test.json"
    export_projects_json(kb, out_path=out_file)

    assert out_file.exists()
    with open(out_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, list)
    assert len(data) == len(kb["projects"])
    assert data[0]["id"] == "distributed-order-processing"


def test_export_github_dossier(tmp_path: Path):
    """Verify export_github_dossier produces formatted Markdown."""
    kb = load_knowledge_base()
    out_file = tmp_path / "dossier_test.md"
    export_github_dossier(kb, out_path=out_file)

    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert "VERIFIED GITHUB PROJECT DOSSIER" in content
    assert "distributed-order-processing" in content
    assert "MindVault" in content


def test_export_candidate_profile(tmp_path: Path):
    """Verify export_candidate_profile produces complete profile JSON."""
    kb = load_knowledge_base()
    out_file = tmp_path / "profile_test.json"
    export_candidate_profile(kb, out_path=out_file)

    assert out_file.exists()
    with open(out_file, "r", encoding="utf-8") as f:
        profile = json.load(f)
    assert profile["first_name"] == "Chinmay"
    assert profile["last_name"] == "Maheshwari"
    assert profile["graduation_year"] == "2027"
    assert profile["gpa"] == "8.8"


def test_resume_tailor_loads_knowledge_base_projects():
    """Verify resume_tailor.load_projects_pool reads from knowledge_base.json."""
    pool = load_projects_pool()
    assert isinstance(pool, list)
    project_ids = [p["id"] for p in pool]
    assert "distributed-order-processing" in project_ids
    assert "gcc-job-radar" in project_ids


def test_knowledge_base_is_git_ignored():
    """Verify git check-ignore confirms knowledge_base.json is strictly ignored and never tracked."""
    res = subprocess.run(
        ["git", "check-ignore", "-v", "builder/knowledge_base.json"],
        cwd=Path(__file__).resolve().parent.parent / "builder",
        capture_output=True,
        text=True,
    )
    # Inside builder directory, knowledge_base.json matches .gitignore
    res_builder = subprocess.run(
        ["git", "check-ignore", "-v", "knowledge_base.json"],
        cwd=Path(__file__).resolve().parent.parent / "builder",
        capture_output=True,
        text=True,
    )
    assert res_builder.returncode == 0
    assert "knowledge_base.json" in res_builder.stdout
