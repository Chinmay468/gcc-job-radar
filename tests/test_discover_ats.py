"""Unit tests for tools/discover_ats.py ATS Web Discovery."""

import argparse
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from gcc_job_radar.config import COMPANIES
from gcc_job_radar.models import ATSProvider
from tools.discover_ats import (
    DDGLinkParser,
    PLATFORM_SPECS,
    extract_slugs_from_urls,
    is_excluded_slug,
    parse_args,
    search_web_async,
    serper_search_async,
    slug_to_name,
    validate_ashby_slug,
    validate_candidate_slug,
    validate_greenhouse_slug,
    validate_lever_slug,
    validate_smartrecruiters_slug,
)


def test_ddg_link_parser_direct_and_redirect_urls() -> None:
    """Verify DDGLinkParser extracts direct links and unwraps uddg redirect params."""
    html_content = """
    <html>
        <body>
            <div class="results">
                <a class="result__a" href="https://boards.greenhouse.io/stripe">Stripe Careers</a>
                <a class="result__url" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fjobs.ashbyhq.com%2Fretell-ai&rut=123">Retell AI</a>
                <a class="other__link" href="https://example.com/ignore">Ignore</a>
                <a class="result__a" href="/l/?uddg=https%3A%2F%2Fjobs.lever.co%2Fnetflix%2F123&rut=456">Netflix Lever</a>
            </div>
        </body>
    </html>
    """
    parser = DDGLinkParser()
    parser.feed(html_content)

    assert len(parser.urls) == 3
    assert parser.urls[0] == "https://boards.greenhouse.io/stripe"
    assert parser.urls[1] == "https://jobs.ashbyhq.com/retell-ai"
    assert parser.urls[2] == "https://jobs.lever.co/netflix/123"


def test_slug_to_name_formatting() -> None:
    """Verify conversion of various slug formats to human-readable titles."""
    assert slug_to_name("stripe") == "Stripe"
    assert slug_to_name("retell-ai") == "Retell Ai"
    assert slug_to_name("data_dog_corp") == "Data Dog Corp"
    assert slug_to_name("single.store") == "Single Store"
    assert slug_to_name("abc-xyz-123") == "Abc Xyz 123"


def test_extract_slugs_from_urls_across_platforms() -> None:
    """Verify regex slug extraction across platforms and rejection of ignored keywords."""
    # Greenhouse
    gh_urls = [
        "https://boards.greenhouse.io/datadog",
        "https://boards.greenhouse.io/datadog/jobs/123",
        "https://boards.greenhouse.io/embed/job_board",  # Ignored keyword
        "https://boards.greenhouse.io/stripe-inc",
    ]
    gh_slugs = extract_slugs_from_urls(gh_urls, PLATFORM_SPECS["greenhouse"]["slug_re"])
    assert "datadog" in gh_slugs
    assert "stripe-inc" in gh_slugs
    assert "embed" not in gh_slugs

    # Ashby
    ashby_urls = [
        "https://jobs.ashbyhq.com/openai",
        "https://jobs.ashbyhq.com/anthropic/job-123",
        "https://jobs.ashbyhq.com/privacy",  # Ignored keyword
    ]
    ashby_slugs = extract_slugs_from_urls(ashby_urls, PLATFORM_SPECS["ashby"]["slug_re"])
    assert "openai" in ashby_slugs
    assert "anthropic" in ashby_slugs
    assert "privacy" not in ashby_slugs

    # Lever
    lever_urls = [
        "https://jobs.lever.co/atlassian",
        "https://jobs.lever.co/veeva/90e4e761",
    ]
    lever_slugs = extract_slugs_from_urls(lever_urls, PLATFORM_SPECS["lever"]["slug_re"])
    assert "atlassian" in lever_slugs
    assert "veeva" in lever_slugs

    # SmartRecruiters
    sr_urls = [
        "https://jobs.smartrecruiters.com/boschgroup",
        "https://jobs.smartrecruiters.com/Lowes",
    ]
    sr_slugs = extract_slugs_from_urls(sr_urls, PLATFORM_SPECS["smartrecruiters"]["slug_re"])
    assert "boschgroup" in sr_slugs
    assert "lowes" in sr_slugs


def test_dynamic_exclusion_logic() -> None:
    """Verify existing tokens and company names are properly excluded."""
    existing_tokens = {"stripe", "openai", "atlassian", "boschgroup"}
    existing_names = {"stripe", "open ai", "atlassian", "bosch group"}

    # Exact token match
    assert is_excluded_slug("stripe", existing_tokens, existing_names) is True
    assert is_excluded_slug("OPENAI", existing_tokens, existing_names) is True

    # Derived company name match
    assert is_excluded_slug("atlassian", existing_tokens, existing_names) is True

    # Unrecorded candidate slug
    assert is_excluded_slug("totally-new-scaleup-xyz", existing_tokens, existing_names) is False


@pytest.mark.asyncio
async def test_validate_greenhouse_slug() -> None:
    """Verify Greenhouse validation parses active jobs count or flags 404."""
    # Positive case
    mock_resp_success = MagicMock()
    mock_resp_success.status_code = 200
    mock_resp_success.json.return_value = {"jobs": [{"title": "Software Engineer 1"}, {"title": "SRE"}]}

    client_mock = AsyncMock(spec=httpx.AsyncClient)
    client_mock.get.return_value = mock_resp_success

    ok, count = await validate_greenhouse_slug("testco", client_mock)
    assert ok is True
    assert count == 2

    # Empty jobs case
    mock_resp_empty = MagicMock()
    mock_resp_empty.status_code = 200
    mock_resp_empty.json.return_value = {"jobs": []}
    client_mock.get.return_value = mock_resp_empty

    ok, count = await validate_greenhouse_slug("emptyco", client_mock)
    assert ok is False
    assert count == 0

    # 404 error case
    mock_resp_404 = MagicMock()
    mock_resp_404.status_code = 404
    client_mock.get.return_value = mock_resp_404

    ok, count = await validate_greenhouse_slug("badco", client_mock)
    assert ok is False
    assert count == 0


@pytest.mark.asyncio
async def test_validate_ashby_slug() -> None:
    """Verify Ashby validation parses live JSON payload and active postings count."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "jobs": [
            {"title": "Founding Engineer"},
            {"title": "Backend Tech Lead"},
            {"title": "Junior Developer"},
        ],
        "apiVersion": "1.0",
    }

    client_mock = AsyncMock(spec=httpx.AsyncClient)
    client_mock.get.return_value = mock_resp

    ok, count = await validate_ashby_slug("retell-ai", client_mock)
    assert ok is True
    assert count == 3

    # Empty case
    mock_resp.json.return_value = {"jobs": []}
    ok, count = await validate_ashby_slug("dead-startup", client_mock)
    assert ok is False
    assert count == 0


@pytest.mark.asyncio
async def test_validate_lever_slug() -> None:
    """Verify Lever validation parses JSON list of postings."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [{"text": "Software Engineer"}, {"text": "DevOps Engineer"}]

    client_mock = AsyncMock(spec=httpx.AsyncClient)
    client_mock.get.return_value = mock_resp

    ok, count = await validate_lever_slug("scale-co", client_mock)
    assert ok is True
    assert count == 2


@pytest.mark.asyncio
async def test_validate_smartrecruiters_slug() -> None:
    """Verify SmartRecruiters validation parses totalFound or content list."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"totalFound": 12, "content": [{"name": "Software Engineer"}]}

    client_mock = AsyncMock(spec=httpx.AsyncClient)
    client_mock.get.return_value = mock_resp

    ok, count = await validate_smartrecruiters_slug("enterprise-corp", client_mock)
    assert ok is True
    assert count == 12


@pytest.mark.asyncio
async def test_validate_candidate_slug_constructs_probe_result() -> None:
    """Verify validate_candidate_slug constructs ProbeResult on success."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"jobs": [{"title": "Software Engineer"}]}

    client_mock = AsyncMock(spec=httpx.AsyncClient)
    client_mock.get.return_value = mock_resp

    result = await validate_candidate_slug("ashby", "hyper-speed-ai", client_mock)
    assert result is not None
    assert result.company_name == "Hyper Speed Ai"
    assert result.provider == ATSProvider.ASHBY
    assert result.board_token == "hyper-speed-ai"
    assert result.active_postings == 1


def test_parse_args_options() -> None:
    """Verify CLI argument defaults and custom flag overrides."""
    # Defaults
    args_default = parse_args([])
    assert "greenhouse" in args_default.platforms
    assert "ashby" in args_default.platforms
    assert args_default.max_queries == 10
    assert args_default.append is True

    # Overrides
    args_custom = parse_args(["-p", "ashby,lever", "-m", "3", "--no-append", "-o", "out.json", "-c", "Retell AI"])
    assert args_custom.platforms == "ashby,lever"
    assert args_custom.max_queries == 3
    assert args_custom.append is False
    assert args_custom.output == "out.json"
    assert args_custom.company == "Retell AI"


@pytest.mark.asyncio
async def test_serper_search_async_success() -> None:
    """Verify serper_search_async extracts organic links correctly."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "organic": [
            {"link": "https://jobs.ashbyhq.com/retell-ai/job1"},
            {"link": "https://jobs.ashbyhq.com/retell-ai/job2"},
        ]
    }

    client_mock = AsyncMock(spec=httpx.AsyncClient)
    client_mock.post.return_value = mock_resp

    urls = await serper_search_async("site:jobs.ashbyhq.com Retell AI", client_mock, api_key="fake-key")
    assert len(urls) == 2
    assert "https://jobs.ashbyhq.com/retell-ai/job1" in urls


@pytest.mark.asyncio
async def test_search_web_async_prefers_serper(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify search_web_async uses Serper when SERPER_API_KEY is present."""
    monkeypatch.setenv("SERPER_API_KEY", "test-key")

    with patch("tools.discover_ats.serper_search_async", new_callable=AsyncMock) as mock_serper:
        mock_serper.return_value = ["https://jobs.ashbyhq.com/test-co"]
        client_mock = AsyncMock(spec=httpx.AsyncClient)

        urls = await search_web_async("test query", client_mock)
        assert urls == ["https://jobs.ashbyhq.com/test-co"]
        mock_serper.assert_called_once()
