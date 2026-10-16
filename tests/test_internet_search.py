"""Unit tests for gcc_job_radar/internet_search.py."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from gcc_job_radar.db import get_latest_jobs, init_db
from gcc_job_radar.internet_search import (
    _extract_company_and_title,
    format_internet_search_html,
    search_internet_jobs,
)
from gcc_job_radar.models import ATSProvider, JobPosting


def test_extract_company_and_title() -> None:
    """Verify company and job title extraction from URLs and snippets."""
    # Greenhouse
    comp, title = _extract_company_and_title(
        raw_title="Software Engineer - Cloud Infrastructure - Greenhouse",
        link="https://boards.greenhouse.io/stripe/jobs/123456",
    )
    assert comp == "Stripe"
    assert "Software Engineer" in title

    # Lever
    comp, title = _extract_company_and_title(
        raw_title="Junior Backend Developer (Java) - Lever",
        link="https://jobs.lever.co/netflix/abc-xyz",
    )
    assert comp == "Netflix"
    assert "Junior Backend Developer" in title

    # LinkedIn
    comp, title = _extract_company_and_title(
        raw_title="Google hiring Software Engineer in Bengaluru",
        link="https://in.linkedin.com/jobs/view/software-engineer-at-google-12345678",
    )
    assert comp == "Google"
    assert "Software Engineer" in title

    # Role at Company format
    comp, title = _extract_company_and_title(
        raw_title="Associate Java Engineer at Uber - Bengaluru",
        link="https://example.com/job/1",
    )
    assert comp == "Uber"
    assert "Associate Java Engineer" in title


def test_format_internet_search_html() -> None:
    """Verify HTML card formatting and escaping."""
    job = JobPosting(
        id="test_search_job_1",
        numeric_id=42,
        company="Razorpay",
        title="Software Engineer 1 (Backend)",
        location="Bengaluru, India",
        apply_url="https://jobs.lever.co/razorpay/123",
        provider=ATSProvider.INTERNET_SEARCH,
        relevance_score=85,
        why="Strong match for Java/Spring Boot & RESTful APIs",
    )

    html_out = format_internet_search_html("Java Developer", "Bangalore", [job])
    assert "Live Internet Search: Java Developer" in html_out
    assert "#42 Razorpay" in html_out
    assert "[85 pts]" in html_out
    assert "https://jobs.lever.co/razorpay/123" in html_out
    assert "/tailor &lt;id&gt;" in html_out or "/tailor <id>" in html_out


@pytest.mark.asyncio
async def test_search_internet_jobs_serper_mock(tmp_path: Path) -> None:
    """Verify search_internet_jobs queries Serper, filters, scores and saves to DB."""
    test_db = tmp_path / "test_search.db"
    init_db(test_db)

    mock_serper_response = {
        "organic": [
            {
                "title": "Software Engineer (Java/Spring) - Greenhouse",
                "link": "https://boards.greenhouse.io/datadog/jobs/987654",
                "snippet": "We are seeking a junior backend engineer skilled in Java, Spring Boot, and Docker.",
            },
            {
                "title": "Senior Staff Architect 15+ years - Lever",
                "link": "https://jobs.lever.co/oldco/jobs/111",
                "snippet": "Requires minimum 15 years of industry leadership.",
            },
        ]
    }

    with patch("gcc_job_radar.internet_search.os.getenv", return_value="fake_serper_key"), \
         patch("gcc_job_radar.internet_search._search_serper_ats", new_callable=AsyncMock) as mock_serper:
        mock_serper.return_value = [
            {
                "title": "Software Engineer (Java/Spring)",
                "company": "Datadog",
                "apply_url": "https://boards.greenhouse.io/datadog/jobs/987654",
                "snippet": "We are seeking a junior backend engineer skilled in Java, Spring Boot, and Docker.",
                "source": "google_ats",
            },
            {
                "title": "Senior Staff Architect 15+ years",
                "company": "OldCo",
                "apply_url": "https://jobs.lever.co/oldco/jobs/111",
                "snippet": "Requires minimum 15 years of industry leadership.",
                "source": "google_ats",
            },
        ]

        results = await search_internet_jobs(
            query="Java Developer",
            location="Bangalore",
            limit=5,
            db_path=test_db,
        )

        assert len(results) >= 1
        found_job = next((j for j in results if j.company == "Datadog"), None)
        assert found_job is not None
        assert found_job.provider == ATSProvider.INTERNET_SEARCH
        assert found_job.relevance_score is not None
        assert found_job.relevance_score >= 40
        assert found_job.numeric_id is not None

        # Verify persisted into DB
        db_jobs = get_latest_jobs(status="ALL", db_path=test_db)
        assert any(j["company"] == "Datadog" for j in db_jobs)


@pytest.mark.asyncio
async def test_search_rejects_non_tech_and_handles_it_query(tmp_path: Path) -> None:
    """Verify non-tech roles (copywriter, writer, assistant) are suppressed and word boundary matching works."""
    from gcc_job_radar.internet_search import _matches_query_terms, SEARCH_STOP_WORDS

    # 1. Whole-word matching: 'it' must not match 'writer' or 'copywriter'
    assert not _matches_query_terms(["it"], "freelance writer writing")
    assert not _matches_query_terms(["it"], "freelance copywriter")
    assert not _matches_query_terms(["it"], "remote office assistant")
    assert _matches_query_terms(["it"], "it consultant technology")
    assert "it" in SEARCH_STOP_WORDS

    test_db = tmp_path / "test_non_tech.db"
    init_db(test_db)

    with patch("gcc_job_radar.internet_search.os.getenv", return_value="fake_serper_key"), \
         patch("gcc_job_radar.internet_search._search_serper_ats", new_callable=AsyncMock) as mock_serper, \
         patch("gcc_job_radar.internet_search._search_remotive_jobs", new_callable=AsyncMock) as mock_remotive, \
         patch("gcc_job_radar.internet_search._search_arbeitnow_jobs", new_callable=AsyncMock) as mock_arbeitnow:

        mock_serper.return_value = []
        # Simulate Remotive returning non-tech items
        mock_remotive.return_value = [
            {"title": "Freelance Writer", "company": "IAPWE", "apply_url": "https://remotive.com/1", "snippet": "Writing"},
            {"title": "Freelance Copywriter", "company": "Coalition", "apply_url": "https://remotive.com/2", "snippet": "Copy"},
            {"title": "Remote Office Assistant", "company": "Coalition", "apply_url": "https://remotive.com/3", "snippet": "Assistant"},
            {"title": "IT Consultant", "company": "ThoughtWorks", "apply_url": "https://thoughtworks.com/4", "snippet": "IT consulting"},
        ]
        mock_arbeitnow.return_value = []

        results = await search_internet_jobs(
            query="it consultant at greenhouse",
            location="India",
            limit=5,
            db_path=test_db,
        )

        # Non-tech roles must be completely filtered out
        titles = [j.title for j in results]
        assert "Freelance Writer" not in titles
        assert "Freelance Copywriter" not in titles
        assert "Remote Office Assistant" not in titles
        # Valid IT Consultant should remain
        assert "IT Consultant" in titles

