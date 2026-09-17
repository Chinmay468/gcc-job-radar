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
    """Resolve the path to resume_tailor.py in the builder submodule or local projects."""
    candidates = [
        Path("builder/resume_tailor.py"),
        Path(__file__).resolve().parent.parent / "builder" / "resume_tailor.py",
        Path("D:/Projects/Resume Builder/resume_tailor.py"),
    ]
    for p in candidates:
        if p.is_file():
            return p.resolve()
    return None


def get_master_resume_path() -> Optional[Path]:
    """Resolve master_resume.tex from environment or builder repository."""
    env_path = os.getenv("MASTER_RESUME_PATH")
    if env_path and Path(env_path).is_file():
        return Path(env_path).resolve()

    candidates = [
        Path("master_resume.tex"),
        Path("builder/master_resume.tex"),
        Path(__file__).resolve().parent.parent / "builder" / "master_resume.tex",
        Path("D:/Projects/Resume Builder/master_resume.tex"),
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


def tailor_resume_for_job(
    job: JobPosting,
    output_dir: Union[str, Path] = "tailored",
    model: str = DEFAULT_MODEL,
    compile_pdf: bool = True,
    timeout: float = 90.0,
    force: bool = False,
) -> tuple[Optional[str], Optional[str]]:
    """Invoke resume_tailor.py as a subprocess to generate tailored LaTeX and PDF resumes.

    Returns:
        (tex_path, pdf_path): Tuple containing relative paths to generated files, or (None, None).
    """
    if not force and not should_tailor_resume(job):
        logger.debug("Skipping resume tailoring for non-matching role: %s - %s", job.company, job.title)
        return None, None

    script_path = get_builder_script_path()
    if not script_path:
        logger.warning("resume_tailor.py not found. Ensure the builder submodule is checked out.")
        return None, None

    master_resume = get_master_resume_path()
    if not master_resume:
        logger.warning("master_resume.tex not found. Skipping tailoring.")
        return None, None

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
            return None, None
    except subprocess.TimeoutExpired:
        logger.warning("resume_tailor.py timed out after %s seconds for %s", timeout, job.company)
        return None, None
    except Exception as exc:
        logger.warning("Error invoking resume_tailor.py: %s", exc)
        return None, None

    tex_path = str(out_tex) if out_tex.is_file() else None
    pdf_candidate = out_tex.with_suffix(".pdf")
    pdf_path = str(pdf_candidate) if pdf_candidate.is_file() else None

    return tex_path, pdf_path
