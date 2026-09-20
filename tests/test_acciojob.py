"""Tests for AccioJob harvester, digest ingestion, stack qualification, and company auto-registration."""

from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest

from gcc_job_radar.clients.acciojob import (
    AccioJobClient,
    ingest_acciojob_digest_text,
    is_job_good_fit,
    parse_acciojob_digest_table,
)
from gcc_job_radar.company_tracker import (
    is_generic_or_invalid_company,
    is_known_company,
    register_hiring_company,
)
from gcc_job_radar.db import init_db
from gcc_job_radar.models import ATSProvider, CompanyConfig, JobPosting
from tools.ingest_acciojob import run_acciojob_pipeline


SAMPLE_ACCIOJOB_DIGEST = """InnovalQ Technologies\tData Analyst\t
SQL
Excel
Python
PowerBI
19 Sept 2026\tunstop\t
Appscrip\tReactJS Developer\t
React.js
HTML
CSS
JavaScript
19 Sept 2026\tCutshort.io\t
ReliaQuest\tAssociate Software Engineer\t
Java
SpringBoot
19 Sept 2026\tCompany's Career Page\t
⚡ Fast Track Placement Program

Bridge your skill gaps with expert mentorship and customised training to become job-ready and get placed faster.

₹55,000
(Scholarships available)\t
Hewlett Packard Enterprise\tExternal Intern\t
Java
19 Sept 2026\tCompany's Career Page\t
Docusign\tSoftware Engineer\t
React
Node.js
JavaScript
19 Sept 2026\tCompany's Career Page\t
KIMS Hospitals\tMis Executive\t
Excel
19 Sept 2026\tNaukri\t"""


def test_is_job_good_fit_core_stack():
    """Verify is_job_good_fit approves core stack roles and rejects non-tech/promos."""
    # Good fits
    good, score, _ = is_job_good_fit("Associate Software Engineer", "Java, SpringBoot", "ReliaQuest")
    assert good is True
    assert score >= 20

    good, score, _ = is_job_good_fit("ReactJS Developer", "React.js, HTML, CSS, JavaScript", "Appscrip")
    assert good is True

    good, score, _ = is_job_good_fit("Software Engineer", "React, Node.js", "Docusign")
    assert good is True

    good, score, _ = is_job_good_fit("External Intern", "Java", "Hewlett Packard Enterprise")
    assert good is True

    # Bad fits
    bad_promo, _, _ = is_job_good_fit("⚡ Fast Track Placement Program", "₹55,000", "AccioJob")
    assert bad_promo is False

    bad_mis, _, _ = is_job_good_fit("Mis Executive", "Excel", "KIMS Hospitals")
    assert bad_mis is False

    bad_exp, _, _ = is_job_good_fit("Senior Architect", "Requires 5+ years experience", "TechFirm")
    assert bad_exp is False


def test_parse_acciojob_digest_table():
    """Verify parse_acciojob_digest_table extracts structured records and skips ads."""
    records = parse_acciojob_digest_table(SAMPLE_ACCIOJOB_DIGEST)
    assert len(records) >= 5
    companies = [r["company"] for r in records]
    assert "ReliaQuest" in companies
    assert "Appscrip" in companies
    assert "Docusign" in companies
    assert not any("fast track" in c.lower() for c in companies)


def test_ingest_acciojob_digest_text(tmp_path: Path):
    """Verify ingest_acciojob_digest_text converts digest to qualified JobPostings."""
    postings = ingest_acciojob_digest_text(SAMPLE_ACCIOJOB_DIGEST)
    assert len(postings) >= 4
    companies = {p.company for p in postings}
    assert "ReliaQuest" in companies
    assert "Appscrip" in companies
    assert "Docusign" in companies
    assert "Hewlett Packard Enterprise" in companies
    for p in postings:
        assert p.provider == ATSProvider.ACCIOJOB
        assert p.location == "India"


def test_is_generic_or_invalid_company():
    """Verify company validator rejects invalid and aggregator placeholders."""
    assert is_generic_or_invalid_company("AccioJob") is True
    assert is_generic_or_invalid_company("AccioMatrix") is True
    assert is_generic_or_invalid_company("LinkedIn Employer") is True
    assert is_generic_or_invalid_company("Naukri") is True
    assert is_generic_or_invalid_company("Confidential") is True
    assert is_generic_or_invalid_company("ReliaQuest") is False
    assert is_generic_or_invalid_company("Docusign") is False


def test_register_hiring_company_custom(tmp_path: Path):
    """Verify register_hiring_company persists new custom company config."""
    dummy_config = tmp_path / "config.py"
    dummy_config.write_text(
        'from gcc_job_radar.models import ATSProvider, CompanyConfig\n\n'
        'COMPANIES: list[CompanyConfig] = [\n'
        '    CompanyConfig(name="Dummy1", provider=ATSProvider.GREENHOUSE, board_token="dummy1"),\n'
        ']\n\n# Strict entry-level\n',
        encoding="utf-8",
    )

    with patch("gcc_job_radar.company_tracker.CONFIG_PATH", dummy_config), \
         patch("gcc_job_radar.company_tracker.probe_ats_for_company", new_callable=AsyncMock, return_value=None):
        cfg = register_hiring_company(
            "TestTechCorp",
            career_url="https://testtechcorp.com/careers",
            config_path=dummy_config,
        )
        assert cfg is not None
        assert cfg.name == "TestTechCorp"
        assert cfg.provider == ATSProvider.CUSTOM
        assert "TestTechCorp" in dummy_config.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_run_acciojob_pipeline(tmp_path: Path):
    """Verify run_acciojob_pipeline end-to-end persists into SQLite."""
    db_file = tmp_path / "accio_test.db"
    init_db(db_file)

    postings = await run_acciojob_pipeline(
        raw_text=SAMPLE_ACCIOJOB_DIGEST,
        scrape=False,
        db_path=db_file,
    )
    assert len(postings) >= 4
