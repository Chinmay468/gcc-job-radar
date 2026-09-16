"""Unit tests for interactive Telegram bot listener."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
import httpx
import pytest

from gcc_job_radar.bot_listener import (
    build_job_inline_keyboard,
    format_jobs_html,
    handle_callback_query,
    handle_command,
)
from gcc_job_radar.db import get_job_by_id, init_db, mark_job_status, record_jobs
from gcc_job_radar.models import ATSProvider, JobPosting
from gcc_job_radar.apply_prep import FormPrepResult


@pytest.fixture
def sample_jobs() -> list[JobPosting]:
    return [
        JobPosting(
            id="test-101",
            company="Celonis",
            title="Associate Software Engineer - Java",
            location="Bangalore, India",
            apply_url="https://job-boards.greenhouse.io/celonis/jobs/7791267003",
            published_date="2026-08-25",
            provider=ATSProvider.GREENHOUSE,
        )
    ]


@pytest.mark.asyncio
async def test_unauthorized_user() -> None:
    """Verify unauthorized users receive access denied response."""
    captured_messages = []

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content.decode("utf-8"))
        captured_messages.append(data)
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await handle_command(
            command_text="/scan",
            chat_id="999999",  # Unauthorized
            bot_token="test_token",
            allowed_chat_id="123456",  # Authorized
            client=client,
        )

        assert len(captured_messages) == 1
        assert "Access Denied" in captured_messages[0]["text"]
        assert captured_messages[0]["chat_id"] == "999999"


@pytest.mark.asyncio
async def test_help_command() -> None:
    """Verify /help returns available commands."""
    captured_messages = []

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content.decode("utf-8"))
        captured_messages.append(data)
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await handle_command(
            command_text="/help",
            chat_id="123456",
            bot_token="test_token",
            allowed_chat_id="123456",
            client=client,
        )

        assert len(captured_messages) == 1
        text = captured_messages[0]["text"]
        assert "/scan" in text
        assert "/check" in text
        assert "/stats" in text
        assert "/latest" in text
        assert "/clear" in text
        assert "/list" in text



@pytest.mark.asyncio
async def test_stats_command(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify /stats formats historical metrics."""
    db_file = tmp_path / "bot_stats.db"
    init_db(db_file)
    record_jobs(sample_jobs, db_file)

    captured_messages = []

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content.decode("utf-8"))
        captured_messages.append(data)
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await handle_command(
            command_text="/stats",
            chat_id="123456",
            bot_token="test_token",
            allowed_chat_id="123456",
            client=client,
            db_path=db_file,
        )

        assert len(captured_messages) == 1
        text = captured_messages[0]["text"]
        assert "Total Roles Tracked:</b> 1" in text
        assert "Celonis" in text


@pytest.mark.asyncio
async def test_latest_command(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify /latest returns recently recorded jobs."""
    db_file = tmp_path / "bot_latest.db"
    init_db(db_file)
    record_jobs(sample_jobs, db_file)

    captured_messages = []

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content.decode("utf-8"))
        captured_messages.append(data)
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await handle_command(
            command_text="/latest",
            chat_id="123456",
            bot_token="test_token",
            allowed_chat_id="123456",
            client=client,
            db_path=db_file,
        )

        assert len(captured_messages) == 1
        text = captured_messages[0]["text"]
        assert "Celonis" in text
        assert "Associate Software Engineer - Java" in text
        assert "href=\"https://job-boards.greenhouse.io/celonis/jobs/7791267003\"" in text


@pytest.mark.asyncio
async def test_check_command(sample_jobs: list[JobPosting]) -> None:
    """Verify /check company triggers scan and replies with results."""
    captured_messages = []

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content.decode("utf-8"))
        captured_messages.append(data)
        return httpx.Response(200, json={"ok": True})

    async def mock_scan(*args, **kwargs):
        return sample_jobs

    with patch("gcc_job_radar.bot_listener.scan_all_companies", side_effect=mock_scan):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await handle_command(
                command_text="/check celonis",
                chat_id="123456",
                bot_token="test_token",
                allowed_chat_id="123456",
                client=client,
            )

            assert len(captured_messages) == 2
            assert "Scanning" in captured_messages[0]["text"]
            assert "Celonis" in captured_messages[1]["text"]
            assert "Associate Software Engineer" in captured_messages[1]["text"]


@pytest.mark.asyncio
async def test_scan_debounce(sample_jobs: list[JobPosting]) -> None:
    """Verify /scan debounces immediate re-executions to avoid duplicate scans."""
    captured_messages = []

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content.decode("utf-8"))
        captured_messages.append(data)
        return httpx.Response(200, json={"ok": True})

    async def mock_scan(*args, **kwargs):
        return sample_jobs

    import gcc_job_radar.bot_listener as bl
    bl._last_scan_timestamp = 0.0
    bl._is_scanning = False

    with patch("gcc_job_radar.bot_listener.scan_all_companies", side_effect=mock_scan):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            # First scan should succeed
            await handle_command("/scan", "123456", "token", "123456", client)
            # Second scan immediately after should trigger debounce
            await handle_command("/scan", "123456", "token", "123456", client)

            # Check that debounce message was sent
            assert any("just completed seconds ago" in msg["text"] for msg in captured_messages)


@pytest.mark.asyncio
async def test_clear_command() -> None:
    """Verify /clear resets chat history and replies."""
    captured_messages = []

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content.decode("utf-8"))
        captured_messages.append(data)
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await handle_command(
            command_text="/clear",
            chat_id="123456",
            bot_token="test_token",
            allowed_chat_id="123456",
            client=client,
        )

        assert len(captured_messages) == 1
        text = captured_messages[0]["text"]
        assert "Chat history cleared" in text


def test_build_job_inline_keyboard_standard(sample_jobs: list[JobPosting]) -> None:
    """Verify standard inline keyboard contains Apply, Dismiss, and Applied buttons."""
    job = sample_jobs[0]
    markup = build_job_inline_keyboard(job)

    assert "inline_keyboard" in markup
    rows = markup["inline_keyboard"]
    assert len(rows) == 2

    # Row 1: Apply URL button
    assert len(rows[0]) == 1
    assert rows[0][0]["text"] == "Apply"
    assert rows[0][0]["url"] == "https://job-boards.greenhouse.io/celonis/jobs/7791267003"

    # Row 2: Dismiss and Applied callback buttons
    assert len(rows[1]) == 2
    assert rows[1][0]["text"] == "Dismiss"
    assert rows[1][0]["callback_data"] == f"dismiss:{job.id}"
    assert rows[1][1]["text"] == "Applied"
    assert rows[1][1]["callback_data"] == f"applied:{job.id}"


def test_build_job_inline_keyboard_best_fit(sample_jobs: list[JobPosting]) -> None:
    """Verify ⭐ Best Fit jobs include 'Prep Application' button in inline keyboard."""
    job = sample_jobs[0]
    # Test via tier attribute
    job.tier = "⭐ Best Fit"
    markup = build_job_inline_keyboard(job)
    rows = markup["inline_keyboard"]
    assert len(rows[1]) == 3
    assert rows[1][0]["text"] == "Prep Application"
    assert rows[1][0]["callback_data"] == f"prep_apply:{job.id}"
    assert rows[1][1]["text"] == "Dismiss"
    assert rows[1][2]["text"] == "Applied"

    # Test via explicit is_best_fit=True
    markup2 = build_job_inline_keyboard(job, is_best_fit=True)
    assert markup2["inline_keyboard"][1][0]["text"] == "Prep Application"

    # Test non-best fit does not include it
    job.tier = "⚡ Strong Fit"
    markup3 = build_job_inline_keyboard(job)
    assert len(markup3["inline_keyboard"][1]) == 2


def test_build_job_inline_keyboard_with_search_url_or_needs_resolve() -> None:
    """Verify conditional 'Search Direct ATS' button is added when direct_search_url or NEEDS_RESOLVE present."""
    job_with_search = JobPosting(
        id="test-resolve",
        company="BT Group",
        title="Associate Engineer",
        location="Bengaluru",
        apply_url="https://jobs.bt.com/123",
        provider=ATSProvider.EMAIL_ALERT,
        status="NEEDS_RESOLVE",
        direct_search_url="https://jobs.bt.com/search/?q=123",
    )
    markup = build_job_inline_keyboard(job_with_search)
    rows = markup["inline_keyboard"]

    # Row 1 has both Apply and Search Direct ATS
    assert len(rows[0]) == 2
    assert rows[0][0]["text"] == "Apply"
    assert rows[0][1]["text"] == "Search Direct ATS"
    assert rows[0][1]["url"] == "https://jobs.bt.com/search/?q=123"


@pytest.mark.asyncio
async def test_callback_query_dismiss(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify dismiss callback updates DB status to DISMISSED, edits message, and answers query."""
    db_file = tmp_path / "bot_cb_dismiss.db"
    init_db(db_file)
    record_jobs(sample_jobs, db_file)

    job = get_job_by_id(sample_jobs[0].id, db_path=db_file)
    assert job["status"] == "NEW"
    rowid = job["numeric_id"]

    captured_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        data = json.loads(request.content.decode("utf-8"))
        captured_requests.append({"url": str(request.url), "data": data})
        return httpx.Response(200, json={"ok": True})

    callback_query = {
        "id": "cb_query_999",
        "from": {"id": 123456},
        "data": f"dismiss:{rowid}",
        "message": {
            "message_id": 42,
            "chat": {"id": 123456},
            "text": "🚀 Celonis\n💼 Associate Software Engineer",
        },
    }

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await handle_callback_query(
            callback_query=callback_query,
            bot_token="test_token",
            allowed_chat_id="123456",
            client=client,
            db_path=db_file,
        )

        assert result is True

        # Verify state change in seen_jobs
        updated_job = get_job_by_id(rowid, db_path=db_file)
        assert updated_job["status"] == "DISMISSED"

        # Verify Telegram Bot API calls (editMessageText and answerCallbackQuery)
        urls = [req["url"] for req in captured_requests]
        assert any("editMessageText" in u for u in urls)
        assert any("answerCallbackQuery" in u for u in urls)

        edit_call = next(req for req in captured_requests if "editMessageText" in req["url"])
        assert "[DISMISSED]" in edit_call["data"]["text"]
        assert "<s>" in edit_call["data"]["text"]
        assert edit_call["data"]["message_id"] == 42

        answer_call = next(req for req in captured_requests if "answerCallbackQuery" in req["url"])
        assert answer_call["data"]["callback_query_id"] == "cb_query_999"
        assert "Dismissed" in answer_call["data"]["text"]


@pytest.mark.asyncio
async def test_callback_query_applied(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify applied callback updates DB status to APPLIED, records timestamp, and edits message."""
    db_file = tmp_path / "bot_cb_applied.db"
    init_db(db_file)
    record_jobs(sample_jobs, db_file)

    job = get_job_by_id(sample_jobs[0].id, db_path=db_file)
    assert job["status"] == "NEW"
    rowid = job["numeric_id"]

    captured_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        data = json.loads(request.content.decode("utf-8"))
        captured_requests.append({"url": str(request.url), "data": data})
        return httpx.Response(200, json={"ok": True})

    callback_query = {
        "id": "cb_query_888",
        "from": {"id": 123456},
        "data": f"applied:{rowid}",
        "message": {
            "message_id": 43,
            "chat": {"id": 123456},
            "text": "🚀 Celonis\n💼 Associate Software Engineer",
        },
    }

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await handle_callback_query(
            callback_query=callback_query,
            bot_token="test_token",
            allowed_chat_id="123456",
            client=client,
            db_path=db_file,
        )

        assert result is True

        # Verify state change in seen_jobs
        updated_job = get_job_by_id(rowid, db_path=db_file)
        assert updated_job["status"] == "APPLIED"
        assert updated_job["applied_at"] is not None

        # Verify Telegram Bot API calls
        edit_call = next(req for req in captured_requests if "editMessageText" in req["url"])
        assert "[APPLIED]" in edit_call["data"]["text"]
        assert edit_call["data"]["message_id"] == 43

        answer_call = next(req for req in captured_requests if "answerCallbackQuery" in req["url"])
        assert answer_call["data"]["callback_query_id"] == "cb_query_888"
        assert "Applied" in answer_call["data"]["text"]


@pytest.mark.asyncio
async def test_callback_query_prep_apply(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify prep_apply callback query triggers tailoring, form prep, and replies with summary."""
    db_file = tmp_path / "bot_cb_prep_apply.db"
    init_db(db_file)
    record_jobs(sample_jobs, db_file)

    job = get_job_by_id(sample_jobs[0].id, db_path=db_file)
    rowid = job["numeric_id"]

    captured_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content.decode("utf-8")) if request.content else {}
        captured_requests.append({"url": str(request.url), "data": data})
        return httpx.Response(200, json={"ok": True})

    callback_query = {
        "id": "cb_query_777",
        "from": {"id": 123456},
        "data": f"prep_apply:{rowid}",
        "message": {
            "message_id": 45,
            "chat": {"id": 123456},
            "text": "🚀 Celonis\n💼 Associate Software Engineer",
        },
    }

    mock_prep_result = FormPrepResult(
        job_id=str(rowid),
        company="Celonis",
        title="Associate Software Engineer",
        apply_url="https://job-boards.greenhouse.io/celonis/jobs/7791267003",
        status="READY_LOCAL",
        prefilled_fields=["Full Name: Chinmay Maheshwari", "Email: chinmay@example.com"],
        drafted_questions=[{"question": "Why Celonis?", "answer": "I love process mining.", "is_ai": True}],
        manual_fields=[],
    )

    with patch("gcc_job_radar.bot_listener.tailor_resume_for_job", return_value=(None, None)), \
         patch("gcc_job_radar.bot_listener.prep_job_application", new_callable=AsyncMock, return_value=mock_prep_result):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await handle_callback_query(
                callback_query=callback_query,
                bot_token="test_token",
                allowed_chat_id="123456",
                client=client,
                db_path=db_file,
            )

            assert result is True

            # Check answerCallbackQuery was called
            answer_call = next(req for req in captured_requests if "answerCallbackQuery" in req["url"])
            assert answer_call["data"]["callback_query_id"] == "cb_query_777"

            # Check sendMessage summary was dispatched
            send_call = next(req for req in captured_requests if "sendMessage" in req["url"])
            assert "Celonis" in send_call["data"]["text"]
            assert "Pre-filled Fields" in send_call["data"]["text"]
            assert "AI-Drafted" in send_call["data"]["text"]
            reply_markup = send_call["data"]["reply_markup"]
            assert any(btn["callback_data"] == f"applied:{rowid}" for row in reply_markup["inline_keyboard"] for btn in row)


@pytest.mark.asyncio
async def test_callback_query_unauthorized(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify unauthorized user callback does not modify database."""
    db_file = tmp_path / "bot_cb_unauth.db"
    init_db(db_file)
    record_jobs(sample_jobs, db_file)

    captured_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        data = json.loads(request.content.decode("utf-8"))
        captured_requests.append({"url": str(request.url), "data": data})
        return httpx.Response(200, json={"ok": True})

    callback_query = {
        "id": "cb_query_unauth",
        "from": {"id": 999999},  # Unauthorized
        "data": "dismiss:1",
        "message": {
            "message_id": 44,
            "chat": {"id": 999999},
            "text": "Some text",
        },
    }

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await handle_callback_query(
            callback_query=callback_query,
            bot_token="test_token",
            allowed_chat_id="123456",  # Authorized is 123456
            client=client,
            db_path=db_file,
        )

        assert result is False

        # Verify DB was NOT modified
        job = get_job_by_id(1, db_path=db_file)
        assert job["status"] == "NEW"

        # Verify rejection alert answered
        answer_call = next(req for req in captured_requests if "answerCallbackQuery" in req["url"])
        assert "Access Denied" in answer_call["data"]["text"]


@pytest.mark.asyncio
async def test_stats_command_reports_active_needs_resolve_dismissed(tmp_path: Path) -> None:
    """Verify /stats reports breakdown of active, needs_resolve, applied, and dismissed jobs."""
    db_file = tmp_path / "bot_stats_breakdown.db"
    init_db(db_file)

    jobs = [
        JobPosting(
            id="j1",
            company="Alpha",
            title="SWE 1",
            location="Remote",
            apply_url="https://alpha.com/1",
            provider=ATSProvider.GREENHOUSE,
            status="NEW",
        ),
        JobPosting(
            id="j2",
            company="Beta",
            title="SWE 2",
            location="Bangalore",
            apply_url="https://beta.com/2",
            provider=ATSProvider.LEVER,
            status="NEEDS_RESOLVE",
        ),
        JobPosting(
            id="j3",
            company="Gamma",
            title="SWE 3",
            location="Pune",
            apply_url="https://gamma.com/3",
            provider=ATSProvider.ASHBY,
            status="DISMISSED",
        ),
        JobPosting(
            id="j4",
            company="Delta",
            title="SWE 4",
            location="Noida",
            apply_url="https://delta.com/4",
            provider=ATSProvider.SMARTRECRUITERS,
            status="APPLIED",
        ),
    ]
    record_jobs(jobs, db_file)
    mark_job_status("j2", "NEEDS_RESOLVE", db_path=db_file)
    mark_job_status("j3", "DISMISSED", db_path=db_file)
    mark_job_status("j4", "APPLIED", db_path=db_file)

    captured_messages = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        data = json.loads(request.content.decode("utf-8"))
        captured_messages.append(data)
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await handle_command(
            command_text="/stats",
            chat_id="123456",
            bot_token="test_token",
            allowed_chat_id="123456",
            client=client,
            db_path=db_file,
        )

        assert len(captured_messages) == 1
        text = captured_messages[0]["text"]
        assert "Total Roles Tracked:</b> 4" in text
        assert "Active (New):</b> 1" in text
        assert "Needs Resolve:</b> 1" in text
        assert "Applied:</b> 1" in text
        assert "Dismissed:</b> 1" in text


@pytest.mark.asyncio
async def test_latest_command_with_inline_keyboard(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify /latest attaches interactive inline keyboard with Apply, Dismiss, Applied buttons."""
    db_file = tmp_path / "bot_latest_kb.db"
    init_db(db_file)
    record_jobs(sample_jobs, db_file)

    captured_messages = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        data = json.loads(request.content.decode("utf-8"))
        captured_messages.append(data)
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await handle_command(
            command_text="/latest",
            chat_id="123456",
            bot_token="test_token",
            allowed_chat_id="123456",
            client=client,
            db_path=db_file,
        )

        assert len(captured_messages) == 1
        msg = captured_messages[0]
        assert "reply_markup" in msg
        kb = msg["reply_markup"]["inline_keyboard"]
        assert len(kb) == 2
        assert kb[0][0]["text"] == "Apply"
        assert kb[1][0]["text"] == "Dismiss"
        assert kb[1][1]["text"] == "Applied"


def test_split_telegram_message() -> None:
    """Verify split_telegram_message splits long texts cleanly without exceeding limits."""
    from gcc_job_radar.bot_listener import split_telegram_message

    # 1. Empty & short text
    assert split_telegram_message("") == [""]
    short_text = "Hello world! Short message."
    assert split_telegram_message(short_text, max_length=100) == [short_text]

    # 2. Paragraph splitting (\n\n)
    para1 = "A" * 60
    para2 = "B" * 60
    combined = f"{para1}\n\n{para2}"
    chunks = split_telegram_message(combined, max_length=70)
    assert len(chunks) == 2
    assert chunks[0] == para1
    assert chunks[1] == para2

    # 3. Line splitting (\n) when paragraph is larger than max_length
    line1 = "C" * 40
    line2 = "D" * 40
    long_para = f"{line1}\n{line2}"
    chunks2 = split_telegram_message(long_para, max_length=50)
    assert len(chunks2) == 2
    assert chunks2[0] == line1
    assert chunks2[1] == line2

    # 4. Hard slicing when single line has no newlines
    giant_line = "E" * 120
    chunks3 = split_telegram_message(giant_line, max_length=50)
    assert len(chunks3) == 3
    assert len(chunks3[0]) == 50
    assert len(chunks3[1]) == 50
    assert len(chunks3[2]) == 20
    assert "".join(chunks3) == giant_line


@pytest.mark.asyncio
async def test_send_telegram_reply_chunking_and_markup() -> None:
    """Verify send_telegram_reply sends multi-chunk messages and attaches reply_markup only to the last chunk."""
    from gcc_job_radar.bot_listener import send_telegram_reply

    sent_payloads = []

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content.decode("utf-8"))
        sent_payloads.append(data)
        return httpx.Response(200, json={"ok": True})

    card1 = "🏢 <b>Company 1</b>\n💼 Role 1\n📍 City 1\n" + ("X" * 2500)
    card2 = "🏢 <b>Company 2</b>\n💼 Role 2\n📍 City 2\n" + ("Y" * 2500)
    long_text = f"{card1}\n\n{card2}"

    fake_markup = {"inline_keyboard": [[{"text": "Button", "url": "https://example.com"}]]}

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ok = await send_telegram_reply(
            bot_token="test_token",
            chat_id=12345,
            text=long_text,
            client=client,
            reply_markup=fake_markup,
        )

        assert ok is True
        assert len(sent_payloads) == 2
        # First chunk has no reply_markup
        assert "reply_markup" not in sent_payloads[0]
        # Final chunk has the reply_markup
        assert sent_payloads[1].get("reply_markup") == fake_markup
        # Both chunks under 3950 chars
        assert len(sent_payloads[0]["text"]) <= 3950
        assert len(sent_payloads[1]["text"]) <= 3950


@pytest.mark.asyncio
async def test_send_telegram_reply_html_fallback() -> None:
    """Verify send_telegram_reply retries without HTML formatting if Telegram returns 400 Bad Request."""
    from gcc_job_radar.bot_listener import send_telegram_reply

    call_count = 0
    sent_payloads = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        data = json.loads(request.content.decode("utf-8"))
        sent_payloads.append(data)
        # Fail first attempt (HTML parsing error)
        if data.get("parse_mode") == "HTML":
            return httpx.Response(400, json={"ok": False, "description": "Bad Request: can't parse entities"})
        # Succeed on retry without parse_mode
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ok = await send_telegram_reply(
            bot_token="test_token",
            chat_id=12345,
            text="<b>Malformed <i>HTML</b>",
            client=client,
        )

        assert ok is True
        assert call_count == 2
        assert sent_payloads[0].get("parse_mode") == "HTML"
        assert "parse_mode" not in sent_payloads[1]
        assert "<b>" not in sent_payloads[1]["text"]


@pytest.mark.asyncio
async def test_send_telegram_reply_sanitizes_unbalanced_tags() -> None:
    """Verify send_telegram_reply balances tags before posting to Telegram."""
    from gcc_job_radar.bot_listener import send_telegram_reply

    sent_payloads = []

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content.decode("utf-8"))
        sent_payloads.append(data)
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ok = await send_telegram_reply(
            bot_token="test_token",
            chat_id=12345,
            text="<b>Malformed <i>HTML</b>",
            client=client,
        )

        assert ok is True
        assert len(sent_payloads) == 1
        assert sent_payloads[0]["text"] == "<b>Malformed <i>HTML</i></b>"


@pytest.mark.asyncio
async def test_dismissed_command(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify /dismissed formats all dismissed roles and companies."""
    db_file = tmp_path / "bot_dismissed.db"
    init_db(db_file)
    record_jobs(sample_jobs, db_file)
    mark_job_status("test-101", status="DISMISSED", db_path=db_file)

    captured_messages = []

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content.decode("utf-8"))
        captured_messages.append(data)
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await handle_command(
            command_text="/dismissed",
            chat_id="123456",
            bot_token="test_token",
            allowed_chat_id="123456",
            client=client,
            db_path=db_file,
        )

        assert len(captured_messages) == 1
        text = captured_messages[0]["text"]
        assert "Dismissed Roles" in text
        assert "Celonis" in text


@pytest.mark.asyncio
async def test_email_command(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify /email command triggers email sync and sends formatted response."""
    db_file = tmp_path / "bot_email.db"
    init_db(db_file)

    captured_messages = []

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content.decode("utf-8"))
        captured_messages.append(data)
        return httpx.Response(200, json={"ok": True})

    with patch("tools.ingest_email.sync_email_alerts", return_value=[sample_jobs[0]]), \
         patch("tools.ingest_email.get_configured_email_accounts", return_value=[("test@gmail.com", "pass")]):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await handle_command(
                command_text="/email",
                chat_id="123456",
                bot_token="test_token",
                allowed_chat_id="123456",
                client=client,
                db_path=db_file,
            )

            assert len(captured_messages) >= 2
            # First message is scanning notification
            assert "Scanning your 1 configured email account" in captured_messages[0]["text"]
            assert "New Email Job Alerts" in captured_messages[1]["text"]
            assert "Celonis" in captured_messages[1]["text"]


@pytest.mark.asyncio
async def test_email_command_missing_credentials(tmp_path: Path) -> None:
    """Verify /email command handles missing credentials gracefully with Render instructions."""
    from tools.ingest_email import MissingEmailCredentialsError

    db_file = tmp_path / "bot_email_missing.db"
    init_db(db_file)

    captured_messages = []

    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content.decode("utf-8"))
        captured_messages.append(data)
        return httpx.Response(200, json={"ok": True})

    with patch("tools.ingest_email.get_configured_email_accounts", side_effect=MissingEmailCredentialsError("No credentials")):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await handle_command(
                command_text="/email",
                chat_id="123456",
                bot_token="test_token",
                allowed_chat_id="123456",
                client=client,
                db_path=db_file,
            )

            assert len(captured_messages) == 1
            assert "Missing Credentials on Render" in captured_messages[0]["text"]
            assert "EMAIL_USER" in captured_messages[0]["text"]


@pytest.mark.asyncio
async def test_tailor_command(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify /tailor command parses selector and invokes resume tailoring."""
    db_file = tmp_path / "bot_tailor.db"
    init_db(db_file)
    record_jobs([sample_jobs[0]], db_path=db_file)

    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"ok": True})

    with patch("gcc_job_radar.bot_listener.tailor_resume_for_job", return_value=("tailored/Celonis.tex", None)):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            # 1. Test without arguments -> usage
            await handle_command(
                command_text="/tailor",
                chat_id="123456",
                bot_token="test_token",
                allowed_chat_id="123456",
                client=client,
                db_path=db_file,
            )
            assert "Usage:" in captured[-1].read().decode("utf-8")

            # 2. Test with matching company -> triggers tailoring
            await handle_command(
                command_text="/tailor Celonis",
                chat_id="123456",
                bot_token="test_token",
                allowed_chat_id="123456",
                client=client,
                db_path=db_file,
            )
            assert len(captured) >= 3
            body = captured[-1].read().decode("utf-8")
            assert "Tailored Resume Generated" in body
            assert "Celonis.tex" in body


@pytest.mark.asyncio
async def test_run_bot_listener_409_conflict_handling() -> None:
    """Verify run_bot_listener handles 409 Conflict gracefully and logs appropriately."""
    calls = 0

    class MockAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def get(self, url, params=None):
            nonlocal calls
            calls += 1
            return httpx.Response(409, text='{"ok":false,"error_code":409,"description":"Conflict"}')

        async def post(self, url, json=None, files=None, data=None, timeout=None):
            return httpx.Response(200, json={"ok": True, "result": True})

    mock_sleep = AsyncMock()
    with patch("gcc_job_radar.bot_listener.httpx.AsyncClient", return_value=MockAsyncClient()):
        from gcc_job_radar.bot_listener import run_bot_listener
        await run_bot_listener(
            bot_token="test_token",
            allowed_chat_id="123456",
            max_iterations=1,
            sleep_func=mock_sleep,
        )
        mock_sleep.assert_called_with(5)


@pytest.mark.asyncio
async def test_sync_telegram_bot_commands() -> None:
    """Verify sync_telegram_bot_commands sends correct payload to setMyCommands."""
    captured_payload = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_payload
        if "setMyCommands" in str(request.url):
            captured_payload = json.loads(request.content.decode("utf-8"))
            return httpx.Response(200, json={"ok": True, "result": True})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        from gcc_job_radar.bot_listener import sync_telegram_bot_commands
        ok = await sync_telegram_bot_commands(bot_token="test_token", client=client)
        assert ok is True
        assert captured_payload is not None
        cmds = captured_payload.get("commands", [])
        assert len(cmds) == 8
        cmd_names = [c["command"] for c in cmds]
        assert "scan" in cmd_names
        assert "latest" in cmd_names
        assert "email" in cmd_names
        assert "tailor" in cmd_names
        assert "applied" in cmd_names
        assert "followups" in cmd_names
        assert "stats" in cmd_names
        assert "help" in cmd_names










