from pathlib import Path
import subprocess
import pytest

from gcc_job_radar.models import ATSProvider, JobPosting
from gcc_job_radar.resume_tailor_bridge import (
    DEFAULT_MODEL,
    get_builder_script_path,
    get_master_resume_path,
    sanitize_filename,
    should_tailor_resume,
    tailor_resume_for_job,
)


@pytest.fixture
def valid_entry_job() -> JobPosting:
    return JobPosting(
        id="job-swe-1",
        company="Databricks",
        title="Software Engineer 1",
        location="Bengaluru, India",
        apply_url="https://boards.greenhouse.io/databricks/jobs/101",
        published_date="2026-09-01",
        provider=ATSProvider.GREENHOUSE,
        description="Looking for an entry level software engineer with 0-1 years of experience in Python and Java.",
    )


def test_sanitize_filename():
    assert sanitize_filename("Electronic Arts") == "Electronic_Arts"
    assert sanitize_filename("Software Engineer (Entry-Level)") == "Software_Engineer_Entry_Level"
    assert sanitize_filename("!@#$%^&*()") == "tailored"


def test_should_tailor_resume_positive(valid_entry_job: JobPosting):
    assert should_tailor_resume(valid_entry_job) is True


def test_should_tailor_resume_remote():
    job = JobPosting(
        id="job-remote-1",
        company="Stripe",
        title="Associate Backend Engineer",
        location="Remote - India",
        apply_url="https://stripe.com/jobs/102",
        provider=ATSProvider.ASHBY,
        is_remote=True,
    )
    assert should_tailor_resume(job) is True


def test_should_tailor_resume_rejects_senior():
    job = JobPosting(
        id="job-senior",
        company="Google",
        title="Senior Staff Software Engineer (8+ yrs)",
        location="Bengaluru, India",
        apply_url="https://careers.google.com/jobs/103",
        provider=ATSProvider.GREENHOUSE,
    )
    assert should_tailor_resume(job) is False


def test_should_tailor_resume_rejects_foreign_location():
    job = JobPosting(
        id="job-us",
        company="Meta",
        title="Software Engineer 1",
        location="Menlo Park, CA, USA",
        apply_url="https://metacareers.com/jobs/104",
        provider=ATSProvider.LEVER,
    )
    assert should_tailor_resume(job) is False


def test_should_tailor_resume_rejects_non_tech():
    job = JobPosting(
        id="job-recruiter",
        company="Amazon",
        title="Senior Technical Recruiter",
        location="Hyderabad, India",
        apply_url="https://amazon.jobs/jobs/105",
        provider=ATSProvider.AMAZON,
    )
    assert should_tailor_resume(job) is False


def test_tailor_resume_subprocess_call_success(valid_entry_job: JobPosting, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Verify subprocess.run is called with correct arguments and returns paths when files are produced."""
    called_cmds = []

    def mock_subprocess_run(cmd, *args, **kwargs):
        called_cmds.append(cmd)
        out_idx = cmd.index("--output")
        out_tex_path = Path(cmd[out_idx + 1])
        out_tex_path.parent.mkdir(parents=True, exist_ok=True)
        out_tex_path.write_text("% Mock Tailored TeX", encoding="utf-8")
        out_pdf_path = out_tex_path.with_suffix(".pdf")
        out_pdf_path.write_bytes(b"%PDF-1.4 Mock")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="[4/4] Successfully saved", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)

    tex, pdf = tailor_resume_for_job(valid_entry_job, output_dir=tmp_path / "tailored")
    assert tex is not None
    assert tex.endswith(".tex")
    assert pdf is not None
    assert pdf.endswith(".pdf")

    assert len(called_cmds) == 1
    cmd = called_cmds[0]
    assert "--company" in cmd
    assert cmd[cmd.index("--company") + 1] == "Databricks"
    assert "--role" in cmd
    assert cmd[cmd.index("--role") + 1] == "Software Engineer 1"
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == DEFAULT_MODEL
    assert "--compile" in cmd


def test_tailor_resume_subprocess_failure_groq_down(valid_entry_job: JobPosting, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Verify Groq API failure (mocked returncode 1) is handled gracefully and returns (None, None)."""
    def mock_subprocess_run(cmd, *args, **kwargs):
        return subprocess.CompletedProcess(
            cmd,
            returncode=1,
            stdout="",
            stderr="[error] API call failed: HTTP Error 503: Service Unavailable (Groq down)",
        )

    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)

    tex, pdf = tailor_resume_for_job(valid_entry_job, output_dir=tmp_path / "tailored")
    assert tex is None
    assert pdf is None


def test_tailor_resume_subprocess_timeout(valid_entry_job: JobPosting, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Verify subprocess timeout is caught cleanly and returns (None, None)."""
    def mock_subprocess_run(cmd, *args, **kwargs):
        raise subprocess.TimeoutExpired(cmd, timeout=10.0)

    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)

    tex, pdf = tailor_resume_for_job(valid_entry_job, output_dir=tmp_path / "tailored")
    assert tex is None
    assert pdf is None


def test_resume_tailor_sanitize_latex():
    """Verify Unicode non-breaking hyphens, en/em-dashes, and quotes are normalized for LaTeX and Windows cp1252."""
    import sys
    sys.path.append("builder")
    from resume_tailor import sanitize_latex

    raw = "full\u2011stack \u2013 distributed \u2014 \u2018secure\u2019 \u201ccloud\u201d"
    sanitized = sanitize_latex(raw)
    assert "\u2011" not in sanitized
    assert "full-stack" in sanitized
    assert " -- distributed --- " in sanitized
    assert "`secure'" in sanitized
    assert "``cloud''" in sanitized


def test_detect_candidate_stack_gaps_and_format_note():
    """Verify JD-named stack items absent from candidate's profile are surfaced in note."""
    from gcc_job_radar.resume_tailor_bridge import (
        detect_candidate_stack_gaps,
        format_stack_gap_note,
        TailorResult,
    )

    mock_master = """
    Languages: Java (Core), SQL
    Backend: Spring Boot 3, RESTful APIs, Node.js
    Databases: MySQL, MongoDB
    Tools: Docker, Git, GitHub Actions
    """

    # Revolut-style JD with GCP, Kubernetes, and PostgreSQL (plus Java and Spring Boot from candidate profile)
    revolut_jd = (
        "Join Revolut Graduate Programme. Looking for engineers with strong Java and Spring Boot. "
        "Experience with GCP, Kubernetes, and PostgreSQL is required."
    )

    gaps = detect_candidate_stack_gaps(revolut_jd, master_resume_text=mock_master)
    assert "Kubernetes" in gaps
    assert "GCP" in gaps
    assert "PostgreSQL" in gaps
    # Java and Spring Boot should NOT be flagged as gaps
    assert "Java" not in gaps
    assert "Spring Boot" not in gaps

    note = format_stack_gap_note(gaps)
    assert note is not None
    assert "Note: this JD specifically asks for" in note
    assert "GCP" in note
    assert "Kubernetes" in note
    assert "PostgreSQL" in note
    assert "which aren't reflected in your current profile — worth knowing before applying." in note

    # JD with matching stack only -> no gaps
    matching_jd = "Java, Spring Boot, Docker, MySQL, and REST APIs."
    no_gaps = detect_candidate_stack_gaps(matching_jd, master_resume_text=mock_master)
    assert no_gaps == []
    assert format_stack_gap_note(no_gaps) is None


def test_tailor_result_backward_compatibility():
    """Confirm TailorResult behaves as 2-tuple for backwards compatibility and carries gap_note."""
    from gcc_job_radar.resume_tailor_bridge import TailorResult

    res = TailorResult("tailored.tex", "tailored.pdf", gap_note="Note: JD asks for GCP.")
    # 1. 2-tuple unpacking
    tex, pdf = res
    assert tex == "tailored.tex"
    assert pdf == "tailored.pdf"
    assert len(res) == 2

    # 2. Named attributes
    assert res.tex_path == "tailored.tex"
    assert res.pdf_path == "tailored.pdf"
    assert res.gap_note == "Note: JD asks for GCP."
