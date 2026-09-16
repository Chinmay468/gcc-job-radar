"""Tests for Semi-Automated Application Prep engine."""

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from gcc_job_radar.apply_prep import (
    FormPrepResult,
    SUBMIT_BUTTON_SELECTORS,
    draft_screening_answer,
    load_candidate_profile,
    prep_ashby_form,
    prep_greenhouse_form,
    prep_job_application,
    prep_lever_form,
    resolve_candidate_resume_pdf,
)
from gcc_job_radar.db import (
    dismiss_selectors_or_companies,
    init_db,
    record_apply_prep_attempt,
    record_jobs,
)
from gcc_job_radar.dormant_companies import DORMANT_REGISTRY
from gcc_job_radar.models import ATSProvider, JobPosting


@pytest.fixture
def sample_profile() -> dict:
    return {
        "name": "Chinmay Maheshwari",
        "first_name": "Chinmay",
        "last_name": "Maheshwari",
        "email": "chinmaymaheshwari.it27@gmail.com",
        "phone": "+91 9460449962",
        "location": "Udaipur, India",
        "linkedin": "https://linkedin.com/in/chinmay8064/",
        "github": "https://github.com/Chinmay0608",
        "portfolio": "https://portfolio-olive-nine-39.vercel.app",
        "degree": "Bachelor of Technology in Information Technology",
        "college": "Jaipur Engineering College and Research Centre",
        "graduation": "Expected May/June 2027",
        "cgpa": "8.8",
    }


@pytest.fixture
def sample_greenhouse_job() -> JobPosting:
    return JobPosting(
        id="gh_12345",
        numeric_id=101,
        company="Databricks",
        title="Software Engineer, University Graduate",
        location="Bengaluru, India",
        apply_url="https://boards.greenhouse.io/databricks/jobs/12345",
        provider=ATSProvider.GREENHOUSE,
    )


@pytest.fixture
def sample_lever_job() -> JobPosting:
    return JobPosting(
        id="lev_67890",
        numeric_id=102,
        company="Atlassian",
        title="Associate Software Engineer",
        location="Bengaluru, India",
        apply_url="https://jobs.lever.co/atlassian/67890",
        provider=ATSProvider.LEVER,
    )


@pytest.fixture
def sample_ashby_job() -> JobPosting:
    return JobPosting(
        id="ash_11223",
        numeric_id=103,
        company="Linear",
        title="Junior Backend Engineer",
        location="India, Remote",
        apply_url="https://jobs.ashbyhq.com/linear/11223",
        provider=ATSProvider.ASHBY,
    )


@pytest.fixture
def sample_workday_job() -> JobPosting:
    return JobPosting(
        id="wd_99887",
        numeric_id=104,
        company="Palo Alto Networks",
        title="Associate Software Engineer",
        location="Bengaluru, India",
        apply_url="https://paloaltonetworks.wd5.myworkdayjobs.com/panwexternalcareers/job/99887",
        provider=ATSProvider.WORKDAY,
    )


# ---------------------------------------------------------------------------
# 1. Profile Loader & Resume Resolution
# ---------------------------------------------------------------------------


def test_load_candidate_profile() -> None:
    profile = load_candidate_profile()
    assert "name" in profile
    assert "email" in profile
    assert "phone" in profile
    assert "linkedin" in profile
    assert "github" in profile


def test_load_candidate_profile_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CANDIDATE_NAME", "Jane Doe")
    monkeypatch.setenv("CANDIDATE_EMAIL", "jane@example.com")
    profile = load_candidate_profile()
    assert profile["name"] == "Jane Doe"
    assert profile["first_name"] == "Jane"
    assert profile["last_name"] == "Doe"
    assert profile["email"] == "jane@example.com"


def test_resolve_candidate_resume_pdf(tmp_path: Path) -> None:
    test_pdf = tmp_path / "custom_resume.pdf"
    test_pdf.write_text("%PDF-1.4 dummy content")
    resolved = resolve_candidate_resume_pdf(str(test_pdf))
    assert resolved is not None
    assert resolved.name == "custom_resume.pdf"


# ---------------------------------------------------------------------------
# 2. Fact Integrity & AI Screening Question Drafter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_draft_screening_answer_factual(sample_greenhouse_job: JobPosting, sample_profile: dict) -> None:
    # Work auth
    ans, is_ai = await draft_screening_answer("Are you legally authorized to work in India?", sample_greenhouse_job, sample_profile)
    assert "Yes" in ans
    assert "India" in ans
    assert is_ai is False

    # Visa sponsorship
    ans, is_ai = await draft_screening_answer("Will you require visa sponsorship now or in the future?", sample_greenhouse_job, sample_profile)
    assert "No" in ans
    assert is_ai is False

    # Notice period
    ans, is_ai = await draft_screening_answer("What is your notice period / availability?", sample_greenhouse_job, sample_profile)
    assert "May 2027" in ans or "Available" in ans
    assert is_ai is False

    # Graduation year
    ans, is_ai = await draft_screening_answer("What is your year of graduation?", sample_greenhouse_job, sample_profile)
    assert "2027" in ans
    assert is_ai is False

    # GPA
    ans, is_ai = await draft_screening_answer("What is your CGPA / percentage?", sample_greenhouse_job, sample_profile)
    assert "8.8" in ans
    assert is_ai is False

    # College
    ans, is_ai = await draft_screening_answer("What university/college did you attend?", sample_greenhouse_job, sample_profile)
    assert "Jaipur" in ans
    assert is_ai is False


@pytest.mark.asyncio
async def test_draft_screening_answer_no_salary_hallucination(sample_greenhouse_job: JobPosting, sample_profile: dict) -> None:
    ans, is_ai = await draft_screening_answer("What are your expected salary / CTC expectations?", sample_greenhouse_job, sample_profile)
    # Must flag manual input rather than inventing numbers
    assert "[NEEDS MANUAL INPUT" in ans
    assert is_ai is False


@pytest.mark.asyncio
async def test_draft_screening_answer_ai_motivation(sample_greenhouse_job: JobPosting, sample_profile: dict) -> None:
    with patch("gcc_job_radar.ai_agent.ask_ai_agent", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "I am excited to apply for Databricks because of your industry-leading data platform. My experience with Java, Spring Boot, and Kafka matches your distributed systems focus."
        ans, is_ai = await draft_screening_answer("Why do you want to work at Databricks?", sample_greenhouse_job, sample_profile)
        assert is_ai is True
        assert "Databricks" in ans


# ---------------------------------------------------------------------------
# 3. Tier A Form Prep Logic (Mocked DOM) & Strict Submit Avoidance
# ---------------------------------------------------------------------------


class MockLocator:
    def __init__(self, elements=None):
        self.elements = elements or []

    async def count(self) -> int:
        return len(self.elements)

    @property
    def first(self):
        return self.elements[0] if self.elements else MockElement()

    def nth(self, idx: int):
        return self.elements[idx] if idx < len(self.elements) else MockElement()

    async def fill(self, value: str) -> None:
        if self.elements:
            await self.elements[0].fill(value)

    async def click(self) -> None:
        raise AssertionError("CRITICAL SAFETY VIOLATION: Submit button or forbidden element was clicked!")


class MockElement:
    def __init__(self, name="", element_id="", input_val="", label=""):
        self.name = name
        self.element_id = element_id
        self.value = input_val
        self.label = label
        self.filled_value = None
        self.uploaded_files = None

    async def count(self) -> int:
        return 1

    async def fill(self, val: str) -> None:
        self.filled_value = val

    async def set_input_files(self, files: str) -> None:
        self.uploaded_files = files

    async def get_attribute(self, attr: str) -> str:
        if attr == "name":
            return self.name
        if attr == "id":
            return self.element_id
        return ""

    async def input_value(self) -> str:
        return self.value

    async def is_visible(self) -> bool:
        return True

    async def inner_text(self) -> str:
        return self.label

    def locator(self, sel: str):
        return MockLocator([self])


class MockPage:
    def __init__(self):
        self.filled: dict[str, str] = {}
        self.files_uploaded: list[str] = []
        self.clicked: list[str] = []

    def locator(self, selector: str):
        # Assert that submit buttons are never targeted for click
        if any(bad in selector for bad in SUBMIT_BUTTON_SELECTORS):
            mock = MockLocator([MockElement(name="submit")])
            mock.click = AsyncMock(side_effect=AssertionError("Submit button clicked!"))
            return mock

        if selector in ("#first_name", "input[name*='first_name']"):
            return MockLocator([MockElement(name="first_name", element_id="first_name")])
        if selector in ("#last_name", "input[name*='last_name']"):
            return MockLocator([MockElement(name="last_name", element_id="last_name")])
        if selector in ("#email", "input[name='email']"):
            return MockLocator([MockElement(name="email", element_id="email")])
        if selector in ("#phone", "input[name='phone']"):
            return MockLocator([MockElement(name="phone", element_id="phone")])
        if selector in ("input[name='name']",):
            return MockLocator([MockElement(name="name")])
        if selector in ("input[name='org']",):
            return MockLocator([MockElement(name="org")])
        if selector.startswith("input[type='file']"):
            return MockLocator([MockElement(name="resume")])
        if "urls[LinkedIn]" in selector:
            return MockLocator([MockElement(name="urls[LinkedIn]")])
        if "urls[GitHub]" in selector:
            return MockLocator([MockElement(name="urls[GitHub]")])
        if "urls[Portfolio]" in selector:
            return MockLocator([MockElement(name="urls[Portfolio]")])

        return MockLocator([])

    async def fill(self, selector: str, value: str) -> None:
        self.filled[selector] = value

    async def goto(self, url: str, **kwargs) -> None:
        pass

    async def wait_for_load_state(self, state: str) -> None:
        pass


@pytest.mark.asyncio
async def test_prep_greenhouse_form(sample_greenhouse_job: JobPosting, sample_profile: dict, tmp_path: Path) -> None:
    mock_page = MockPage()
    dummy_resume = tmp_path / "resume.pdf"
    dummy_resume.write_text("pdf dummy")

    result = await prep_greenhouse_form(mock_page, sample_greenhouse_job, sample_profile, dummy_resume)
    assert any("First Name" in f for f in result["prefilled"])
    assert any("Last Name" in f for f in result["prefilled"])
    assert any("Email" in f for f in result["prefilled"])
    assert any("Phone" in f for f in result["prefilled"])
    assert mock_page.filled.get("#first_name") == "Chinmay"
    assert mock_page.filled.get("#email") == "chinmaymaheshwari.it27@gmail.com"


@pytest.mark.asyncio
async def test_prep_lever_form(sample_lever_job: JobPosting, sample_profile: dict, tmp_path: Path) -> None:
    mock_page = MockPage()
    dummy_resume = tmp_path / "resume.pdf"
    dummy_resume.write_text("pdf dummy")

    result = await prep_lever_form(mock_page, sample_lever_job, sample_profile, dummy_resume)
    assert any("Full Name" in f for f in result["prefilled"])
    assert any("Email" in f for f in result["prefilled"])
    assert mock_page.filled.get("input[name='name']") == "Chinmay Maheshwari"
    assert mock_page.filled.get("input[name='email']") == "chinmaymaheshwari.it27@gmail.com"


@pytest.mark.asyncio
async def test_prep_ashby_form(sample_ashby_job: JobPosting, sample_profile: dict, tmp_path: Path) -> None:
    mock_page = MockPage()
    dummy_resume = tmp_path / "resume.pdf"
    dummy_resume.write_text("pdf dummy")

    result = await prep_ashby_form(mock_page, sample_ashby_job, sample_profile, dummy_resume)
    assert any("Full Name" in f for f in result["prefilled"])
    assert any("Email" in f for f in result["prefilled"])


# ---------------------------------------------------------------------------
# 4. Tier B Draft-Only Fallback & Guardrails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prep_job_application_tier_b_fallback(sample_workday_job: JobPosting, tmp_path: Path) -> None:
    test_db = tmp_path / "test_prep.db"
    init_db(test_db)

    result = await prep_job_application(sample_workday_job, db_path=test_db)
    assert result.status == "FALLBACK_DRAFT"
    assert "Draft-Only" in result.message
    assert "WORKDAY" in result.message
    assert len(result.prefilled_fields) > 0
    assert len(result.drafted_questions) > 0


@pytest.mark.asyncio
async def test_prep_job_application_dormant_company_rejected(tmp_path: Path) -> None:
    test_db = tmp_path / "test_dormant.db"
    init_db(test_db)

    dormant_name = DORMANT_REGISTRY[0].config.name
    job = JobPosting(
        id="dormant_1",
        numeric_id=201,
        company=dormant_name,
        title="Software Engineer",
        location="Bengaluru, India",
        apply_url="https://boards.greenhouse.io/dormant/1",
        provider=ATSProvider.GREENHOUSE,
    )

    result = await prep_job_application(job, db_path=test_db)
    assert result.status == "FAILED"
    assert "dormant" in result.message.lower()


@pytest.mark.asyncio
async def test_prep_job_application_dismissed_company_rejected(tmp_path: Path) -> None:
    test_db = tmp_path / "test_dismissed.db"
    init_db(test_db)

    dismiss_selectors_or_companies(["AcmeCorp"], db_path=test_db)

    job = JobPosting(
        id="acme_1",
        numeric_id=202,
        company="AcmeCorp",
        title="Software Engineer",
        location="Bengaluru, India",
        apply_url="https://boards.greenhouse.io/acme/1",
        provider=ATSProvider.GREENHOUSE,
    )

    result = await prep_job_application(job, db_path=test_db)
    assert result.status == "FAILED"
    assert "dismissed" in result.message.lower()


@pytest.mark.asyncio
async def test_prep_job_application_rate_limiting(sample_greenhouse_job: JobPosting, tmp_path: Path) -> None:
    test_db = tmp_path / "test_ratelimit.db"
    init_db(test_db)

    # First record an attempt
    record_apply_prep_attempt(
        job_id=sample_greenhouse_job.numeric_id,
        company=sample_greenhouse_job.company,
        title=sample_greenhouse_job.title,
        provider=sample_greenhouse_job.provider.value,
        status="SUCCESS",
        details="Prepped earlier",
        db_path=test_db,
    )

    result = await prep_job_application(sample_greenhouse_job, db_path=test_db, force=False)
    assert result.status == "ALREADY_PREPPED"
    assert "already prepared" in result.message.lower()
