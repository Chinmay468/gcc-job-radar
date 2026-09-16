"""Tests for the gcc-job-radar Typer CLI commands, options, and file exports."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
from typer.testing import CliRunner

from gcc_job_radar import __version__
from gcc_job_radar.cli import app
from gcc_job_radar.models import ATSProvider, JobPosting

runner = CliRunner()


def test_cli_help() -> None:
    """Verify CLI --help runs cleanly and documents options."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "CLI tool to fetch verified entry-level tech roles in India" in result.output
    assert "--company" in result.output
    assert "--provider" in result.output
    assert "--concurrency" in result.output
    assert "--new-only" in result.output
    assert "--stats" in result.output
    assert "--notify-discord" in result.output
    assert "--notify-telegram-token" in result.output
    assert "--notify-telegram-chat" in result.output
    assert "--json" in result.output
    assert "--csv" in result.output


def test_cli_version() -> None:
    """Verify CLI --version outputs the version string."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert f"version {__version__}" in result.output


def test_cli_company_not_found() -> None:
    """Verify CLI flags invalid company queries with exit code 1."""
    result = runner.invoke(app, ["--company", "invalid_unknown_company_xyz"])
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "not found in registry" in result.output


def test_cli_stats_flag(tmp_path: Path) -> None:
    """Verify CLI --stats flag displays database overview."""
    db_file = tmp_path / "stats_test.db"
    result = runner.invoke(app, ["--stats", "--db", str(db_file)])
    assert result.exit_code == 0
    assert "Database Statistics" in result.output
    assert "Total Historically Tracked Roles" in result.output


def test_cli_new_only_flag(tmp_path: Path) -> None:
    """Verify CLI --new-only filters out previously recorded jobs."""
    db_file = tmp_path / "new_only_test.db"
    sample_job = JobPosting(
        id="test-101",
        company="Databricks",
        title="Software Engineer 1",
        location="Bengaluru, India",
        apply_url="https://boards.greenhouse.io/databricks/jobs/101",
        published_date="2026-09-01",
        provider=ATSProvider.GREENHOUSE,
    )

    async def mock_scan(*args, **kwargs):
        return [sample_job]

    # Run 1: Should detect 1 new job
    with patch("gcc_job_radar.cli.scan_all_companies", side_effect=mock_scan), patch(
        "gcc_job_radar.cli.dispatch_notifications"
    ):
        res1 = runner.invoke(app, ["--new-only", "--db", str(db_file)])
        assert res1.exit_code == 0
        assert "Found 1 new entry-level opening" in res1.output

    # Run 2: Same job should now be detected as 0 new jobs
    with patch("gcc_job_radar.cli.scan_all_companies", side_effect=mock_scan), patch(
        "gcc_job_radar.cli.dispatch_notifications"
    ):
        res2 = runner.invoke(app, ["--new-only", "--db", str(db_file)])
        assert res2.exit_code == 0
        assert "No newly discovered entry-level roles since your last scan" in res2.output


def test_cli_notification_dispatch(tmp_path: Path) -> None:
    """Verify CLI invokes dispatch_notifications when new jobs and flags are supplied."""
    db_file = tmp_path / "notify_test.db"
    sample_job = JobPosting(
        id="test-101",
        company="Databricks",
        title="Software Engineer 1",
        location="Bengaluru, India",
        apply_url="https://boards.greenhouse.io/databricks/jobs/101",
        published_date="2026-09-01",
        provider=ATSProvider.GREENHOUSE,
    )

    async def mock_scan(*args, **kwargs):
        return [sample_job]

    mock_dispatch = AsyncMock()
    with patch("gcc_job_radar.cli.scan_all_companies", side_effect=mock_scan), patch(
        "gcc_job_radar.cli.dispatch_notifications", mock_dispatch
    ):
        result = runner.invoke(
            app,
            [
                "--db",
                str(db_file),
                "--notify-discord",
                "https://discord.com/api/webhooks/mock",
            ],
        )
        assert result.exit_code == 0
        assert mock_dispatch.called
        call_kwargs = mock_dispatch.call_args[1]
        assert call_kwargs["discord_webhook"] == "https://discord.com/api/webhooks/mock"
        assert len(call_kwargs["new_jobs"]) == 1


def test_cli_json_and_csv_export(tmp_path: Path) -> None:
    """Verify CLI executes export flows and creates valid JSON/CSV outputs."""
    db_file = tmp_path / "export_test.db"
    sample_job = JobPosting(
        id="test-101",
        company="Databricks",
        title="Software Engineer 1",
        location="Bengaluru, India",
        apply_url="https://boards.greenhouse.io/databricks/jobs/101",
        published_date="2026-09-01",
        provider=ATSProvider.GREENHOUSE,
    )

    json_file = tmp_path / "results.json"
    csv_file = tmp_path / "results.csv"

    async def mock_scan(*args, **kwargs):
        return [sample_job]

    with patch("gcc_job_radar.cli.scan_all_companies", side_effect=mock_scan), patch(
        "gcc_job_radar.cli.dispatch_notifications"
    ):
        result = runner.invoke(
            app,
            [
                "--company",
                "databricks",
                "--db",
                str(db_file),
                "--json",
                str(json_file),
                "--csv",
                str(csv_file),
            ],
        )

        assert result.exit_code == 0
        assert json_file.exists()
        assert csv_file.exists()

        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert len(data) == 1
        assert data[0]["company"] == "Databricks"
        assert data[0]["title"] == "Software Engineer 1"

        with open(csv_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 2
        assert "company,title,location,apply_url,published_date,provider" in lines[0]
        assert "Databricks,Software Engineer 1" in lines[1]


def test_cli_provider_filter_success(tmp_path: Path) -> None:
    """Verify CLI --provider filters scan target companies to specified ATS."""
    db_file = tmp_path / "provider_test.db"
    captured_companies = []

    async def mock_scan(companies, **kwargs):
        nonlocal captured_companies
        captured_companies = companies
        return []

    with patch("gcc_job_radar.cli.scan_all_companies", side_effect=mock_scan):
        result = runner.invoke(app, ["--provider", "greenhouse", "--db", str(db_file)])
        assert result.exit_code == 0
        assert len(captured_companies) > 0
        assert all(c.provider == ATSProvider.GREENHOUSE for c in captured_companies)


def test_cli_provider_filter_workday(tmp_path: Path) -> None:
    """Verify CLI --provider workday filters scan to Workday GCC boards and updates banner."""
    db_file = tmp_path / "workday_test.db"
    captured_companies = []

    async def mock_scan(companies, **kwargs):
        nonlocal captured_companies
        captured_companies = companies
        return []

    with patch("gcc_job_radar.cli.scan_all_companies", side_effect=mock_scan):
        result = runner.invoke(app, ["scan", "--provider", "workday", "--db", str(db_file)])
        assert result.exit_code == 0
        assert len(captured_companies) == 62
        assert all(c.provider == ATSProvider.WORKDAY for c in captured_companies)
        assert "WORKDAY" in result.output
        assert "62" in result.output



def test_cli_provider_filter_invalid() -> None:
    """Verify CLI flags unknown provider with exit code 1 and lists valid options."""
    result = runner.invoke(app, ["--provider", "nonexistent_ats_provider"])
    assert result.exit_code == 1
    assert "No companies found matching ATS provider" in result.output
    assert "greenhouse" in result.output


def test_cli_custom_concurrency(tmp_path: Path) -> None:
    """Verify CLI passes custom --concurrency setting to scan_all_companies."""
    db_file = tmp_path / "concurrency_test.db"
    passed_concurrency = None

    async def mock_scan(companies, concurrency, **kwargs):
        nonlocal passed_concurrency
        passed_concurrency = concurrency
        return []

    with patch("gcc_job_radar.cli.scan_all_companies", side_effect=mock_scan):
        result = runner.invoke(app, ["--concurrency", "45", "--company", "Databricks", "--db", str(db_file)])
        assert result.exit_code == 0
        assert passed_concurrency == 45
