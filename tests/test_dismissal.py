import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from gcc_job_radar.db import (
    dismiss_selectors_or_companies,
    get_applied_and_dismissed_companies,
    init_db,
    is_company_excluded,
    mark_job_status,
    record_jobs,
)
from gcc_job_radar.models import ATSProvider, JobPosting


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    db_file = tmp_path / "test_dismiss.db"
    init_db(db_file)
    return db_file


def test_is_company_excluded() -> None:
    excluded = {"wysa", "katalystcs", "wsp", "betterworks"}

    # Exact lowercase match
    assert is_company_excluded("wysa", excluded) is True
    assert is_company_excluded("Wysa", excluded) is True
    assert is_company_excluded("WSP", excluded) is True

    # Substring / Long form matches
    assert is_company_excluded("KATALYSTCS CONSULTING SERVICES PRIVATE LIMITED", excluded) is True
    assert is_company_excluded("WSP India Pvt Ltd", excluded) is True
    assert is_company_excluded("Betterworks Inc", excluded) is True

    # Unrelated companies should NOT match
    assert is_company_excluded("Google", excluded) is False
    assert is_company_excluded("Microsoft", excluded) is False
    assert is_company_excluded("Amazon", excluded) is False


def test_dismiss_single_and_multiple_numeric_ids(temp_db: Path) -> None:
    job1 = JobPosting(
        id="gh_1",
        company="Company A",
        title="Role 1",
        location="Bengaluru",
        apply_url="https://example.com/1",
        provider=ATSProvider.GREENHOUSE,
    )
    job2 = JobPosting(
        id="gh_2",
        company="Company B",
        title="Role 2",
        location="Bengaluru",
        apply_url="https://example.com/2",
        provider=ATSProvider.GREENHOUSE,
    )
    record_jobs([job1, job2], db_path=temp_db)

    # Dismiss by numeric ID #1
    res1 = dismiss_selectors_or_companies("1", db_path=temp_db)
    assert res1["status"] == "success"
    assert res1["total_jobs"] == 1
    assert "gh_1" in res1["dismissed_jobs"][0]["id"]

    # Dismiss by numeric ID #2
    res2 = dismiss_selectors_or_companies("#2", db_path=temp_db)
    assert res2["status"] == "success"
    assert res2["total_jobs"] == 1
    assert "gh_2" in res2["dismissed_jobs"][0]["id"]


def test_dismiss_existing_company_jobs(temp_db: Path) -> None:
    job1 = JobPosting(
        id="gh_wysa_1",
        company="Wysa",
        title="Associate Full Stack Engineer",
        location="Bengaluru",
        apply_url="https://example.com/wysa1",
        provider=ATSProvider.GREENHOUSE,
    )
    job2 = JobPosting(
        id="gh_wysa_2",
        company="Wysa",
        title="Junior Backend Developer",
        location="Bengaluru",
        apply_url="https://example.com/wysa2",
        provider=ATSProvider.GREENHOUSE,
    )
    record_jobs([job1, job2], db_path=temp_db)

    res = dismiss_selectors_or_companies("Wysa", db_path=temp_db)
    assert res["status"] == "success"
    assert res["total_jobs"] == 2
    assert "Wysa" in res["dismissed_companies"]

    applied, dismissed = get_applied_and_dismissed_companies(temp_db)
    assert "wysa" in dismissed


def test_dismiss_adhoc_multi_companies_on_empty_db(temp_db: Path) -> None:
    """Verify that dismissing companies not yet present in SQLite creates adhoc suppressions."""
    target_str = "wysa, katalystcs, tvaram, WSP, betterworks"
    res = dismiss_selectors_or_companies(target_str, db_path=temp_db)

    assert res["status"] == "success"
    assert res["total_adhoc"] == 5
    assert len(res["dismissed_companies"]) == 5

    applied, dismissed = get_applied_and_dismissed_companies(temp_db)
    assert "wysa" in dismissed
    assert "katalystcs" in dismissed
    assert "tvaram" in dismissed
    assert "wsp" in dismissed
    assert "betterworks" in dismissed


@pytest.mark.asyncio
async def test_ai_agent_manage_job_status_dismiss(temp_db: Path) -> None:
    from gcc_job_radar.ai_agent import execute_tool

    res = await execute_tool(
        "manage_job_status",
        {"action": "dismiss", "target": "wysa, katalystcs, tvaram, WSP, betterworks"},
        db_path=temp_db,
    )

    assert res["status"] == "success"
    assert res["action"] == "dismiss"
    assert res["count"] == 5
    assert len(res["companies"]) == 5


@pytest.mark.asyncio
async def test_bot_listener_dismiss_command(temp_db: Path) -> None:
    from gcc_job_radar.bot_listener import handle_command

    mock_client = AsyncMock()
    mock_post = AsyncMock()
    mock_post.return_value.status_code = 200
    mock_client.post = mock_post

    await handle_command(
        command_text="/dismiss wysa, katalystcs, tvaram, WSP, betterworks",
        chat_id="12345",
        bot_token="fake_token",
        allowed_chat_id="12345",
        client=mock_client,
        db_path=temp_db,
    )

    assert mock_post.called
    sent_payload = mock_post.call_args[1]["json"]
    sent_text = sent_payload["text"]

    assert "Dismissed" in sent_text
    assert "Wysa" in sent_text
    assert "Katalystcs" in sent_text or "katalystcs" in sent_text.lower()
    assert "Tvaram" in sent_text
    assert "Wsp" in sent_text or "WSP" in sent_text
    assert "Betterworks" in sent_text


@pytest.mark.asyncio
async def test_dismiss_role_does_not_block_future_roles(temp_db: Path) -> None:
    """Ensure dismissing a role does not blacklist the company: future new roles must still be detected and notified."""
    from gcc_job_radar.db import filter_new_jobs
    from gcc_job_radar.notifier import dispatch_notifications

    # 1. Old role for Wysa is shown and dismissed
    old_role = JobPosting(
        id="gh_wysa_old_1",
        company="Wysa",
        title="Associate Full Stack Engineer",
        location="Bengaluru",
        apply_url="https://example.com/wysa/old_role",
        provider=ATSProvider.GREENHOUSE,
    )
    record_jobs([old_role], db_path=temp_db)
    dismiss_selectors_or_companies("Wysa", db_path=temp_db)

    # 2. In future scan, Wysa posts a brand new role
    new_role = JobPosting(
        id="gh_wysa_new_2",
        company="Wysa",
        title="Junior Software Development Engineer",
        location="Bengaluru",
        apply_url="https://example.com/wysa/new_role",
        provider=ATSProvider.GREENHOUSE,
    )

    # 3. Verify the new role is identified as NEW (not blocked)
    new_jobs, _ = filter_new_jobs([new_role], db_path=temp_db)
    assert len(new_jobs) == 1
    assert new_jobs[0].title == "Junior Software Development Engineer"

    # 4. Verify notifications are successfully dispatched for the new role
    with patch("gcc_job_radar.notifier.send_telegram_notification", new_callable=AsyncMock) as mock_send_tg:
        mock_send_tg.return_value = True
        await dispatch_notifications(
            new_jobs=[new_role],
            telegram_token="dummy_token",
            telegram_chat_id="12345",
            db_path=temp_db,
        )
        assert mock_send_tg.called
        dispatched_jobs = mock_send_tg.call_args[0][2]
        assert len(dispatched_jobs) == 1
        assert dispatched_jobs[0].company == "Wysa"


@pytest.mark.asyncio
async def test_sync_dismissals_registry_and_filter_unalerted(temp_db: Path) -> None:
    """Verify sync_dismissals_registry correctly suppresses offending openings across all platforms."""
    from gcc_job_radar.db import filter_unalerted_jobs, sync_dismissals_registry
    from gcc_job_radar.notifier import dispatch_notifications

    # 1. Run sync_dismissals_registry on a fresh database
    count = sync_dismissals_registry(temp_db)
    assert count >= 8

    # 2. Re-create the offending Katalon and Gallagher postings
    katalon_role = JobPosting(
        id="custom_katalon_af9103e5f0",
        company="Katalon",
        title="AI Application Engineering Intern",
        location="India",
        apply_url="https://careers.katalon.com/jobs/8186184-ai-application-engineering-intern",
        provider=ATSProvider.CUSTOM,
    )
    gallagher_role = JobPosting(
        id="custom_gallagher_59751f2c84",
        company="Gallagher",
        title="Junior Test Analyst",
        location="India",
        apply_url="https://careers.gallagher.com/jobs/8369837-junior-test-analyst",
        provider=ATSProvider.CUSTOM,
    )
    # A new, legitimate role from Katalon that was NOT dismissed
    katalon_new_role = JobPosting(
        id="custom_katalon_new_12345",
        company="Katalon",
        title="Junior Backend Developer",
        location="India",
        apply_url="https://careers.katalon.com/jobs/999999-junior-backend-developer",
        provider=ATSProvider.CUSTOM,
    )

    # 3. filter_unalerted_jobs must discard the dismissed roles and keep only the new role
    unalerted_tg = filter_unalerted_jobs([katalon_role, gallagher_role, katalon_new_role], "telegram", db_path=temp_db)
    assert len(unalerted_tg) == 1
    assert unalerted_tg[0].id == "custom_katalon_new_12345"

    unalerted_dc = filter_unalerted_jobs([katalon_role, gallagher_role, katalon_new_role], "discord", db_path=temp_db)
    assert len(unalerted_dc) == 1
    assert unalerted_dc[0].id == "custom_katalon_new_12345"

    # 4. In dispatch_notifications, only the new role is sent
    with patch("gcc_job_radar.notifier.send_telegram_notification", new_callable=AsyncMock) as mock_send_tg:
        mock_send_tg.return_value = True
        await dispatch_notifications(
            new_jobs=[katalon_role, gallagher_role, katalon_new_role],
            telegram_token="dummy_token",
            telegram_chat_id="12345",
            db_path=temp_db,
        )
        assert mock_send_tg.called
        dispatched_jobs = mock_send_tg.call_args[0][2]
        assert len(dispatched_jobs) == 1
        assert dispatched_jobs[0].title == "Junior Backend Developer"


