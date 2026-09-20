"""Tests for expandable blockquote presentation, Telegram sanitization, and splitting."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from gcc_job_radar.bot_listener import handle_command
from gcc_job_radar.models import ATSProvider, JobPosting
from gcc_job_radar.presentation import (
    format_jobs_html,
    sanitize_telegram_html,
    split_telegram_message,
)


def test_format_jobs_html_expandable_blockquote() -> None:
    """Verify format_jobs_html places top 5 cards outside and remaining cards inside <blockquote expandable>."""
    jobs = [
        JobPosting(
            id=f"job-{i}",
            company=f"Company_{i:02d}",
            title=f"Software Engineer {i}",
            location="Bengaluru",
            apply_url=f"https://example.com/job/{i}",
            provider=ATSProvider.GREENHOUSE,
            relevance_score=90 - i,
            why=f"Matches Python + Cloud {i}",
        )
        for i in range(1, 13)
    ]

    html_out = format_jobs_html(jobs, "All Verified Openings", max_full_cards=5)

    # 1. Check title & lead-in
    assert "🚀 <b>All Verified Openings (12)</b>" in html_out
    assert "Found 12 new roles" in html_out

    # 2. Check expandable blockquote exists
    assert "<blockquote expandable>" in html_out
    assert "</blockquote>" in html_out

    # 3. Split by <blockquote expandable>
    outside_part, inside_part = html_out.split("<blockquote expandable>")

    # 4. Verify top 5 cards are outside the blockquote
    for i in range(1, 6):
        assert f"<b>{i}. Company_{i:02d}</b>" in outside_part
        assert f"Software Engineer {i}" in outside_part
        assert f"Matches Python + Cloud {i}" in outside_part

    # 5. Verify cards 6 through 12 are inside the blockquote
    for i in range(6, 13):
        assert f"<b>{i}. Company_{i:02d}</b>" in inside_part
        assert f"Software Engineer {i}" in inside_part
        assert f"Matches Python + Cloud {i}" in inside_part

    # 6. Verify tail header is inside the blockquote
    assert "+7 more roles" in inside_part
    assert "reply <code>/latest all</code>" in inside_part


def test_telegram_html_sanitizer_preserves_expandable() -> None:
    """Verify TelegramHTMLSanitizer retains the expandable attribute on blockquote tags."""
    raw = '<blockquote expandable><b>Header</b>\n\nContent</blockquote>'
    clean = sanitize_telegram_html(raw)
    assert '<blockquote expandable>' in clean
    assert '</blockquote>' in clean
    assert '<b>Header</b>' in clean

    # Auto-close unclosed expandable blockquote
    unclosed = '<blockquote expandable>Line 1\nLine 2'
    auto_closed = sanitize_telegram_html(unclosed)
    assert auto_closed.startswith('<blockquote expandable>')
    assert auto_closed.endswith('</blockquote>')


def test_split_telegram_message_with_expandable_blockquote() -> None:
    """Verify split_telegram_message cleanly closes and re-opens <blockquote expandable> across chunks."""
    jobs = [
        JobPosting(
            id=f"job-{i}",
            company=f"TechCorp_{i:02d}",
            title=f"Backend Developer {i}",
            location="Bengaluru, Karnataka, India",
            apply_url=f"https://jobs.example.com/techcorp/roles/{i}",
            provider=ATSProvider.ASHBY,
            relevance_score=80 - i,
            why=f"Strong stack fit with FastAPI, Docker, and PostgreSQL ({i})",
        )
        for i in range(1, 25)
    ]

    html_out = format_jobs_html(jobs, "Large Scan Output", max_full_cards=5)
    assert len(html_out) > 2000

    chunks = split_telegram_message(html_out, max_length=1500)
    assert len(chunks) >= 2

    for idx, chunk in enumerate(chunks):
        assert len(chunk) <= 1500
        # If this chunk contains jobs inside the expandable section, it should be wrapped
        if idx > 0 and "Backend Developer" in chunk:
            assert chunk.startswith("<blockquote expandable>") or "<blockquote expandable>" in chunk
            assert chunk.rstrip().endswith("</blockquote>")

        # Each chunk must be valid HTML
        safe = sanitize_telegram_html(chunk)
        assert safe.count("<blockquote") == safe.count("</blockquote>")


@pytest.mark.asyncio
async def test_handle_command_latest_all(tmp_path: Path) -> None:
    """Verify /latest all retrieves up to 100 jobs and formats them with expandable blockquote."""
    from gcc_job_radar.db import init_db, record_jobs

    db_file = tmp_path / "test_latest.db"
    init_db(db_file)

    sample_jobs = [
        JobPosting(
            id=f"rec-{i}",
            company=f"GCC_{i:02d}",
            title=f"Associate Engineer {i}",
            location="Pune",
            apply_url=f"https://careers.gcc.com/{i}",
            provider=ATSProvider.LEVER,
            status="NEW",
            relevance_score=50,
        )
        for i in range(1, 15)
    ]
    record_jobs(sample_jobs, db_file)

    replies: list[str] = []

    async def mock_send(token, cid, text, client, reply_markup=None):
        replies.append(text)
        return True

    mock_client = AsyncMock()

    with patch("gcc_job_radar.bot_listener.send_telegram_reply", side_effect=mock_send):
        # 1. Test /latest (default 5)
        await handle_command(
            "/latest",
            chat_id="123",
            bot_token="tok",
            allowed_chat_id="123",
            client=mock_client,
            db_path=db_file,
        )
        assert len(replies) == 1
        assert "Latest Discovered Openings" in replies[0]
        # Should only have 5 jobs when default
        assert "5. GCC_" in replies[0]
        assert "6. GCC_" not in replies[0]

        replies.clear()

        # 2. Test /latest all (fetches all and puts 6+ in expandable blockquote)
        await handle_command(
            "/latest all",
            chat_id="123",
            bot_token="tok",
            allowed_chat_id="123",
            client=mock_client,
            db_path=db_file,
        )
        assert len(replies) == 1
        assert "All Latest Discovered Openings" in replies[0]
        assert "<blockquote expandable>" in replies[0]
        assert "1. GCC_" in replies[0]
        assert "14. GCC_" in replies[0]
