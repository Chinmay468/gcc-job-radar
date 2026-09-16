"""Unit tests for the conversational AI agent and tool calling integration."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
import httpx
import pytest

from gcc_job_radar.ai_agent import (
    ChatHistoryManager,
    ask_ai_agent,
    clear_chat_history,
    convert_markdown_tables_to_cards,
    execute_tool,
    format_jobs_html,
    markdown_to_telegram_html,
)
from gcc_job_radar.db import init_db, mark_job_status, query_jobs, record_jobs
from gcc_job_radar.models import ATSProvider, JobPosting


@pytest.fixture
def sample_jobs() -> list[JobPosting]:
    return [
        JobPosting(
            id="job-python-01",
            company="Celonis",
            title="Associate Python Engineer",
            location="Bengaluru, Karnataka, India",
            apply_url="https://job-boards.greenhouse.io/celonis/jobs/101",
            published_date="2026-08-25",
            provider=ATSProvider.GREENHOUSE,
        ),
        JobPosting(
            id="job-java-02",
            company="Databricks",
            title="Graduate Software Engineer - Java",
            location="Hyderabad, Telangana, India",
            apply_url="https://job-boards.greenhouse.io/databricks/jobs/102",
            published_date="2026-08-26",
            provider=ATSProvider.GREENHOUSE,
        ),
        JobPosting(
            id="job-pune-03",
            company="Snowflake",
            title="Software QA Engineer 1",
            location="Pune, Maharashtra, India",
            apply_url="https://job-boards.greenhouse.io/snowflake/jobs/103",
            published_date="2026-08-27",
            provider=ATSProvider.GREENHOUSE,
        ),
    ]


# 1. Database Querying Tests


def test_query_jobs_filters(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify db.query_jobs filters correctly by company, title, and location."""
    db_file = tmp_path / "test_query.db"
    init_db(db_file)
    record_jobs(sample_jobs, db_file)

    # Filter by company
    celonis_jobs = query_jobs(company="Celonis", db_path=db_file)
    assert len(celonis_jobs) == 1
    assert celonis_jobs[0]["company"] == "Celonis"

    # Filter by title keyword
    python_jobs = query_jobs(title_keyword="python", db_path=db_file)
    assert len(python_jobs) == 1
    assert "Python" in python_jobs[0]["title"]

    # Filter by location
    pune_jobs = query_jobs(location="Pune", db_path=db_file)
    assert len(pune_jobs) == 1
    assert "Pune" in pune_jobs[0]["location"]

    # Filter by combined parameters
    none_jobs = query_jobs(company="Celonis", location="Pune", db_path=db_file)
    assert len(none_jobs) == 0

    # Test limit
    all_jobs = query_jobs(limit=2, db_path=db_file)
    assert len(all_jobs) == 2


# 2. History Manager Tests


def test_chat_history_manager() -> None:
    """Verify conversational memory tracks, truncates, and clears messages."""
    mgr = ChatHistoryManager(max_turns=3)
    chat_id = "test-chat-123"

    mgr.add_turn(chat_id, "user", "Hello")
    mgr.add_turn(chat_id, "assistant", "Hi there!")
    history = mgr.get_history(chat_id)
    assert len(history) == 2
    assert history[0]["content"] == "Hello"
    assert history[1]["content"] == "Hi there!"

    # Test clearing
    mgr.clear(chat_id)
    assert len(mgr.get_history(chat_id)) == 0


# 3. Formatting Tests


def test_markdown_to_telegram_html() -> None:
    """Verify markdown tags convert to safe Telegram HTML."""
    md = "Hello **world** with *italics* and `code` and [link](https://example.com)"
    res = markdown_to_telegram_html(md)
    assert "<b>world</b>" in res
    assert "<i>italics</i>" in res
    assert "<code>code</code>" in res
    assert '<a href="https://example.com">link</a>' in res

    # Verify HTML escaping for raw angled brackets
    raw = "If 5 < 10 & 10 > 2 then **valid**"
    res2 = markdown_to_telegram_html(raw)
    assert "&lt;" in res2
    assert "&amp;" in res2
    assert "<b>valid</b>" in res2


def test_markdown_table_to_cards_conversion() -> None:
    """Verify markdown tables (like those returned by LLMs) convert to mobile-friendly cards."""
    table_md = (
        "Here is the latest opening:\n\n"
        "| Company | Role | Location | Posted |\n"
        "|---------|------|----------|---------|\n"
        "| Celonis | Associate Software Engineer – Java | Bangalore, India | 25 Aug 2026 |\n\n"
        "[Apply here](https://job-boards.greenhouse.io/celonis/jobs/7791267003)"
    )
    html_res = markdown_to_telegram_html(table_md)
    # Ensure raw table pipes were converted away
    assert "| Company |" not in html_res
    assert "|---------" not in html_res
    assert "🏢 <b>Celonis</b>" in html_res
    assert "💼 Associate Software Engineer" in html_res
    assert "📍 Bangalore, India • 📅 25 Aug 2026" in html_res
    assert '<a href="https://job-boards.greenhouse.io/celonis/jobs/7791267003">Apply here</a>' in html_res


def test_markdown_table_with_inline_links() -> None:
    """Verify markdown tables containing apply URLs convert directly to cards with action links."""
    table_md = (
        "| Company | Role | Location | Apply |\n"
        "|---|---|---|---|\n"
        "| Databricks | Software Engineer 1 | Bangalore | https://databricks.com/jobs/101 |\n"
    )
    html_res = markdown_to_telegram_html(table_md)
    assert "🏢 <b>Databricks</b>" in html_res
    assert "💼 Software Engineer 1" in html_res
    assert '<a href="https://databricks.com/jobs/101">Apply on ATS</a>' in html_res


def test_markdown_bullets_and_italics_safe() -> None:
    """Verify asterisk bullets do not accidentally close or mangle subsequent italics."""
    md = "* **Associate Software Engineer - Java** at **Celonis** (Bangalore, India) — *Published Aug 25, 2026*"
    html_res = markdown_to_telegram_html(md)
    assert html_res.startswith("• ")
    assert "<b>Associate Software Engineer - Java</b>" in html_res
    assert "<b>Celonis</b>" in html_res
    assert "<i>Published Aug 25, 2026</i>" in html_res
    assert "</i>Published" not in html_res


def test_format_jobs_html(sample_jobs: list[JobPosting]) -> None:
    """Verify job listing formatter generates readable HTML with URLs."""
    job_dicts = [
        {
            "company": j.company,
            "title": j.title,
            "location": j.location,
            "apply_url": str(j.apply_url),
            "published_date": j.published_date,
        }
        for j in sample_jobs
    ]
    res = format_jobs_html(job_dicts, "Test Results")
    assert "Test Results (3)" in res
    assert "Celonis" in res
    assert "Databricks" in res
    assert "href=\"https://job-boards.greenhouse.io/celonis/jobs/101\"" in res


# 4. Tool Execution Tests


@pytest.mark.asyncio
async def test_execute_tool(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify tool execution router executes db queries, stats, and live checks."""
    db_file = tmp_path / "test_exec_tools.db"
    init_db(db_file)
    record_jobs(sample_jobs, db_file)

    # Test query_jobs tool
    q_res = await execute_tool("query_jobs", {"title_keyword": "Java"}, db_path=db_file)
    assert q_res["status"] == "success"
    assert q_res["count"] == 1
    assert "Databricks" in q_res["jobs"][0]["company"]

    # Test get_tracking_stats tool
    s_res = await execute_tool("get_tracking_stats", {}, db_path=db_file)
    assert s_res["status"] == "success"
    assert s_res["stats"]["total_tracked"] == 3

    # Test check_company_live tool with mock
    async def mock_scan(*args, **kwargs):
        return [sample_jobs[0]]

    with patch("gcc_job_radar.ai_agent.scan_all_companies", side_effect=mock_scan):
        c_res = await execute_tool("check_company_live", {"company_name": "Celonis"}, db_path=db_file)
        assert c_res["status"] == "success"
        assert c_res["count"] == 1

    # Test get_configured_companies tool
    comp_res = await execute_tool("get_configured_companies", {}, db_path=db_file)
    assert comp_res["status"] == "success"
    assert comp_res["count"] > 0
    assert any(c["name"] == "Celonis" for c in comp_res["companies"])

    # Test get_applied_jobs tool
    from gcc_job_radar.db import mark_job_status
    mark_job_status(1, "APPLIED", notes="Applied on Celonis", db_path=db_file)

    app_res = await execute_tool("get_applied_jobs", {}, db_path=db_file)
    assert app_res["status"] == "success"
    assert app_res["count"] == 1
    assert app_res["jobs"][0]["company"] == "Celonis"
    assert app_res["jobs"][0]["status"] == "APPLIED"

    # Test query_jobs with status="APPLIED"
    q_applied = await execute_tool("query_jobs", {"status": "APPLIED"}, db_path=db_file)
    assert q_applied["status"] == "success"
    assert q_applied["count"] == 1
    assert q_applied["jobs"][0]["company"] == "Celonis"



# 5. Fallback Mode Tests (No API Keys Configured)


@pytest.mark.asyncio
async def test_ask_ai_agent_fallback_greeting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify greeting triggers assistant description when no LLM key is set."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    reply = await ask_ai_agent("Hello, what can you do?", chat_id="chat-1")
    assert "GCC Job Radar Assistant" in reply
    assert "/scan" in reply


@pytest.mark.asyncio
async def test_ask_ai_agent_fallback_search(
    tmp_path: Path, sample_jobs: list[JobPosting], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify natural query finds jobs in database via keyword extraction."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    db_file = tmp_path / "fallback_search.db"
    init_db(db_file)
    record_jobs(sample_jobs, db_file)

    reply = await ask_ai_agent("Are there any python roles in Bangalore?", chat_id="chat-2", db_path=db_file)
    assert "Celonis" in reply
    assert "Associate Python Engineer" in reply
    assert "Apply on ATS" in reply


@pytest.mark.asyncio
async def test_ask_ai_agent_fallback_stats(
    tmp_path: Path, sample_jobs: list[JobPosting], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify natural query for stats reports totals."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    db_file = tmp_path / "fallback_stats.db"
    init_db(db_file)
    record_jobs(sample_jobs, db_file)

    reply = await ask_ai_agent("How many total jobs are in the database?", chat_id="chat-3", db_path=db_file)
    assert "Total Roles Tracked:</b> 3" in reply


@pytest.mark.asyncio
async def test_ask_ai_agent_fallback_live_check(
    tmp_path: Path, sample_jobs: list[JobPosting], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify live check intent triggers ATS scan and replies."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    db_file = tmp_path / "fallback_live.db"
    init_db(db_file)

    async def mock_scan(*args, **kwargs):
        return [sample_jobs[0]]

    with patch("gcc_job_radar.ai_agent.scan_all_companies", side_effect=mock_scan):
        reply = await ask_ai_agent("Check Celonis live now", chat_id="chat-4", db_path=db_file)
        assert "Live Scan Results for Celonis" in reply
        assert "Associate Python Engineer" in reply


@pytest.mark.asyncio
async def test_ask_ai_agent_fallback_list_companies(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify natural query asking to list companies returns company directory."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    reply = await ask_ai_agent("Can you list all companies tracked?", chat_id="chat-list")
    assert "Tracked GCCs & Enterprise Tech Hubs" in reply
    assert "GREENHOUSE" in reply
    assert "Celonis" in reply



# 6. Gemini REST API Integration with Tool Calling Mock


@pytest.mark.asyncio
async def test_ask_ai_agent_gemini_tool_calling(
    tmp_path: Path, sample_jobs: list[JobPosting], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify Gemini API flow executes functionCall and returns LLM response."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake_gemini_key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    db_file = tmp_path / "gemini_tool.db"
    init_db(db_file)
    record_jobs(sample_jobs, db_file)

    call_count = 0

    def gemini_mock(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        data = json.loads(request.content.decode("utf-8"))

        if call_count == 1:
            # First response: Gemini asks to call tool `query_jobs`
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "functionCall": {
                                            "name": "query_jobs",
                                            "args": {"title_keyword": "Python"},
                                        }
                                    }
                                ]
                            }
                        }
                    ]
                },
            )
        else:
            # Second response: Gemini summarizes tool output
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "text": "I found 1 Python role: **Associate Python Engineer** at Celonis! [Apply](https://job-boards.greenhouse.io/celonis/jobs/101)"
                                    }
                                ]
                            }
                        }
                    ]
                },
            )

    async with httpx.AsyncClient(transport=httpx.MockTransport(gemini_mock)) as client:
        reply = await ask_ai_agent(
            "Find python roles",
            chat_id="chat-gemini",
            db_path=db_file,
            client=client,
        )
        assert call_count == 2
        assert "<b>Associate Python Engineer</b>" in reply
        assert "href=\"https://job-boards.greenhouse.io/celonis/jobs/101\"" in reply


# 7. OpenAI REST API Integration with Tool Calling Mock


@pytest.mark.asyncio
async def test_ask_ai_agent_openai_tool_calling(
    tmp_path: Path, sample_jobs: list[JobPosting], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify OpenAI API flow executes tool_calls and returns LLM response."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "fake_openai_key")

    db_file = tmp_path / "openai_tool.db"
    init_db(db_file)
    record_jobs(sample_jobs, db_file)

    call_count = 0

    def openai_mock(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        data = json.loads(request.content.decode("utf-8"))

        if call_count == 1:
            # First response: OpenAI returns tool_calls
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "id": "call_123",
                                        "type": "function",
                                        "function": {
                                            "name": "query_jobs",
                                            "arguments": json.dumps({"company": "Databricks"}),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        else:
            # Second response: OpenAI generates final answer
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "Found 1 role at **Databricks**: [Graduate Software Engineer](https://job-boards.greenhouse.io/databricks/jobs/102)",
                            }
                        }
                    ]
                },
            )

    async with httpx.AsyncClient(transport=httpx.MockTransport(openai_mock)) as client:
        reply = await ask_ai_agent(
            "Show me jobs at Databricks",
            chat_id="chat-openai",
            db_path=db_file,
            client=client,
        )
        assert call_count == 2
        assert "<b>Databricks</b>" in reply
        assert "href=\"https://job-boards.greenhouse.io/databricks/jobs/102\"" in reply


# 8. Non-Tool Queries & Error Logging Tests


@pytest.mark.asyncio
async def test_ask_ai_agent_gemini_direct_text(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Verify basic non-tool queries (like general conversation or 2+2) return direct model text."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake_gemini_key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def gemini_mock(request: httpx.Request) -> httpx.Response:
        assert "gemini-3.1-flash-lite" in str(request.url)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "2 + 2 is **4**."}
                            ]
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(gemini_mock)) as client:
        reply = await ask_ai_agent("what is 2+2", chat_id="chat-math", client=client)
        assert "<b>4</b>" in reply

    captured = capsys.readouterr()
    assert "Available LLM providers: Gemini" in captured.out


@pytest.mark.asyncio
async def test_ask_ai_agent_gemini_api_error_logging(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Verify API error (e.g. 404/429) is logged to stderr with exact message before fallback."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake_gemini_key")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def gemini_mock(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            text='{"error": {"code": 404, "message": "models/gemini-1.5-flash is not found"}}',
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(gemini_mock)) as client:
        reply = await ask_ai_agent("what is 2+2", chat_id="chat-err", client=client)
        assert "I couldn't find an exact match" in reply

    captured = capsys.readouterr()
    assert "Available LLM providers: Gemini" in captured.out
    assert "[AI Agent Error] Gemini API error (HTTP 404)" in captured.err
    assert "models/gemini-1.5-flash is not found" in captured.err


@pytest.mark.asyncio
async def test_ask_ai_agent_no_key_logging(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Verify ask_ai_agent logs when no keys are detected."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    reply = await ask_ai_agent("hello", chat_id="chat-nokey")
    assert "Hello! I'm your GCC Job Radar Assistant" in reply
    captured = capsys.readouterr()
    assert "Neither GEMINI_API_KEY nor GROQ_API_KEY nor OPENAI_API_KEY detected" in captured.out


# 9. Smart Shifting Tests (Gemini -> Groq)


@pytest.mark.asyncio
async def test_ask_ai_agent_smart_shift_gemini_to_groq(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Verify that when Gemini fails (e.g. HTTP 429 quota error), the agent automatically shifts to Groq."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake_gemini_key")
    monkeypatch.setenv("GROQ_API_KEY", "fake_groq_key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def multi_provider_mock(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "generativelanguage.googleapis.com" in url_str:
            # Gemini fails with 429 rate limit error
            return httpx.Response(
                429,
                text='{"error": {"code": 429, "message": "Quota exceeded for Gemini"}}',
            )
        elif "api.groq.com" in url_str:
            # Groq catches request and responds successfully
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "Hello from **Groq** via smart shifting!",
                            }
                        }
                    ]
                },
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(multi_provider_mock)) as client:
        reply = await ask_ai_agent("what is 2+2", chat_id="chat-shift", client=client)
        assert "<b>Groq</b>" in reply

    captured = capsys.readouterr()
    assert "Gemini, Groq" in captured.out
    assert "Attempting primary provider: Gemini" in captured.out
    assert "Smart shifting to Groq" in captured.out


@pytest.mark.asyncio
async def test_ask_ai_agent_groq_standalone(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify Groq works directly when Gemini key is absent."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "fake_groq_key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def groq_mock(request: httpx.Request) -> httpx.Response:
        assert "api.groq.com" in str(request.url)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Answer is **4**",
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(groq_mock)) as client:
        reply = await ask_ai_agent("what is 2+2", chat_id="chat-groq-only", client=client)
        assert "<b>4</b>" in reply


@pytest.mark.asyncio
async def test_ask_ai_agent_primary_groq_preference(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Verify that when PRIMARY_LLM_PROVIDER=groq, Groq is called first."""
    monkeypatch.setenv("PRIMARY_LLM_PROVIDER", "groq")
    monkeypatch.setenv("GEMINI_API_KEY", "fake_gemini_key")
    monkeypatch.setenv("GROQ_API_KEY", "fake_groq_key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def groq_first_mock(request: httpx.Request) -> httpx.Response:
        assert "api.groq.com" in str(request.url)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Lightning-fast answer from **Groq**!",
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(groq_first_mock)) as client:
        reply = await ask_ai_agent("any new opening", chat_id="chat-groq-primary", client=client)
        assert "<b>Groq</b>" in reply

    captured = capsys.readouterr()
    assert "Attempting primary provider: Groq" in captured.out


@pytest.mark.asyncio
async def test_execute_tool_query_jobs_hard_cap_and_compact_schema(tmp_path: Path) -> None:
    """Verify execute_tool('query_jobs') caps results at 15 and strips verbose DB fields."""
    db_file = tmp_path / "test_cap.db"
    init_db(db_file)
    postings = [
        JobPosting(
            id=f"cap-job-{i}",
            company=f"Company {i}",
            title=f"Software Engineer {i}",
            location="Bengaluru, India",
            apply_url=f"https://jobs.example.com/{i}",
            provider=ATSProvider.GREENHOUSE,
            published_date="2026-09-01",
        )
        for i in range(25)
    ]
    record_jobs(postings, db_file)

    res = await execute_tool("query_jobs", {"limit": 100}, db_path=db_file)
    assert res["status"] == "success"
    # Hard capped at 15
    assert res["count"] == 15
    assert len(res["jobs"]) == 15

    # Check compact schema: includes relevance and recommendation rationale
    allowed_keys = {
        "id", "company", "title", "location", "apply_url", "published_date",
        "relevance_score", "matched_reasons", "why",
    }
    for j in res["jobs"]:
        assert set(j.keys()) == allowed_keys


@pytest.mark.asyncio
async def test_ask_ai_agent_fallback_general_openings(tmp_path: Path, sample_jobs: list[JobPosting], monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify natural query 'all openings' returns active roles with clear summary."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    db_file = tmp_path / "general_openings.db"
    init_db(db_file)
    record_jobs(sample_jobs, db_file)

    reply = await ask_ai_agent("not lastest i want all the openings", chat_id="chat-all-jobs", db_path=db_file)
    assert "Active Verified GCC Openings" in reply
    assert "Celonis" in reply


@pytest.mark.asyncio
async def test_groq_413_payload_too_large_smart_shift(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that when Groq returns HTTP 413 (TPM limit exceeded), it smart shifts to Gemini."""
    monkeypatch.setenv("PRIMARY_LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "fake_groq_key")
    monkeypatch.setenv("GEMINI_API_KEY", "fake_gemini_key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def mock_router(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "api.groq.com" in url_str:
            # Simulate Groq 413 TPM limit error
            return httpx.Response(
                413,
                json={
                    "error": {
                        "message": "Request too large for model `openai/gpt-oss-120b` on TPM: Limit 8000, Requested 11365",
                        "type": "tokens",
                        "code": "rate_limit_exceeded",
                    }
                },
            )
        elif "generativelanguage.googleapis.com" in url_str:
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {"text": "Successfully shifted to Gemini after Groq 413!"}
                                ]
                            }
                        }
                    ]
                },
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(mock_router)) as client:
        reply = await ask_ai_agent("all openings", chat_id="chat-413-shift", client=client)
        assert "Successfully shifted to Gemini after Groq 413!" in reply


@pytest.mark.asyncio
async def test_ask_ai_agent_points_explanation(monkeypatch) -> None:
    """Test AI agent explains relevance score points in fallback when asked."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    reply = await ask_ai_agent("What does these points means", chat_id="test-pts-query")
    assert "Personal Tech-Stack Relevance Score" in reply
    assert "Java" in reply
    assert "Core Stack" in reply


@pytest.mark.asyncio
async def test_execute_tool_get_dismissed_jobs(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify execute_tool handles get_dismissed_jobs with total count and company summaries."""
    db_file = tmp_path / "test_dismissed_tool.db"
    init_db(db_file)
    record_jobs(sample_jobs, db_file)
    mark_job_status("job-python-01", status="DISMISSED", db_path=db_file)

    res = await execute_tool("get_dismissed_jobs", {}, db_path=db_file)
    assert res["status"] == "success"
    assert res["total_dismissed_count"] == 1
    assert "Celonis" in res["all_dismissed_companies"]
    assert len(res["recent_dismissed_jobs"]) == 1


@pytest.mark.asyncio
async def test_ask_ai_agent_dismissed_fallback(tmp_path: Path, sample_jobs: list[JobPosting], monkeypatch) -> None:
    """Test AI agent fallback returns organized dismissed list."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    db_file = tmp_path / "test_dismissed_fallback.db"
    init_db(db_file)
    record_jobs(sample_jobs, db_file)
    mark_job_status("job-python-01", status="DISMISSED", db_path=db_file)

    reply = await ask_ai_agent("dismissed list", chat_id="test-dismissed-query", db_path=db_file)
    assert "Dismissed Roles" in reply
    assert "Celonis" in reply


@pytest.mark.asyncio
async def test_execute_tool_sync_email_jobs(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify execute_tool handles sync_email_jobs by delegating to sync_email_alerts."""
    db_file = tmp_path / "test_email_tool.db"
    init_db(db_file)

    with patch("tools.ingest_email.sync_email_alerts", return_value=[sample_jobs[0]]) as mock_sync, \
         patch("tools.ingest_email.get_configured_email_accounts", return_value=[("test@gmail.com", "pass")]):
        res = await execute_tool("sync_email_jobs", {"days": 7, "limit": 10}, db_path=db_file)
        assert res["status"] == "success"
        assert res["total_found"] == 1
        assert res["accounts_checked"] == ["test@gmail.com"]
        assert len(res["jobs"]) == 1
        assert res["jobs"][0]["company"] == "Celonis"
        mock_sync.assert_called_once()


@pytest.mark.asyncio
async def test_ask_ai_agent_email_query_fallback(tmp_path: Path, sample_jobs: list[JobPosting], monkeypatch) -> None:
    """Verify AI agent handles user asking to go through email accounts for jobs."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    db_file = tmp_path / "test_email_fallback.db"
    init_db(db_file)

    with patch("tools.ingest_email.sync_email_alerts", return_value=[sample_jobs[0]]), \
         patch("tools.ingest_email.get_configured_email_accounts", return_value=[("test@gmail.com", "pass")]):
        reply = await ask_ai_agent("Go through all 3 email accounts for new relevant jobs", chat_id="test-email-user", db_path=db_file)
        assert "New Openings from Email Alerts" in reply
        assert "Celonis" in reply


@pytest.mark.asyncio
async def test_execute_tool_tailor_job_resume(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify execute_tool handles tailor_job_resume by invoking tailor_resume_for_job."""
    db_file = tmp_path / "test_tailor_tool.db"
    init_db(db_file)
    record_jobs([sample_jobs[0]], db_file)

    with patch("gcc_job_radar.resume_tailor_bridge.tailor_resume_for_job", return_value=("tailored/Celonis.tex", "tailored/Celonis.pdf")) as mock_tailor:
        res = await execute_tool("tailor_job_resume", {"company": "Celonis"}, db_path=db_file)
        assert res["status"] == "success"
        assert res["company"] == "Celonis"
        assert res["tex_path"] == "tailored/Celonis.tex"
        assert res["pdf_path"] == "tailored/Celonis.pdf"
        mock_tailor.assert_called_once()

        # Test non-existent company
        res_nf = await execute_tool("tailor_job_resume", {"company": "NonExistentCo"}, db_path=db_file)
        assert res_nf["status"] == "not_found"


@pytest.mark.asyncio
async def test_ask_ai_agent_tailor_fallback(tmp_path: Path, sample_jobs: list[JobPosting], monkeypatch) -> None:
    """Verify AI agent fallback handles resume tailoring requests."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    db_file = tmp_path / "test_tailor_fallback.db"
    init_db(db_file)
    record_jobs([sample_jobs[0]], db_file)

    with patch("gcc_job_radar.resume_tailor_bridge.tailor_resume_for_job", return_value=("tailored/Celonis.tex", "tailored/Celonis.pdf")):
        reply = await ask_ai_agent("tailor my resume for Celonis", chat_id="test-tailor-user", db_path=db_file)
        assert "Tailored Resume Ready!" in reply
        assert "Celonis" in reply
        assert "Celonis.tex" in reply
        assert "Celonis.pdf" in reply


def test_markdown_to_telegram_html_headers_and_checkboxes() -> None:
    """Verify markdown headers and task list checkboxes convert to Telegram-safe HTML."""
    raw_md = (
        "### Weekly Action Items\n\n"
        "- [ ] **Follow up on Celonis** — Pending 8 days\n"
        "- [x] **Applied to Databricks** — Completed\n"
        "• **Backend Focus** — Priority stack Java/Spring"
    )
    res = markdown_to_telegram_html(raw_md)
    # Header converted to bold
    assert "<b>Weekly Action Items</b>" in res
    # Checkbox converted to visual Unicode ballot box
    assert "☐ <b>Follow up on Celonis</b> — Pending 8 days" in res
    assert "☑ <b>Applied to Databricks</b> — Completed" in res
    # Bullet preserved
    assert "• <b>Backend Focus</b> — Priority stack Java/Spring" in res


@pytest.mark.asyncio
async def test_execute_tool_get_stale_applications(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify execute_tool handles get_stale_applications correctly."""
    db_file = tmp_path / "test_stale_tool.db"
    init_db(db_file)
    record_jobs([sample_jobs[0]], db_file)
    mark_job_status(1, status="APPLIED", notes="Test note", db_path=db_file)

    stale_mock_job = {
        "id": "job-python-01",
        "numeric_id": 1,
        "company": "Celonis",
        "title": "Associate Python Engineer",
        "location": "Bengaluru",
        "applied_at": "2026-08-01 10:00:00",
        "applied_days": 10,
        "notes": "Test note",
        "apply_url": "https://example.com/apply",
    }
    with patch("gcc_job_radar.db.get_stale_applications", return_value=[stale_mock_job]):
        res = await execute_tool("get_stale_applications", {"days": 7}, db_path=db_file)
        assert res["status"] == "success"
        assert res["count"] == 1
        assert res["total_stale"] == 1
        assert res["stale_jobs"][0]["company"] == "Celonis"
        assert res["stale_jobs"][0]["applied_days"] == 10


@pytest.mark.asyncio
async def test_ask_ai_agent_prioritize_fallback(tmp_path: Path, monkeypatch) -> None:
    """Verify 'What should I prioritize this week?' returns structured headers, checkboxes, and bullets."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    db_file = tmp_path / "test_prio.db"
    init_db(db_file)

    # 1. Telegram delivery path (default HTML)
    reply_html = await ask_ai_agent("What should I prioritize this week?", chat_id="test-prio-tg", db_path=db_file)
    assert "Priorities" in reply_html
    assert "Immediate Action Items" in reply_html
    assert "☐" in reply_html  # Visual checkbox in Telegram HTML
    assert "•" in reply_html  # Bullet point
    assert "<b>" in reply_html  # Bold key terms

    # 2. CLI delivery path (as_markdown=True)
    reply_md = await ask_ai_agent("What should I prioritize this week?", chat_id="cli", db_path=db_file, as_markdown=True)
    assert "### Weekly Priorities & Action Plan" in reply_md
    assert "- [ ]" in reply_md  # Markdown checkbox for CLI Rich rendering
    assert "•" in reply_md
    assert "**" in reply_md


@pytest.mark.asyncio
async def test_ask_ai_agent_stale_applications_fallback(tmp_path: Path, monkeypatch) -> None:
    """Verify 'Summarize my stale applications' returns structured summary."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    db_file = tmp_path / "test_stale_q.db"
    init_db(db_file)

    stale_mock_job = {
        "id": "job-python-01",
        "numeric_id": 1,
        "company": "Celonis",
        "title": "Associate Python Engineer",
        "location": "Bengaluru",
        "applied_at": "2026-08-01 10:00:00",
        "applied_days": 12,
        "notes": "Followed up once",
        "apply_url": "https://example.com/apply",
    }
    with patch("gcc_job_radar.db.get_stale_applications", return_value=[stale_mock_job]):
        # Telegram path
        reply_html = await ask_ai_agent("Summarize my stale applications", chat_id="test-stale-tg", db_path=db_file)
        assert "Stale Applications Summary" in reply_html
        assert "Celonis" in reply_html
        assert "12 days" in reply_html
        assert "☐" in reply_html

        # CLI path
        reply_md = await ask_ai_agent("Summarize my stale applications", chat_id="cli", db_path=db_file, as_markdown=True)
        assert "### Stale Applications Summary" in reply_md
        assert "- [ ]" in reply_md
        assert "**Celonis (Associate Python Engineer)**" in reply_md


@pytest.mark.asyncio
async def test_ask_ai_agent_filter_explanation_fallback(tmp_path: Path, monkeypatch) -> None:
    """Verify 'Why was this job filtered out?' returns structured filtering rules."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    reply_html = await ask_ai_agent("Why was this job filtered out?", chat_id="test-filter-tg")
    assert "Filtering Rules" in reply_html
    assert "Entry-Level Seniority" in reply_html
    assert "Senior Titles" in reply_html
    assert "•" in reply_html

    reply_md = await ask_ai_agent("Why was this job filtered out?", chat_id="cli", as_markdown=True)
    assert "### GCC Job Radar Filtering Rules" in reply_md
    assert "**Mandatory Inclusion Criteria:**" in reply_md
    assert "**Automatic Exclusion Triggers:**" in reply_md


@pytest.mark.asyncio
async def test_ask_ai_agent_custom_career_scrapers_fallback(monkeypatch) -> None:
    """Verify queries asking about custom career scrapers and non-ATS boards return accurate breakdown."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    # 1. Telegram HTML path with user query 1
    q1 = "how many companies are currently present that we do scrap of careers page"
    reply_html1 = await ask_ai_agent(q1, chat_id="tg-scraper-1")
    assert "Career Page Scraper Architecture" in reply_html1
    assert "Custom Career Portal Scrapers (6 Companies)" in reply_html1
    assert "Flipkart" in reply_html1
    assert "Apple" in reply_html1
    assert "Amazon" in reply_html1
    assert "Microsoft" in reply_html1
    assert "5,313" in reply_html1

    # 2. Telegram HTML path with user query 2 (mentioning flipkart, apple, cisco)
    q2 = "i mean like flipkart, apple, cisco and more are not present on any ATS board like that how many companies are present"
    reply_html2 = await ask_ai_agent(q2, chat_id="tg-scraper-2")
    assert "Custom Career Portal Scrapers (6 Companies)" in reply_html2
    assert "Flipkart" in reply_html2
    assert "Apple" in reply_html2
    assert "Workday" in reply_html2

    # 3. CLI Markdown path
    reply_md = await ask_ai_agent(q2, chat_id="cli", as_markdown=True)
    assert "### Career Page Scraper Architecture" in reply_md
    assert "**Custom Career Portal Scrapers (6 Companies):**" in reply_md
    assert "**Total Tracked Companies:** 5,313" in reply_md


@pytest.mark.asyncio
async def test_execute_tool_get_configured_companies_enriched_metadata(tmp_path: Path) -> None:
    """Verify execute_tool get_configured_companies returns custom scraper count and ATS breakdown."""
    db_file = tmp_path / "test_comps_meta.db"
    init_db(db_file)

    res = await execute_tool("get_configured_companies", {"include_all": True}, db_path=db_file)
    assert res["status"] == "success"
    assert res["custom_career_scrapers_count"] >= 6
    assert "Flipkart" in res["custom_career_scrapers"]
    assert "Apple" in res["custom_career_scrapers"]
    assert "Amazon" in res["custom_career_scrapers"]
    assert "greenhouse" in res["ats_provider_breakdown"]
    assert "ashby" in res["ats_provider_breakdown"]
    assert "note" in res

