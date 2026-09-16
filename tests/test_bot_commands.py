"""Tests for Telegram bot commands (/apply, /dismiss, /restore) and multi-selector job resolution."""

from pathlib import Path
from unittest.mock import AsyncMock, patch
import httpx
import pytest

from gcc_job_radar.ai_agent import ask_ai_agent, execute_tool, format_tool_result_summary
from gcc_job_radar.bot_listener import handle_command
from gcc_job_radar.db import (
    find_jobs_by_selector,
    get_job_by_id,
    init_db,
    mark_job_status,
    record_jobs,
)
from gcc_job_radar.models import ATSProvider, JobPosting


@pytest.fixture
def test_db_with_jobs(tmp_path: Path) -> Path:
    db_file = tmp_path / "test_bot_cmds.db"
    init_db(db_file)
    jobs = [
        JobPosting(
            id="celonis_101",
            company="Celonis",
            title="Associate Python Engineer",
            location="Bengaluru, Karnataka, India",
            apply_url="https://job-boards.greenhouse.io/celonis/jobs/101",
            provider=ATSProvider.GREENHOUSE,
            direct_search_url="https://www.google.com/search?q=Celonis+Associate+Python+Engineer",
        ),
        JobPosting(
            id="databricks_102",
            company="Databricks",
            title="Graduate Software Engineer",
            location="Hyderabad, India",
            apply_url="https://job-boards.greenhouse.io/databricks/jobs/102",
            provider=ATSProvider.GREENHOUSE,
        ),
        JobPosting(
            id="devmani_103",
            company="Devmani Traders",
            title="Full Stack Developer Intern",
            location="Remote",
            apply_url="https://www.glassdoor.com/job-listing/?jl=103",
            provider=ATSProvider.EMAIL_ALERT,
        ),
        JobPosting(
            id="bt_104",
            company="BT Group",
            title="Associate engineer",
            location="Bengaluru",
            apply_url="https://jobs.bt.com/job/104",
            provider=ATSProvider.EMAIL_ALERT,
            direct_search_url="https://jobs.bt.com/search/?q=61954",
        ),
    ]
    record_jobs(jobs, db_file)
    return db_file


# 1. find_jobs_by_selector Tests


def test_find_jobs_by_selector_single_id(test_db_with_jobs: Path) -> None:
    """Verify single numeric ID, #ID, and exact key lookups."""
    # Find by numeric ID
    res1 = find_jobs_by_selector("1", db_path=test_db_with_jobs)
    assert len(res1) == 1
    assert res1[0]["company"] == "Celonis"

    # Find by #ID
    res2 = find_jobs_by_selector("#2", db_path=test_db_with_jobs)
    assert len(res2) == 1
    assert res2[0]["company"] == "Databricks"

    # Find by exact unique key
    res3 = find_jobs_by_selector("greenhouse_celonis_celonis_101", db_path=test_db_with_jobs)
    assert len(res3) == 1
    assert res3[0]["company"] == "Celonis"


def test_find_jobs_by_selector_multiple_ids(test_db_with_jobs: Path) -> None:
    """Verify comma-, space-, and 'and'-separated ID lists."""
    res_comma = find_jobs_by_selector("1, 3", db_path=test_db_with_jobs)
    assert len(res_comma) == 2
    comps = [j["company"] for j in res_comma]
    assert "Celonis" in comps
    assert "Devmani Traders" in comps

    res_space = find_jobs_by_selector("2 4", db_path=test_db_with_jobs)
    assert len(res_space) == 2
    comps_space = [j["company"] for j in res_space]
    assert "Databricks" in comps_space
    assert "BT Group" in comps_space

    res_and = find_jobs_by_selector("1 and 4", db_path=test_db_with_jobs)
    assert len(res_and) == 2


def test_find_jobs_by_selector_company_and_title(test_db_with_jobs: Path) -> None:
    """Verify search by company name and title substring."""
    res_comp = find_jobs_by_selector("Devmani", db_path=test_db_with_jobs)
    assert len(res_comp) == 1
    assert res_comp[0]["company"] == "Devmani Traders"

    res_title = find_jobs_by_selector("intern", db_path=test_db_with_jobs)
    assert len(res_title) == 1
    assert "Intern" in res_title[0]["title"]


def test_find_jobs_by_selector_empty_and_unknown(test_db_with_jobs: Path) -> None:
    """Verify empty query or unmatched query returns empty list."""
    assert find_jobs_by_selector("", db_path=test_db_with_jobs) == []
    assert find_jobs_by_selector("NonExistentCompanyXYZ", db_path=test_db_with_jobs) == []


# 2. Telegram Bot Command Handler Tests


@pytest.mark.asyncio
async def test_bot_handle_command_dismiss(test_db_with_jobs: Path) -> None:
    """Verify /dismiss marks job as DISMISSED and sends Telegram reply."""
    replies = []

    def mock_transport(request: httpx.Request) -> httpx.Response:
        replies.append(request.read().decode("utf-8"))
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(mock_transport)) as client:
        await handle_command(
            command_text="/dismiss 1, 3",
            chat_id="12345",
            bot_token="test_token",
            allowed_chat_id="12345",
            client=client,
            db_path=test_db_with_jobs,
        )

    assert len(replies) == 1
    assert "Dismissed 2 Job(s)" in replies[0]
    assert "Celonis" in replies[0]
    assert "Devmani Traders" in replies[0]

    # Check database status
    j1 = get_job_by_id(1, db_path=test_db_with_jobs)
    j3 = get_job_by_id(3, db_path=test_db_with_jobs)
    assert j1["status"] == "DISMISSED"
    assert j3["status"] == "DISMISSED"


@pytest.mark.asyncio
async def test_bot_handle_command_apply(test_db_with_jobs: Path) -> None:
    """Verify /apply marks job as APPLIED, saves notes, and sends Telegram reply."""
    replies = []

    def mock_transport(request: httpx.Request) -> httpx.Response:
        replies.append(request.read().decode("utf-8"))
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(mock_transport)) as client:
        await handle_command(
            command_text='/apply BT Group -n "Applied via Req 61954"',
            chat_id="12345",
            bot_token="test_token",
            allowed_chat_id="12345",
            client=client,
            db_path=test_db_with_jobs,
        )

    assert len(replies) == 1
    assert "Marked as APPLIED" in replies[0]
    assert "BT Group" in replies[0]
    assert "Applied via Req 61954" in replies[0]
    assert "Apply Link" in replies[0]

    # Check database status
    j4 = get_job_by_id(4, db_path=test_db_with_jobs)
    assert j4["status"] == "APPLIED"
    assert "Req 61954" in j4["notes"]
    assert j4["applied_at"] is not None


@pytest.mark.asyncio
async def test_bot_handle_command_restore(test_db_with_jobs: Path) -> None:
    """Verify /restore and /undismiss revert job back to NEW status."""
    # First dismiss job 1
    mark_job_status(1, "DISMISSED", db_path=test_db_with_jobs)
    assert get_job_by_id(1, db_path=test_db_with_jobs)["status"] == "DISMISSED"

    replies = []

    def mock_transport(request: httpx.Request) -> httpx.Response:
        replies.append(request.read().decode("utf-8"))
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(mock_transport)) as client:
        await handle_command(
            command_text="/restore 1",
            chat_id="12345",
            bot_token="test_token",
            allowed_chat_id="12345",
            client=client,
            db_path=test_db_with_jobs,
        )

    assert len(replies) == 1
    assert "Restored 1 Job(s) to NEW" in replies[0]
    assert "Celonis" in replies[0]

    # Verify status in database
    j1 = get_job_by_id(1, db_path=test_db_with_jobs)
    assert j1["status"] == "NEW"


@pytest.mark.asyncio
async def test_bot_handle_command_usage_and_errors(test_db_with_jobs: Path) -> None:
    """Verify command usage on empty input and error message when job is not found."""
    replies = []

    def mock_transport(request: httpx.Request) -> httpx.Response:
        replies.append(request.read().decode("utf-8"))
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(mock_transport)) as client:
        # Empty /dismiss
        await handle_command(
            command_text="/dismiss",
            chat_id="12345",
            bot_token="test_token",
            allowed_chat_id="12345",
            client=client,
            db_path=test_db_with_jobs,
        )
        # Not found /apply
        await handle_command(
            command_text="/apply 999",
            chat_id="12345",
            bot_token="test_token",
            allowed_chat_id="12345",
            client=client,
            db_path=test_db_with_jobs,
        )

    assert len(replies) == 2
    assert "Usage:</b> <code>/dismiss" in replies[0]
    assert "No jobs found matching '<code>999</code>'" in replies[1]


# 3. AI Agent Tool & Fallback Tests


@pytest.mark.asyncio
async def test_ai_agent_manage_job_status_tool(test_db_with_jobs: Path) -> None:
    """Verify execute_tool executes manage_job_status for apply, dismiss, and restore."""
    # Dismiss
    res_dismiss = await execute_tool(
        "manage_job_status",
        {"action": "dismiss", "target": "Devmani Traders"},
        db_path=test_db_with_jobs,
    )
    assert res_dismiss["status"] == "success"
    assert res_dismiss["target_status"] == "DISMISSED"
    assert res_dismiss["count"] == 1
    assert get_job_by_id(3, db_path=test_db_with_jobs)["status"] == "DISMISSED"

    # Restore
    res_restore = await execute_tool(
        "manage_job_status",
        {"action": "restore", "target": "3"},
        db_path=test_db_with_jobs,
    )
    assert res_restore["status"] == "success"
    assert res_restore["target_status"] == "NEW"
    assert get_job_by_id(3, db_path=test_db_with_jobs)["status"] == "NEW"

    # Apply
    res_apply = await execute_tool(
        "manage_job_status",
        {"action": "apply", "target": "4", "notes": "Applied on portal"},
        db_path=test_db_with_jobs,
    )
    assert res_apply["status"] == "success"
    assert res_apply["target_status"] == "APPLIED"
    assert get_job_by_id(4, db_path=test_db_with_jobs)["status"] == "APPLIED"


@pytest.mark.asyncio
async def test_ai_agent_fallback_status_intents(
    test_db_with_jobs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify natural fallback commands (dismiss, apply, restore) execute without LLM keys."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    # Test natural dismiss
    res_d = await ask_ai_agent("dismiss 1, 2", chat_id="chat-test", db_path=test_db_with_jobs)
    assert "Dismissed 2 Job(s)" in res_d
    assert "Celonis" in res_d
    assert "Databricks" in res_d
    assert get_job_by_id(1, db_path=test_db_with_jobs)["status"] == "DISMISSED"

    # Test natural restore
    res_r = await ask_ai_agent("restore 1", chat_id="chat-test", db_path=test_db_with_jobs)
    assert "Restored 1 Job(s) to NEW" in res_r
    assert get_job_by_id(1, db_path=test_db_with_jobs)["status"] == "NEW"


def test_find_jobs_by_selector_multi_company(test_db_with_jobs: Path) -> None:
    """Verify comma- and 'and'-separated multi-company searches."""
    # Comma-separated companies
    res_comma = find_jobs_by_selector("Celonis, Databricks", db_path=test_db_with_jobs)
    assert len(res_comma) == 2
    comps = {j["company"] for j in res_comma}
    assert comps == {"Celonis", "Databricks"}

    # 'and'-separated companies
    res_and = find_jobs_by_selector("BT Group and Devmani Traders", db_path=test_db_with_jobs)
    assert len(res_and) == 2
    comps_and = {j["company"] for j in res_and}
    assert comps_and == {"BT Group", "Devmani Traders"}

    # Mixed ID and company
    res_mixed = find_jobs_by_selector("1, Databricks", db_path=test_db_with_jobs)
    assert len(res_mixed) == 2
    comps_mixed = {j["company"] for j in res_mixed}
    assert comps_mixed == {"Celonis", "Databricks"}


@pytest.mark.asyncio
async def test_bot_handle_command_applied(test_db_with_jobs: Path) -> None:
    """Verify /applied command returns empty message and formatted list after applying."""
    replies = []

    def mock_transport(request: httpx.Request) -> httpx.Response:
        replies.append(request.read().decode("utf-8"))
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(mock_transport)) as client:
        # 1. When no jobs are applied yet
        await handle_command(
            command_text="/applied",
            chat_id="12345",
            bot_token="test_token",
            allowed_chat_id="12345",
            client=client,
            db_path=test_db_with_jobs,
        )
        assert "No Applied Roles Recorded" in replies[0]

        # 2. Mark Celonis and BT Group as applied
        mark_job_status(1, "APPLIED", notes="Online ATS", db_path=test_db_with_jobs)
        mark_job_status(4, "APPLIED", notes="Referral", db_path=test_db_with_jobs)

        # 3. Call /applied again
        await handle_command(
            command_text="/applied",
            chat_id="12345",
            bot_token="test_token",
            allowed_chat_id="12345",
            client=client,
            db_path=test_db_with_jobs,
        )
        assert "Your Applied Listings (2)" in replies[1]
        assert "Celonis" in replies[1]
        assert "BT Group" in replies[1]


@pytest.mark.asyncio
async def test_ai_agent_get_applied_jobs_tool(test_db_with_jobs: Path) -> None:
    """Verify execute_tool retrieves real applied jobs from database."""
    # Before applying
    res_empty = await execute_tool("get_applied_jobs", {}, db_path=test_db_with_jobs)
    assert res_empty["status"] == "success"
    assert res_empty["count"] == 0

    # Mark two jobs applied
    mark_job_status(1, "APPLIED", db_path=test_db_with_jobs)
    mark_job_status(2, "APPLIED", db_path=test_db_with_jobs)

    res_applied = await execute_tool("get_applied_jobs", {}, db_path=test_db_with_jobs)
    assert res_applied["status"] == "success"
    assert res_applied["count"] == 2
    comps = {j["company"] for j in res_applied["jobs"]}
    assert comps == {"Celonis", "Databricks"}


@pytest.mark.asyncio
async def test_ai_agent_fallback_applied_intents(
    test_db_with_jobs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify rule-based fallback handles applied sheet requests and 'applied to X, Y'."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    # 1. "applied to Celonis, Databricks"
    res_apply = await ask_ai_agent(
        "applied to Celonis, Databricks",
        chat_id="chat-test-applied",
        db_path=test_db_with_jobs,
    )
    assert "Marked as APPLIED (2)" in res_apply
    assert "Celonis" in res_apply
    assert "Databricks" in res_apply
    assert get_job_by_id(1, db_path=test_db_with_jobs)["status"] == "APPLIED"
    assert get_job_by_id(2, db_path=test_db_with_jobs)["status"] == "APPLIED"

    # 2. "pull out the applied sheet"
    res_sheet = await ask_ai_agent(
        "pull out the applied sheet",
        chat_id="chat-test-applied",
        db_path=test_db_with_jobs,
    )
    assert "Your Applied Listings (2)" in res_sheet
    assert "Celonis" in res_sheet
    assert "Databricks" in res_sheet

    # 3. "applied list"
    res_list = await ask_ai_agent(
        "applied list",
        chat_id="chat-test-applied",
        db_path=test_db_with_jobs,
    )
    assert "Your Applied Listings (2)" in res_list


def test_parse_apply_target() -> None:
    """Verify parse_apply_target accurately parses company, title, and notes."""
    from gcc_job_radar.ai_agent import parse_apply_target

    # 1. Simple "<company> applied"
    c, t, n = parse_apply_target("flam applied")
    assert c == "Flam"
    assert t == "Software Engineer"
    assert n is None

    # 2. Complex conversational with typos and role specification
    c2, t2, n2 = parse_apply_target("i mean i applies to flam software engineering intern opening so mark it as aoplies")
    assert c2 == "Flam"
    assert "Software Engineering Intern" in t2
    assert n2 is None

    # 3. Title at Company
    c3, t3, n3 = parse_apply_target("software engineering intern at flam")
    assert c3 == "Flam"
    assert t3 == "Software Engineering Intern"

    # 4. Notes flag
    c4, t4, n4 = parse_apply_target("Google applied -n Referral from Alice")
    assert c4 == "Google"
    assert n4 == "Referral from Alice"


@pytest.mark.asyncio
async def test_handle_command_apply_adhoc_missing_job(test_db_with_jobs: Path) -> None:
    """Verify /apply auto-records ad-hoc application when job is not in DB."""
    replies = []

    async def mock_send_reply(token, cid, text, client):
        replies.append(text)

    async with httpx.AsyncClient() as client:
        with patch("gcc_job_radar.bot_listener.send_telegram_reply", side_effect=mock_send_reply):
            # Apply to Flam (not previously in database)
            await handle_command(
                command_text="/apply Flam Software Engineering Intern -n Applied via LinkedIn",
                chat_id="12345",
                bot_token="test_token",
                allowed_chat_id="12345",
                client=client,
                db_path=test_db_with_jobs,
            )

    assert len(replies) == 1
    assert "Marked as APPLIED (1)" in replies[0]
    assert "Flam" in replies[0]
    assert "Software Engineering Intern" in replies[0]
    assert "Applied via LinkedIn" in replies[0]

    # Verify job is recorded in database
    flam_jobs = find_jobs_by_selector("Flam", db_path=test_db_with_jobs)
    assert len(flam_jobs) == 1
    assert flam_jobs[0]["company"] == "Flam"
    assert flam_jobs[0]["status"] == "APPLIED"


@pytest.mark.asyncio
async def test_ai_agent_fallback_trailing_apply(
    test_db_with_jobs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify natural language 'flam applied' marks/records application via fallback."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    res = await ask_ai_agent(
        "flam applied",
        chat_id="chat-test-flam",
        db_path=test_db_with_jobs,
    )
    assert "Marked as APPLIED" in res
    assert "Flam" in res


@pytest.mark.asyncio
async def test_handle_command_scan_new(test_db_with_jobs: Path) -> None:
    """Verify /scan new scans only the newly added companies."""
    replies = []

    async def mock_send_reply(token, cid, text, client):
        replies.append(text)

    mock_scan_called_with = []

    async def mock_scan(companies):
        mock_scan_called_with.append(companies)
        return []

    async with httpx.AsyncClient() as client:
        with patch("gcc_job_radar.bot_listener.send_telegram_reply", side_effect=mock_send_reply), \
             patch("gcc_job_radar.bot_listener.scan_all_companies", side_effect=mock_scan):

            # Test /scan new 10
            await handle_command(
                command_text="/scan new 10",
                chat_id="12345",
                bot_token="test_token",
                allowed_chat_id="12345",
                client=client,
                db_path=test_db_with_jobs,
            )

    assert any("the last <b>10</b> newly added companies" in r for r in replies)


def test_format_jobs_html_ranked_recommendations() -> None:
    """Verify format_jobs_html outputs ranked recommendations with lead-in, tiers, why-lines, and tail summary."""
    from gcc_job_radar.bot_listener import format_jobs_html
    from gcc_job_radar.models import ATSProvider, JobPosting

    jobs = [
        JobPosting(
            id=f"job-{i}",
            company=f"TargetComp_{i}",
            title="Java Backend Engineer" if i == 0 else "Associate Support Engineer",
            location="Bengaluru",
            apply_url=f"https://example.com/job/{i}",
            provider=ATSProvider.GREENHOUSE,
            relevance_score=80 if i == 0 else 10,
        )
        for i in range(7)
    ]

    html_out = format_jobs_html(jobs, "Latest Roles", max_full_cards=5)

    # Lead-in
    assert "Found 7 new roles, 1 strong fit for your stack." in html_out
    # Tiers
    assert "⭐ Best Fit:" in html_out
    assert "📋 Worth a Look:" in html_out
    # Best fit called out first
    assert "<b>1. TargetComp_0</b>" in html_out
    # Why line present
    assert "💡 <i>" in html_out
    # Tail summary present with company names
    assert "+2 more roles at TargetComp_5, TargetComp_6" in html_out
    assert "reply <code>/latest all</code> or search by company to view" in html_out


