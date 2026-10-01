"""Unit tests for gcc_job_radar/link_resolver.py and tools/check_links.py."""

from pathlib import Path
import sqlite3
from unittest.mock import patch
import urllib.parse

import pytest
from typer.testing import CliRunner

from gcc_job_radar.cli import app
from gcc_job_radar.db import (
    get_job_by_id,
    init_db,
    record_jobs,
    update_job_direct_search_url,
)
from gcc_job_radar.link_resolver import (
    build_direct_careers_search_url,
    build_direct_search_url,
    find_direct_ats_link_in_html,
    is_aggregator_url,
    is_direct_ats_url,
    is_glassdoor_url,
    is_job_legitimate,
    resolve_company_career_portal,
    resolve_effective_apply_url,
    resolve_job_link,
    unwrap_destination_url,
)
from gcc_job_radar.models import ATSProvider, JobPosting
from tools.check_links import resolve_links

runner = CliRunner()


def test_is_direct_ats_url() -> None:
    """Verify ATS domain detection for major platforms."""
    assert is_direct_ats_url("https://boards.greenhouse.io/stripe/jobs/123") is True
    assert is_direct_ats_url("https://job-boards.greenhouse.io/celonis/jobs/456") is True
    assert is_direct_ats_url("https://jobs.lever.co/netflix/abc-123") is True
    assert is_direct_ats_url("https://jobs.ashbyhq.com/linear/789") is True
    assert is_direct_ats_url("https://jobs.smartrecruiters.com/Acme/001") is True
    assert is_direct_ats_url("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite") is True
    assert is_direct_ats_url("https://www.glassdoor.com/job-listing/?jl=123") is False
    assert is_direct_ats_url("https://www.linkedin.com/jobs/view/456") is False
    assert is_direct_ats_url("") is False


def test_is_aggregator_url() -> None:
    """Verify aggregator domain identification."""
    assert is_aggregator_url("https://www.glassdoor.com/job-listing/?jl=1010236056374") is True
    assert is_aggregator_url("https://www.linkedin.com/jobs/view/4458273297/") is True
    assert is_aggregator_url("https://www.indeed.com/viewjob?jk=abcdef") is True
    assert is_aggregator_url("https://www.naukri.com/job-listings-12345") is True
    assert is_aggregator_url("https://boards.greenhouse.io/databricks/jobs/101") is False
    assert is_aggregator_url("") is False


def test_unwrap_destination_url() -> None:
    """Verify nested tracking and redirect parameter unwrapping."""
    # Standard single nested parameter
    target = "https://boards.greenhouse.io/stripe/jobs/99999"
    encoded = urllib.parse.quote(target)
    wrapper_url = f"https://www.glassdoor.com/partner/jobListing.htm?pos=101&url={encoded}"
    assert unwrap_destination_url(wrapper_url) == target

    # 'dest' parameter
    dest_url = f"https://tracking.aggregator.com/click?dest={encoded}"
    assert unwrap_destination_url(dest_url) == target

    # Double-nested redirect parameter
    double_encoded = urllib.parse.quote(encoded)
    nested_url = f"https://ad.network.com/track?redirect_url=https%3A%2F%2Fclick.mail.com%3Furl%3D{double_encoded}"
    assert unwrap_destination_url(nested_url) == target

    # Non-nested URL returns None
    assert unwrap_destination_url("https://boards.greenhouse.io/stripe/jobs/123") is None
    assert unwrap_destination_url("") is None


def test_find_direct_ats_link_in_html() -> None:
    """Verify extracting direct ATS hyperlinks from HTML alert card vicinity."""
    html_card = """
    <div class="job-card">
        <h3>Software Engineer</h3>
        <a href="https://boards.greenhouse.io/stripe/jobs/98765" class="apply-btn">Apply Directly</a>
    </div>
    """
    found = find_direct_ats_link_in_html(html_card)
    assert found == "https://boards.greenhouse.io/stripe/jobs/98765"

    lever_card = """
    <div><a href="https://jobs.lever.co/company/abc-role">View Posting</a></div>
    """
    assert find_direct_ats_link_in_html(lever_card) == "https://jobs.lever.co/company/abc-role"

    no_ats_html = "<div><a href='https://example.com/other'>Link</a></div>"
    assert find_direct_ats_link_in_html(no_ats_html) is None
    assert find_direct_ats_link_in_html("") is None


def test_build_direct_search_url() -> None:
    """Verify Google and DuckDuckGo ATS fallback search query construction."""
    google_url = build_direct_search_url("Goldman Sachs", "Analyst / SDE 1")
    assert google_url.startswith("https://www.google.com/search?q=")
    assert "Goldman+Sachs" in google_url
    assert "Analyst+%2F+SDE+1" in google_url
    assert "site%3Agreenhouse.io" in google_url
    assert "site%3Alever.co" in google_url

    ddg_url = build_direct_search_url("Uber", "Software Engineer", engine="duckduckgo")
    assert ddg_url.startswith("https://html.duckduckgo.com/html/?q=")
    assert "Uber" in ddg_url


def test_resolve_job_link() -> None:
    """Verify resolve_job_link unwrapping and fallback search URL generation."""
    target_ats = "https://boards.greenhouse.io/amazon/jobs/101"
    aggregator_url = f"https://www.glassdoor.com/job-listing/?url={urllib.parse.quote(target_ats)}"
    job = {
        "company": "Amazon",
        "title": "Software Development Engineer I",
        "apply_url": aggregator_url,
    }

    resolved_apply, direct_search = resolve_job_link(job)
    assert resolved_apply == target_ats
    assert "Amazon" in direct_search
    assert "Software+Development+Engineer+I" in direct_search


def test_update_job_direct_search_url_db(tmp_path: Path) -> None:
    """Verify update_job_direct_search_url updates the database record."""
    db_file = tmp_path / "test_resolve.db"
    init_db(db_file)

    job = JobPosting(
        id="test_job_1",
        company="TestCorp",
        title="Software Engineer 1",
        location="Bengaluru",
        apply_url="https://www.glassdoor.com/job-listing/?jl=12345",
        provider=ATSProvider.GREENHOUSE,
    )
    record_jobs([job], db_path=db_file)

    stored = get_job_by_id("test_job_1", db_path=db_file)
    assert stored is not None
    num_id = stored["numeric_id"]

    search_url = build_direct_search_url("TestCorp", "Software Engineer 1")
    update_job_direct_search_url(num_id, search_url, db_path=db_file)

    updated = get_job_by_id(num_id, db_path=db_file)
    assert updated["direct_search_url"] == search_url


def test_cli_apply_with_fallback(tmp_path: Path) -> None:
    """Verify CLI apply --fallback opens browser and records direct ATS search note."""
    db_file = tmp_path / "test_apply_fb.db"
    init_db(db_file)

    job = JobPosting(
        id="test_fb_job",
        company="BlockCorp",
        title="Associate Engineer",
        location="Remote",
        apply_url="https://www.glassdoor.com/job-listing/?jl=999",
        provider=ATSProvider.LEVER,
        status="NEEDS_RESOLVE",
    )
    record_jobs([job], db_path=db_file)
    stored = get_job_by_id("test_fb_job", db_path=db_file)
    num_id = stored["numeric_id"]

    with patch("webbrowser.open") as mock_browser_open:
        res = runner.invoke(app, ["apply", str(num_id), "--fallback", "--db", str(db_file)])
        assert res.exit_code == 0
        assert "APPLIED" in res.output
        mock_browser_open.assert_called_once()
        opened_url = mock_browser_open.call_args[0][0]
        assert "BlockCorp" in opened_url
        assert "careers" in opened_url

    updated = get_job_by_id(num_id, db_path=db_file)
    assert updated["status"] == "APPLIED"
    assert "Opened direct ATS search:" in updated["notes"]


def test_tools_resolve_all_links(tmp_path: Path) -> None:
    """Verify resolve_links utility scans and updates database entries."""
    db_file = tmp_path / "test_tool_resolve.db"
    init_db(db_file)

    target_ats = "https://jobs.lever.co/stripe/abc"
    wrapped_url = f"https://www.glassdoor.com/job-listing/?url={urllib.parse.quote(target_ats)}"

    job = JobPosting(
        id="tool_job",
        company="Stripe",
        title="Software Engineer",
        location="Bengaluru",
        apply_url=wrapped_url,
        provider=ATSProvider.LEVER,
        status="NEEDS_RESOLVE",
    )
    record_jobs([job], db_path=db_file)

    updates = resolve_links(db_path=db_file, dry_run=False, only_missing=False)
    assert len(updates) == 1

    updated = get_job_by_id("tool_job", db_path=db_file)
    assert updated["direct_search_url"] is not None
    assert "Stripe" in updated["direct_search_url"]


def test_is_glassdoor_url() -> None:
    """Verify Glassdoor domain detection."""
    assert is_glassdoor_url("https://www.glassdoor.com/job-listing/?jl=1010251913049") is True
    assert is_glassdoor_url("https://glassdoor.co.in/job-listing/?jl=123") is True
    assert is_glassdoor_url("https://www.glassdoor.com/partner/jobListing.htm") is True
    assert is_glassdoor_url("https://boards.greenhouse.io/stripe/jobs/123") is False
    assert is_glassdoor_url("https://jobs.lever.co/netflix") is False
    assert is_glassdoor_url("") is False


def test_resolve_company_career_portal() -> None:
    """Verify official careers portal resolution from known portals and company registry."""
    # Known custom portal dictionary
    assert resolve_company_career_portal("BT Group") == "https://jobs.bt.com"
    assert resolve_company_career_portal("BT") == "https://jobs.bt.com"
    assert resolve_company_career_portal("Amazon") == "https://amazon.jobs"
    assert resolve_company_career_portal("Microsoft") == "https://careers.microsoft.com"
    assert resolve_company_career_portal("Unisys") == "https://jobs.unisys.com"
    assert resolve_company_career_portal("Smiths Medical") == "https://smithsmedical.wd3.myworkdayjobs.com/External"

    # From COMPANIES registry
    celonis_portal = resolve_company_career_portal("Celonis")
    assert celonis_portal is not None
    assert "job-boards.greenhouse.io" in celonis_portal

    walmart_portal = resolve_company_career_portal("Walmart Global Tech")
    assert walmart_portal is not None
    assert "myworkdayjobs.com" in walmart_portal

    # Unknown company
    assert resolve_company_career_portal("UnknownStartupNonExistent12345") is None


def test_build_direct_careers_search_url() -> None:
    """Verify unblocked direct careers search URL generation."""
    url = build_direct_careers_search_url("BT Group", "Associate Engineer")
    assert url.startswith("https://www.google.com/search?q=")
    assert "BT+Group" in url
    assert "Associate+Engineer" in url
    assert "careers" in url
    assert "jobs" in url
    # Ensure it is not restricted to greenhouse/lever only
    assert "site%3Agreenhouse.io" not in url

    ddg_url = build_direct_careers_search_url("PartyFly", engine="duckduckgo")
    assert ddg_url.startswith("https://html.duckduckgo.com/html/?q=")
    assert "PartyFly" in ddg_url


def test_is_job_legitimate() -> None:
    """Verify job legitimacy filter rules."""
    # Genuine entry-level role
    legit, _ = is_job_legitimate("BT Group", "Associate Engineer", "Bengaluru")
    assert legit is True

    # Genuine remote intern
    legit, _ = is_job_legitimate("Acme Tech", "Software Developer Intern", "Remote")
    assert legit is True

    # Experienced role
    legit, reason = is_job_legitimate("Google", "Senior Principal Architect (10+ years exp)", "Bengaluru")
    assert legit is False

    # Spam posting
    legit, reason = is_job_legitimate("EarnFast", "Earn money without investment data entry", "Remote")
    assert legit is False
    assert "promotional/spam" in reason

    # Invalid empty company or title
    legit, _ = is_job_legitimate("", "Software Engineer")
    assert legit is False
    legit, _ = is_job_legitimate("Acme", "")
    assert legit is False


def test_resolve_effective_apply_url() -> None:
    """Verify resolve_effective_apply_url bypasses Glassdoor links."""
    # 1. Glassdoor URL for company with known career portal (BT Group)
    bt_job = {
        "company": "BT Group",
        "title": "Associate engineer",
        "apply_url": "https://www.glassdoor.com/job-listing/?jl=1010251913049",
    }
    eff_url, search_url, label = resolve_effective_apply_url(bt_job)
    assert eff_url == "https://jobs.bt.com"
    assert label == "Official Careers Portal"
    assert "BT+Group" in search_url

    # 2. Glassdoor URL for company with newly registered careers portal (Devmani Traders)
    devmani_job = {
        "company": "Devmani Traders",
        "title": "Full Stack Developer Intern",
        "apply_url": "https://www.glassdoor.com/job-listing/?jl=99999",
    }
    eff_url, search_url, label = resolve_effective_apply_url(devmani_job)
    assert eff_url == "https://devmanitraders.com/careers"
    assert label == "Official Careers Portal"
    assert "glassdoor.com" not in eff_url

    # 3. Glassdoor URL for unknown company without registered careers portal (uses direct careers redirect, never Glassdoor)
    unknown_job = {
        "company": "Acme Software Labs",
        "title": "Junior Developer",
        "apply_url": "https://www.glassdoor.com/job-listing/?jl=88888",
    }
    eff_url, search_url, label = resolve_effective_apply_url(unknown_job)
    assert "glassdoor.com" not in eff_url
    assert "btnI=1" in eff_url
    assert label == "Acme Software Labs Careers"

    # 3. Direct ATS URL remains untouched
    ats_job = {
        "company": "Stripe",
        "title": "Software Engineer",
        "apply_url": "https://boards.greenhouse.io/stripe/jobs/123",
    }
    eff_url, _, label = resolve_effective_apply_url(ats_job)
    assert eff_url == "https://boards.greenhouse.io/stripe/jobs/123"
    assert label == "Apply on ATS"

    # 4. MetLife official careers portal lookup
    assert resolve_company_career_portal("MetLife") == "https://www.metlifecareers.com"

    # 5. Direct LinkedIn job opening link is preserved
    linkedin_job = {
        "company": "MetLife",
        "title": "Software Engineer I",
        "apply_url": "https://www.linkedin.com/jobs/view/4458281479/",
    }
    eff_url, _, label = resolve_effective_apply_url(linkedin_job)
    assert eff_url == "https://www.linkedin.com/jobs/view/4458281479/"
    assert label == "Apply on LinkedIn"

    # 6. Generic LinkedIn search URL falls back to company careers portal
    generic_search_job = {
        "company": "MetLife",
        "title": "Software Engineer I",
        "apply_url": "https://www.linkedin.com/jobs/search?keywords=metlife",
    }
    eff_url, _, label = resolve_effective_apply_url(generic_search_job)
    assert eff_url == "https://www.metlifecareers.com"
    assert label == "Official Careers Portal"


@pytest.mark.asyncio
async def test_telegram_notification_bypasses_glassdoor() -> None:
    """Verify Telegram notification message embeds direct career portals instead of Glassdoor."""
    from gcc_job_radar.notifier import send_telegram_notification
    import httpx

    glassdoor_job = JobPosting(
        id="test_gd_job",
        company="BT Group",
        title="Associate engineer",
        location="Bengaluru",
        apply_url="https://www.glassdoor.com/job-listing/?jl=1010251913049",
        provider=ATSProvider.EMAIL_ALERT,
    )

    captured_payload = None

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_payload
        import json
        captured_payload = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        ok = await send_telegram_notification("token123", "chat456", [glassdoor_job], client)
        assert ok is True

    assert captured_payload is not None
    text = captured_payload["text"]
    assert "https://jobs.bt.com" in text
    assert "Official Careers Portal" in text
    assert "glassdoor.com" not in text
