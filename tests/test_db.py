"""Unit tests for SQLite state tracking and persistence."""

from pathlib import Path
import sqlite3
import pytest

from gcc_job_radar.db import (
    filter_new_jobs,
    filter_unseen_email_uids,
    get_stats,
    init_db,
    is_email_seen,
    make_job_key,
    record_jobs,
    record_seen_email_uids,
)
from gcc_job_radar.models import ATSProvider, JobPosting


@pytest.fixture
def sample_jobs() -> list[JobPosting]:
    return [
        JobPosting(
            id="job-101",
            company="Databricks",
            title="Software Engineer 1",
            location="Bengaluru, India",
            apply_url="https://boards.greenhouse.io/databricks/jobs/101",
            published_date="2026-09-01",
            provider=ATSProvider.GREENHOUSE,
        ),
        JobPosting(
            id="job-202",
            company="Atlassian",
            title="Associate Software Engineer",
            location="Pune, India",
            apply_url="https://jobs.lever.co/atlassian/job-202",
            published_date="2026-08-30",
            provider=ATSProvider.LEVER,
        ),
    ]


def test_init_db(tmp_path: Path) -> None:
    """Verify schema initialization and index creation."""
    db_file = tmp_path / "test_jobs.db"
    init_db(db_file)
    assert db_file.exists()

    with sqlite3.connect(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='seen_jobs'")
        assert cursor.fetchone() is not None

        cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_seen_jobs_company'")
        assert cursor.fetchone() is not None


def test_filter_and_record_jobs(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify first run flags all as new, second run flags as existing."""
    db_file = tmp_path / "test_jobs.db"

    # 1. First run: all should be new
    new_jobs, existing_jobs = filter_new_jobs(sample_jobs, db_file)
    assert len(new_jobs) == 2
    assert len(existing_jobs) == 0

    # 2. Record jobs into DB
    record_jobs(sample_jobs, db_file)

    # 3. Second run: all should be existing
    new_jobs, existing_jobs = filter_new_jobs(sample_jobs, db_file)
    assert len(new_jobs) == 0
    assert len(existing_jobs) == 2

    # 4. Introduce a 3rd new job
    job_3 = JobPosting(
        id="job-303",
        company="Linear",
        title="Junior Software Engineer",
        location="Remote - India",
        apply_url="https://jobs.ashbyhq.com/linear/job-303",
        published_date="2026-08-25",
        provider=ATSProvider.ASHBY,
    )
    mixed_jobs = sample_jobs + [job_3]
    new_jobs, existing_jobs = filter_new_jobs(mixed_jobs, db_file)
    assert len(new_jobs) == 1
    assert new_jobs[0].id == "job-303"
    assert len(existing_jobs) == 2


def test_get_stats(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify stats calculation and company breakdown."""
    db_file = tmp_path / "test_jobs.db"

    stats_empty = get_stats(db_file)
    assert stats_empty["total_tracked"] == 0
    assert stats_empty["company_breakdown"] == {}

    record_jobs(sample_jobs, db_file)
    stats = get_stats(db_file)
    assert stats["total_tracked"] == 2
    assert stats["company_breakdown"]["Databricks"] == 1
    assert stats["company_breakdown"]["Atlassian"] == 1
    assert stats["first_recorded"] is not None


def test_dispatched_alerts_deduplication(tmp_path: Path, sample_jobs: list[JobPosting]) -> None:
    """Verify dispatched_alerts table prevents duplicate notifications per platform."""
    from gcc_job_radar.db import (
        filter_unalerted_jobs,
        record_dispatched_alert,
        record_dispatched_alerts,
    )

    db_file = tmp_path / "test_alerts.db"

    # Initially, all jobs are unalerted for Telegram
    unalerted_tg = filter_unalerted_jobs(sample_jobs, "telegram", db_file)
    assert len(unalerted_tg) == 2

    # Record first job as alerted for Telegram
    from gcc_job_radar.db import make_job_key

    record_dispatched_alert(make_job_key(sample_jobs[0]), "telegram", db_file)

    # Now only 1 job should be unalerted for Telegram
    unalerted_tg = filter_unalerted_jobs(sample_jobs, "telegram", db_file)
    assert len(unalerted_tg) == 1
    assert unalerted_tg[0].id == sample_jobs[1].id

    # Discord should still have 2 unalerted jobs (different platform)
    unalerted_dc = filter_unalerted_jobs(sample_jobs, "discord", db_file)
    assert len(unalerted_dc) == 2

    # Atomically record remaining for Telegram
    record_dispatched_alerts(unalerted_tg, "telegram", db_file)
    unalerted_tg_final = filter_unalerted_jobs(sample_jobs, "telegram", db_file)
    assert len(unalerted_tg_final) == 0


def test_canonicalize_url() -> None:
    """Verify URL canonicalization removes query params, tracking, and trailing slashes."""
    from gcc_job_radar.db import canonicalize_url

    url_1 = "https://job-boards.greenhouse.io/celonis/jobs/7791267003?gh_jid=7791267003"
    url_2 = "https://job-boards.greenhouse.io/celonis/jobs/7791267003/"
    assert canonicalize_url(url_1) == "https://job-boards.greenhouse.io/celonis/jobs/7791267003"
    assert canonicalize_url(url_2) == "https://job-boards.greenhouse.io/celonis/jobs/7791267003"


def test_record_jobs_semantic_deduplication(tmp_path: Path) -> None:
    """Verify recording duplicate jobs with different IDs/URLs updates existing row and does not duplicate."""
    from gcc_job_radar.db import get_latest_jobs, query_jobs

    db_file = tmp_path / "test_dedup.db"

    job_a = JobPosting(
        id="7791267003",
        company="Celonis",
        title="Associate Software Engineer - Java",
        location="Bangalore, India",
        apply_url="https://job-boards.greenhouse.io/celonis/jobs/7791267003?gh_jid=7791267003",
        published_date="2026-08-25",
        provider=ATSProvider.GREENHOUSE,
    )

    # Identical role with different ID and clean URL
    job_b = JobPosting(
        id="test-101",
        company="Celonis",
        title="Associate Software Engineer - Java",
        location="Bangalore, India",
        apply_url="https://job-boards.greenhouse.io/celonis/jobs/7791267003",
        published_date="2026-08-25",
        provider=ATSProvider.GREENHOUSE,
    )

    # Record first job
    record_jobs([job_a], db_file)
    assert get_stats(db_file)["total_tracked"] == 1

    # Record duplicate job
    record_jobs([job_b], db_file)
    assert get_stats(db_file)["total_tracked"] == 1

    # Query should return exactly 1 result
    latest = get_latest_jobs(limit=10, db_path=db_file)
    assert len(latest) == 1
    assert latest[0]["company"] == "Celonis"

    queried = query_jobs(company="Celonis", db_path=db_file)
    assert len(queried) == 1


def test_cleanup_duplicate_jobs(tmp_path: Path) -> None:
    """Verify cleanup_duplicate_jobs deletes duplicates and keeps the latest entry."""
    from gcc_job_radar.db import cleanup_duplicate_jobs

    db_file = tmp_path / "test_cleanup.db"

    # Simulate legacy table without unique index
    with sqlite3.connect(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE seen_jobs (
                id TEXT PRIMARY KEY,
                company TEXT NOT NULL,
                title TEXT NOT NULL,
                location TEXT NOT NULL,
                apply_url TEXT NOT NULL,
                provider TEXT NOT NULL,
                published_date TEXT,
                is_active INTEGER DEFAULT 1,
                first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cursor.execute(
            """
            INSERT INTO seen_jobs (id, company, title, location, apply_url, provider, published_date, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, '2026-09-01 10:00:00', '2026-09-01 10:00:00')
            """,
            ("dup-1", "Celonis", "Associate Software Engineer - Java", "Bangalore, India", "https://job-boards.greenhouse.io/celonis/jobs/7791267003?gh_jid=7791267003", "greenhouse", "2026-08-25"),
        )
        cursor.execute(
            """
            INSERT INTO seen_jobs (id, company, title, location, apply_url, provider, published_date, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, '2026-09-02 10:00:00', '2026-09-02 12:00:00')
            """,
            ("dup-2", "Celonis", "Associate Software Engineer - Java", "Bangalore, India", "https://job-boards.greenhouse.io/celonis/jobs/7791267003", "greenhouse", "2026-08-25"),
        )
        conn.commit()

    # Verify 2 rows initially
    with sqlite3.connect(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM seen_jobs")
        assert cursor.fetchone()[0] == 2

    # Run cleanup
    cleaned = cleanup_duplicate_jobs(db_file)
    assert cleaned >= 1

    # Verify only 1 row remains (the newest one)
    with sqlite3.connect(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, apply_url, last_seen_at FROM seen_jobs")
        rows = cursor.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "dup-2"
        assert rows[0][1] == "https://job-boards.greenhouse.io/celonis/jobs/7791267003"

    # Now init_db should successfully create the unique index on the cleaned table
    init_db(db_file)
    with sqlite3.connect(db_file) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_seen_jobs_company_title_loc'")
        assert cursor.fetchone() is not None


def test_seen_emails_tracking(tmp_path: Path) -> None:
    """Verify seen_emails table lifecycle: insertion, duplicate ignore, and filtering."""
    db_file = tmp_path / "test_seen_emails.db"
    init_db(db_file)

    # Initially none are seen
    assert is_email_seen("101", db_file) is False
    assert filter_unseen_email_uids(["101", "102", "103"], db_file) == ["101", "102", "103"]

    # Record 101 and 102
    record_seen_email_uids(["101", "102"], db_file)
    assert is_email_seen("101", db_file) is True
    assert is_email_seen("102", db_file) is True
    assert is_email_seen("103", db_file) is False

    # Filter unseen should now only return 103 and 104
    unseen = filter_unseen_email_uids(["101", "102", "103", "104"], db_file)
    assert unseen == ["103", "104"]

    # Recording duplicates should be safely ignored
    record_seen_email_uids(["101", "103"], db_file)
    assert is_email_seen("103", db_file) is True

    # Empty inputs handled cleanly
    assert filter_unseen_email_uids([], db_file) == []
    record_seen_email_uids([], db_file)


def test_seen_emails_multi_account_isolation(tmp_path: Path) -> None:
    """Verify that multiple email accounts with identical UIDs remain isolated."""
    db_file = tmp_path / "test_multi_seen_emails.db"
    init_db(db_file)

    acc1 = "user1@example.com"
    acc2 = "user2@example.com"

    # User 1 has seen UID 999
    record_seen_email_uids(["999"], db_file, account=acc1)
    assert is_email_seen("999", db_file, account=acc1) is True

    # User 2 has NOT seen UID 999
    assert is_email_seen("999", db_file, account=acc2) is False
    assert filter_unseen_email_uids(["999"], db_file, account=acc2) == ["999"]

    # When User 2 processes UID 999, it is recorded independently
    record_seen_email_uids(["999"], db_file, account=acc2)
    assert is_email_seen("999", db_file, account=acc2) is True
    assert filter_unseen_email_uids(["999"], db_file, account=acc2) == []


def test_cross_platform_duplicate_preserves_applied_status(tmp_path: Path) -> None:
    """Verify that an email alert duplicate role preserves the APPLIED status and canonical ATS URL."""
    from gcc_job_radar.db import filter_new_jobs, get_latest_jobs, record_jobs
    from gcc_job_radar.models import ATSProvider, JobPosting

    db_file = tmp_path / "test_cross_platform.db"
    init_db(db_file)

    # 1. User applies to Hevo Data via Lever
    lever_job = JobPosting(
        id="lever_hevo_123",
        company="Hevo Data",
        title="SDE I",
        location="Bangalore, India",
        apply_url="https://jobs.lever.co/hevodata/6cbbe304-e065-4711-bf3e-756795d2bc2a",
        provider=ATSProvider.LEVER,
        published_date="2026-07-27",
        status="APPLIED",
    )
    record_jobs([lever_job], db_path=db_file)

    # 2. Email alert arrives later from Glassdoor with location 'India'
    email_job = JobPosting(
        id="email_glassdoor_999",
        company="Hevo Data",
        title="SDE I",
        location="India",
        apply_url="https://www.glassdoor.com/job-listing/?jl=1010210834752",
        provider=ATSProvider.EMAIL_ALERT,
        published_date="Recent",
        status="NEW",
    )

    # filter_new_jobs should classify it as existing, not new
    new_jobs, existing_jobs = filter_new_jobs([email_job], db_path=db_file)
    assert len(new_jobs) == 0
    assert len(existing_jobs) == 1

    # record_jobs should update without overwriting APPLIED status or Lever URL
    record_jobs([email_job], db_path=db_file)
    all_jobs = get_latest_jobs(status="ALL", db_path=db_file)
    assert len(all_jobs) == 1
    saved_job = all_jobs[0]
    assert saved_job["company"] == "Hevo Data"
    assert saved_job["status"] == "APPLIED"
    assert "jobs.lever.co" in saved_job["apply_url"]


def test_application_dates_persistence_and_prune_expired(tmp_path: Path) -> None:
    """Verify application start and end dates are persisted, and prune_expired_jobs marks past deadlines as EXPIRED."""
    from gcc_job_radar.db import (
        filter_new_jobs,
        filter_unalerted_jobs,
        get_latest_jobs,
        init_db,
        prune_expired_jobs,
        record_jobs,
    )
    from gcc_job_radar.models import ATSProvider, JobPosting

    db_file = tmp_path / "test_dates_prune.db"
    init_db(db_file)

    # 1. Job with active future deadline
    active_job = JobPosting(
        id="job_active_1",
        company="Snowflake",
        title="Associate Software Engineer",
        location="Bengaluru",
        apply_url="https://jobs.ashbyhq.com/snowflake/1",
        provider=ATSProvider.ASHBY,
        published_date="2026-09-01",
        application_start_date="2026-09-01",
        application_end_date="2026-10-15",
        status="NEW",
    )

    # 1. Job with active future deadline
    active_job = JobPosting(
        id="job_active_1",
        company="Snowflake",
        title="Associate Software Engineer",
        location="Bengaluru",
        apply_url="https://jobs.ashbyhq.com/snowflake/1",
        provider=ATSProvider.ASHBY,
        published_date="2026-09-01",
        application_start_date="2026-09-01",
        application_end_date="2026-10-15",
        status="NEW",
    )

    # 2. Job with deadline in near future (active now, but will expire by Oct 1)
    expiring_job = JobPosting(
        id="job_expiring_2",
        company="OldCo",
        title="Junior Java Developer",
        location="Pune",
        apply_url="https://boards.greenhouse.io/oldco/2",
        provider=ATSProvider.GREENHOUSE,
        published_date="2026-08-01",
        application_start_date="2026-08-01",
        application_end_date="2026-09-25",
        status="NEW",
    )

    # 3. Applied job with deadline in near future
    applied_expiring_job = JobPosting(
        id="job_applied_3",
        company="AppliedCo",
        title="SDE 1",
        location="Bengaluru",
        apply_url="https://jobs.lever.co/appliedco/3",
        provider=ATSProvider.LEVER,
        published_date="2026-08-01",
        application_start_date="2026-08-01",
        application_end_date="2026-09-25",
        status="APPLIED",
    )

    # 4. Job that is ALREADY expired when scraped
    already_expired = JobPosting(
        id="job_past_4",
        company="PastCo",
        title="Software Intern",
        location="Remote",
        apply_url="https://jobs.lever.co/pastco/4",
        provider=ATSProvider.LEVER,
        published_date="2026-08-01",
        application_start_date="2026-08-01",
        application_end_date="2026-09-05",
        status="NEW",
    )

    record_jobs([active_job, expiring_job, applied_expiring_job, already_expired], db_path=db_file)

    # Verify dates were persisted and already_expired was immediately marked EXPIRED
    all_jobs = get_latest_jobs(status="ALL", db_path=db_file)
    assert len(all_jobs) == 4
    active_record = next(j for j in all_jobs if j["company"] == "Snowflake")
    assert active_record["application_start_date"] == "2026-09-01"
    assert active_record["application_end_date"] == "2026-10-15"
    assert active_record["status"] == "NEW"
    assert active_record["is_active"] == 1

    past_record = next(j for j in all_jobs if j["company"] == "PastCo")
    assert past_record["status"] == "EXPIRED"
    assert past_record["is_active"] == 0

    expiring_record = next(j for j in all_jobs if j["company"] == "OldCo")
    assert expiring_record["status"] == "NEW"
    assert expiring_record["is_active"] == 1

    applied_record = next(j for j in all_jobs if j["company"] == "AppliedCo")
    assert applied_record["status"] == "APPLIED"
    assert applied_record["is_active"] == 1

    # Fast forward time to Oct 1: prune_expired_jobs should expire expiring_job and deactivate applied_expiring_job
    pruned_count = prune_expired_jobs(db_path=db_file, ref_date="2026-10-01")
    assert pruned_count == 2  # OldCo + AppliedCo

    # Check statuses after pruning
    latest_all = get_latest_jobs(status="ALL", db_path=db_file)
    pruned_oldco = next(j for j in latest_all if j["company"] == "OldCo")
    assert pruned_oldco["status"] == "EXPIRED"
    assert pruned_oldco["is_active"] == 0

    pruned_applied = next(j for j in latest_all if j["company"] == "AppliedCo")
    assert pruned_applied["status"] == "APPLIED"  # Status preserved!
    assert pruned_applied["is_active"] == 0

    # get_latest_jobs with default NEW should only return active_job (Snowflake)
    latest_new = get_latest_jobs(status="NEW", db_path=db_file)
    assert len(latest_new) == 1
    assert latest_new[0]["company"] == "Snowflake"

    # filter_new_jobs should not surface expired roles as new
    new_jobs, existing_jobs = filter_new_jobs([already_expired, expiring_job], db_path=db_file)
    assert len(new_jobs) == 0
    assert len(existing_jobs) == 2

    # filter_unalerted_jobs should discard expired roles
    unalerted = filter_unalerted_jobs([already_expired, active_job], "telegram", db_path=db_file)
    assert len(unalerted) == 1
    assert unalerted[0].id == "job_active_1"





