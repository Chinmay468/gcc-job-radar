"""Subprocess bridge to Builder (resume_tailor.py) for automated resume tailoring."""

import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Optional, Union

from gcc_job_radar.filters import is_entry_level, is_remote_opening, matches_india_location
from gcc_job_radar.models import JobPosting

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")


def get_builder_script_path() -> Optional[Path]:
    """Resolve the path to resume_tailor.py in the builder directory."""
    candidates = [
        Path("builder/resume_tailor.py"),
        Path(__file__).resolve().parent.parent / "builder" / "resume_tailor.py",
    ]
    for p in candidates:
        if p.is_file():
            return p.resolve()
    return None


def get_master_resume_path() -> Optional[Path]:
    """Resolve master_resume.tex from environment or builder directory."""
    env_path = os.getenv("MASTER_RESUME_PATH")
    if env_path and Path(env_path).is_file():
        return Path(env_path).resolve()

    candidates = [
        Path("master_resume.tex"),
        Path("builder/master_resume.tex"),
        Path(__file__).resolve().parent.parent / "builder" / "master_resume.tex",
    ]
    for p in candidates:
        if p.is_file():
            return p.resolve()
    return None


def sanitize_filename(name: str) -> str:
    """Convert company and role into a safe filename tag."""
    clean = re.sub(r"[^\w]", "_", (name or "").strip())
    clean = re.sub(r"_+", "_", clean).strip("_")
    return clean or "tailored"


def should_tailor_resume(job: JobPosting) -> bool:
    """Guard resume tailoring behind strict entry-level title and India location filters."""
    # 1. Location check: Must match Indian tech cities or India remote
    if not (matches_india_location(job.location) or is_remote_opening(job)):
        return False

    # 2. Strict entry-level tech title check
    if not is_entry_level(job, content=job.description or ""):
        return False

    return True


# Notable tech stack keywords to detect gaps against candidate's master profile
KNOWN_TECH_CATALOG: list[tuple[str, re.Pattern[str]]] = [
    ("Kubernetes", re.compile(r"(?i)\b(?:kubernetes|k8s)\b")),
    ("GCP", re.compile(r"(?i)\b(?:gcp|google\s+cloud(?:\s+platform)?)\b")),
    ("AWS", re.compile(r"(?i)\b(?:aws|amazon\s+web\s+services)\b")),
    ("Azure", re.compile(r"(?i)\b(?:azure|microsoft\s+azure)\b")),
    ("Terraform", re.compile(r"(?i)\bterraform\b")),
    ("PostgreSQL", re.compile(r"(?i)\b(?:postgresql|postgres)\b")),
    ("Go / Golang", re.compile(r"(?i)\b(?:golang|go\s+language)\b")),
    ("C++", re.compile(r"(?i)\bc\+\+\b")),
    ("C# / .NET", re.compile(r"(?i)\b(?:c#|\.net|dotnet)\b")),
    ("GraphQL", re.compile(r"(?i)\bgraphql\b")),
    ("Angular", re.compile(r"(?i)\bangular(?:\.js)?\b")),
    ("Vue.js", re.compile(r"(?i)\bvue(?:\.js)?\b")),
    ("Rust", re.compile(r"(?i)\brust\b")),
    ("Elasticsearch", re.compile(r"(?i)\belasticsearch\b")),
    ("Cassandra", re.compile(r"(?i)\bcassandra\b")),
    ("DynamoDB", re.compile(r"(?i)\bdynamodb\b")),
    ("Snowflake", re.compile(r"(?i)\bsnowflake\b")),
]


def detect_candidate_stack_gaps(job_description: str, master_resume_text: Optional[str] = None) -> list[str]:
    """Identify JD-named technologies that do not appear anywhere in the candidate's master profile."""
    if not job_description:
        return []

    if master_resume_text is None:
        p = get_master_resume_path()
        if p and p.is_file():
            try:
                master_resume_text = p.read_text(encoding="utf-8")
            except Exception:
                master_resume_text = ""
        else:
            master_resume_text = ""

    master_lower = (master_resume_text or "").lower()
    gaps: list[str] = []

    for label, pattern in KNOWN_TECH_CATALOG:
        if pattern.search(job_description):
            if not pattern.search(master_lower):
                gaps.append(label)

    return gaps


def format_stack_gap_note(gaps: list[str]) -> Optional[str]:
    """Format detected technology gaps into an informational one-line note."""
    if not gaps:
        return None

    if len(gaps) == 1:
        tech_str = gaps[0]
        verb = "isn't"
    elif len(gaps) == 2:
        tech_str = f"{gaps[0]} and {gaps[1]}"
        verb = "aren't"
    else:
        tech_str = f"{', '.join(gaps[:-1])}, and {gaps[-1]}"
        verb = "aren't"

    return f"Note: this JD specifically asks for {tech_str}, which {verb} reflected in your current profile — worth knowing before applying."


class TailorResult(tuple):
    """2-tuple (tex_path, pdf_path) with gap_note attribute, preserving 100% backward compatibility."""

    def __new__(cls, tex_path: Optional[str], pdf_path: Optional[str], gap_note: Optional[str] = None):
        instance = super().__new__(cls, (tex_path, pdf_path))
        instance.gap_note = gap_note
        return instance

    @property
    def tex_path(self) -> Optional[str]:
        return self[0]

    @property
    def pdf_path(self) -> Optional[str]:
        return self[1]


def tailor_resume_for_job(
    job: JobPosting,
    output_dir: Union[str, Path] = "tailored",
    model: str = DEFAULT_MODEL,
    compile_pdf: bool = True,
    timeout: float = 90.0,
    force: bool = False,
) -> TailorResult:
    """Invoke resume_tailor.py as a subprocess to generate tailored LaTeX and PDF resumes.

    Returns:
        TailorResult: (tex_path, pdf_path) tuple with .gap_note attribute.
    """
    if not force and not should_tailor_resume(job):
        logger.debug("Skipping resume tailoring for non-matching role: %s - %s", job.company, job.title)
        return TailorResult(None, None, gap_note=None)

    script_path = get_builder_script_path()
    if not script_path:
        logger.warning("resume_tailor.py not found. Ensure the builder submodule is checked out.")
        return TailorResult(None, None, gap_note=None)

    master_resume = get_master_resume_path()
    if not master_resume:
        logger.warning("master_resume.tex not found. Skipping tailoring.")
        return TailorResult(None, None, gap_note=None)

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Ensure resume.cls is in the output directory so pdflatex can compile without missing class errors
    cls_source = master_resume.parent / "resume.cls"
    if cls_source.is_file() and not (out_dir / "resume.cls").is_file():
        try:
            shutil.copy2(cls_source, out_dir / "resume.cls")
        except Exception:
            pass

    tag = f"{sanitize_filename(job.company)}_{sanitize_filename(job.title)}"
    out_tex = out_dir / f"{tag}.tex"

    # Prepare job description text (pass rich fallback if description is empty or < 30 chars)
    job_desc = (job.description or "").strip()
    if len(job_desc) < 30:
        job_desc = (
            f"Company: {job.company}\n"
            f"Role: {job.title}\n"
            f"Location: {job.location}\n"
            f"Entry-level software engineering position requiring core computer science skills, "
            f"problem solving, data structures, and backend/frontend application development."
        )

    cmd = [
        sys.executable,
        str(script_path),
        "--resume",
        str(master_resume),
        "--job",
        job_desc,
        "--company",
        job.company,
        "--role",
        job.title,
        "--output",
        str(out_tex),
        "--model",
        model,
    ]
    if compile_pdf:
        cmd.append("--compile")

    logger.info("Tailoring resume for %s - %s via Groq model %s...", job.company, job.title, model)
    try:
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
        if res.returncode != 0:
            logger.warning("resume_tailor.py failed (code %d): %s", res.returncode, res.stderr or res.stdout)
            return TailorResult(None, None, gap_note=None)
    except subprocess.TimeoutExpired:
        logger.warning("resume_tailor.py timed out after %s seconds for %s", timeout, job.company)
        return TailorResult(None, None, gap_note=None)
    except Exception as exc:
        logger.warning("Error invoking resume_tailor.py: %s", exc)
        return TailorResult(None, None, gap_note=None)

    # Detect JD stack gaps not present in candidate's profile
    gaps = detect_candidate_stack_gaps(job_desc)
    gap_note = format_stack_gap_note(gaps)
    if gap_note:
        logger.info("Detected JD stack gaps for %s: %s", job.company, gap_note)

    tex_path = str(out_tex) if out_tex.is_file() else None
    pdf_candidate = out_tex.with_suffix(".pdf")
    pdf_path = str(pdf_candidate) if pdf_candidate.is_file() else None

    return TailorResult(tex_path, pdf_path, gap_note=gap_note)
