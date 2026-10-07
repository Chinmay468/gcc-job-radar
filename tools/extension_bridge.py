#!/usr/bin/env python3
"""
extension_bridge.py — Localhost Bridge Server for the GCC Job Radar Chrome Extension.

Provides HTTP REST endpoints on http://127.0.0.1:8765 for:
  - GET  /health           : Status of bridge, Groq API keys, Tectonic compiler, database
  - POST /evaluate         : Rapid JD fit evaluation (local rules + Groq deep analysis)
  - POST /tailor           : One-click resume tailoring via Groq + instant local PDF compilation via Tectonic (no Overleaf)
  - GET  /download/<file>  : Download compiled PDF directly into Chrome
  - POST /record_job       : Record evaluated/applied job to local gcc_jobs.db and Turso
"""

from __future__ import annotations

import difflib
import http.server
import json
import logging
import os
from pathlib import Path
import re
import shutil
import socketserver
import sys
import urllib.parse
from typing import Any, Dict

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("extension_bridge")

# Import Builder and Radar modules
from builder.evaluate import (
    local_evaluate,
    ai_evaluate,
    get_groq_api_keys,
    CANDIDATE,
)
from builder.resume_tailor import (
    tailor_resume,
    refine_resume,
    compile_pdf,
    load_dotenv_if_present,
)
from gcc_job_radar.db import (
    init_db,
    get_db_path,
    get_stats,
    mark_job_status,
    dismiss_selectors_or_companies,
    save_dismissal_to_registry,
)

load_dotenv_if_present()

BUILDER_DIR = PROJECT_ROOT / "builder"
MASTER_RESUME_PATH = BUILDER_DIR / "master_resume.tex"
OUTPUT_DIR = BUILDER_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Ensure resume.cls and master_resume.pdf are available in output directory
CLS_SOURCE = BUILDER_DIR / "resume.cls"
CLS_DEST = OUTPUT_DIR / "resume.cls"
if CLS_SOURCE.exists() and not CLS_DEST.exists():
    try:
        shutil.copy2(CLS_SOURCE, CLS_DEST)
    except Exception as e:
        logger.warning(f"Could not copy resume.cls to output dir: {e}")

MASTER_PDF_SOURCE = BUILDER_DIR / "master_resume.pdf"
MASTER_PDF_DEST = OUTPUT_DIR / "master_resume.pdf"
if MASTER_PDF_SOURCE.exists():
    try:
        shutil.copy2(MASTER_PDF_SOURCE, MASTER_PDF_DEST)
    except Exception as e:
        logger.warning(f"Could not copy master_resume.pdf to output dir: {e}")

PORT = 8765


class BridgeHTTPRequestHandler(http.server.BaseHTTPRequestHandler):
    """Handles requests from the Chrome Extension with full CORS support."""

    def _set_cors_headers(self, content_type: str = "application/json"):
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With")

    def do_OPTIONS(self):
        """Respond to CORS preflight checks."""
        self.send_response(204)
        self._set_cors_headers()
        self.end_headers()

    def _send_json(self, status_code: int, data: Dict[str, Any]):
        body = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self._set_cors_headers("application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> Dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length <= 0:
            return {}
        raw = self.rfile.read(content_length).decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def do_HEAD(self):
        url_parts = urllib.parse.urlparse(self.path)
        path = url_parts.path

        if path in ("/", "/health", "/status"):
            self.send_response(200)
            self._set_cors_headers("application/json")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path.startswith("/download/"):
            filename = urllib.parse.unquote(path[len("/download/"):].strip())
            self.handle_download(filename, as_attachment=True, head_only=True)
        elif path.startswith("/view/"):
            filename = urllib.parse.unquote(path[len("/view/"):].strip())
            self.handle_download(filename, as_attachment=False, head_only=True)
        else:
            self.send_response(404)
            self._set_cors_headers("application/json")
            self.end_headers()

    def do_GET(self):
        url_parts = urllib.parse.urlparse(self.path)
        path = url_parts.path

        if path in ("/", "/health", "/status"):
            self.handle_health()
        elif path == "/lookup_company":
            query_params = urllib.parse.parse_qs(url_parts.query)
            company = query_params.get("company", [""])[0]
            title = query_params.get("title", [""])[0]
            url = query_params.get("url", [""])[0]
            self.handle_lookup_company(company, title, url)
        elif path.startswith("/download/"):
            filename = urllib.parse.unquote(path[len("/download/"):].strip())
            self.handle_download(filename, as_attachment=True)
        elif path.startswith("/view/"):
            filename = urllib.parse.unquote(path[len("/view/"):].strip())
            self.handle_download(filename, as_attachment=False)
        elif path == "/candidate_profile":
            self.handle_get_candidate_profile()
        else:
            self._send_json(404, {"error": "Not Found", "path": path})

    def do_POST(self):
        url_parts = urllib.parse.urlparse(self.path)
        path = url_parts.path

        if path == "/evaluate":
            self.handle_evaluate()
        elif path == "/lookup_company":
            body = self._read_json_body()
            self.handle_lookup_company(
                body.get("company", ""),
                body.get("title", ""),
                body.get("url", ""),
            )
        elif path == "/tailor":
            self.handle_tailor()
        elif path == "/refine":
            self.handle_refine()
        elif path == "/record_job":
            self.handle_record_job()
        elif path == "/dismiss":
            self.handle_dismiss()
        elif path == "/generate_outreach":
            self.handle_generate_outreach()
        elif path == "/candidate_profile":
            self.handle_save_candidate_profile()
        elif path == "/answer_screening_question":
            self.handle_answer_screening_question()
        else:
            self._send_json(404, {"error": "Not Found", "path": path})

    def handle_health(self):
        """Returns health and readiness status."""
        groq_keys = get_groq_api_keys()
        tectonic_path = PROJECT_ROOT / "tools" / "bin" / "tectonic.exe"
        tectonic_available = tectonic_path.exists() or bool(shutil.which("tectonic"))
        master_resume_available = MASTER_RESUME_PATH.exists()

        db_path = get_db_path()
        init_db(db_path)
        stats = get_stats(db_path)
        total_jobs = stats.get("active_count", 0)
        applied_jobs = stats.get("applied_count", 0)

        status_data = {
            "status": "healthy",
            "service": "gcc-job-radar-bridge",
            "version": "1.0.0",
            "groq": {
                "configured": len(groq_keys) > 0,
                "keys_count": len(groq_keys),
            },
            "tectonic": {
                "available": tectonic_available,
                "path": str(tectonic_path) if tectonic_path.exists() else shutil.which("tectonic"),
            },
            "master_resume": {
                "exists": master_resume_available,
                "path": str(MASTER_RESUME_PATH),
            },
            "database": {
                "active_jobs": total_jobs,
                "applied_jobs": applied_jobs,
                "total_tracked": stats.get("total_tracked", 0),
            },
            "candidate": {
                "name": CANDIDATE.get("name"),
                "core_stack": CANDIDATE.get("core_stack", [])[:6],
            },
        }
        self._send_json(200, status_data)

    def handle_evaluate(self):
        """Evaluates JD text against candidate profile using local rules & Groq AI."""
        body = self._read_json_body()
        jd_text = body.get("jd_text", "").strip()
        company = body.get("company", "").strip()
        title = body.get("title", "").strip()

        if not jd_text:
            self._send_json(400, {"error": "Missing 'jd_text' in request body."})
            return

        logger.info(f"Evaluating JD for: {company or 'Unknown'} — {title or 'Unknown'} ({len(jd_text)} chars)")

        # 1. Step A: Fast Local Rule Check
        local_res = local_evaluate(jd_text)

        # 2. Step B: AI Deep Analysis (Groq)
        ai_res = None
        try:
            ai_res = ai_evaluate(jd_text)
        except Exception as e:
            logger.warning(f"AI evaluation failed or skipped: {e}")

        # Combine results
        score = 50
        verdict = local_res.get("local_verdict", "BORDERLINE")
        matched_skills = []
        missing_skills = []
        one_line_reason = ""
        resume_version = "java_backend"
        apply_action = "Review requirements and apply if aligned."

        if ai_res:
            score = ai_res.get("score", 50)
            verdict = ai_res.get("verdict", verdict)
            matched_skills = ai_res.get("matched_skills", [])
            missing_skills = ai_res.get("missing_skills", [])
            one_line_reason = ai_res.get("one_line_reason", "")
            resume_version = ai_res.get("resume_version", "java_backend")
            apply_action = ai_res.get("apply_action", apply_action)
            detected_company = ai_res.get("company", "")
            detected_role = ai_res.get("role", "")
            if not company and detected_company and detected_company.lower() != "unknown":
                company = detected_company
            if not title and detected_role and detected_role.lower() != "unknown":
                title = detected_role
        else:
            if verdict == "APPLY":
                score = 85
            elif verdict == "SKIP":
                score = 20
            else:
                score = 55
            one_line_reason = f"Local rule analysis evaluated verdict as {verdict}."

        response = {
            "verdict": verdict,
            "score": score,
            "company": company,
            "title": title,
            "hard_blocks": local_res.get("hard_blocks", []),
            "warnings": local_res.get("warnings", []),
            "green_flags": local_res.get("green_flags_found", []),
            "matched_skills": matched_skills,
            "missing_skills": missing_skills,
            "resume_version": resume_version,
            "one_line_reason": one_line_reason,
            "apply_action": apply_action,
        }
        self._send_json(200, response)

    def handle_tailor(self):
        """Tailors LaTeX resume and compiles locally to PDF via Tectonic in ~1.4s."""
        body = self._read_json_body()
        jd_text = body.get("jd_text", "").strip()
        company = body.get("company", "").strip() or "Company"
        role = body.get("role", "").strip() or "Software_Engineer"

        if not jd_text:
            self._send_json(400, {"error": "Missing 'jd_text' in request body."})
            return

        if not MASTER_RESUME_PATH.exists():
            self._send_json(500, {"error": f"Master resume not found at: {MASTER_RESUME_PATH}"})
            return

        with open(MASTER_RESUME_PATH, "r", encoding="utf-8") as f:
            master_tex = f.read()

        logger.info(f"Tailoring resume for {company} — {role}...")

        # 1. Generate tailored LaTeX via Groq AI
        try:
            tailored_tex = tailor_resume(
                master_tex=master_tex,
                job_description=jd_text,
                company=company,
                role=role,
            )
        except Exception as e:
            logger.error(f"Tailoring failed: {e}")
            self._send_json(500, {"error": f"AI Tailoring failed: {e}"})
            return

        # 2. Sanitize filename & save .tex
        clean_comp = re.sub(r"[^a-zA-Z0-9_\-]", "_", company).strip("_") or "Tailored"
        clean_role = re.sub(r"[^a-zA-Z0-9_\-]", "_", role).strip("_") or "SWE"
        base_name = f"Chinmay_Maheshwari_{clean_comp}_{clean_role}"
        tex_filename = f"{base_name}.tex"
        pdf_filename = f"{base_name}.pdf"

        tex_path = OUTPUT_DIR / tex_filename
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(tailored_tex)

        # 3. Compile PDF via Tectonic
        logger.info(f"Compiling PDF with Tectonic: {tex_path}")
        compile_ok = compile_pdf(str(tex_path))
        pdf_path = OUTPUT_DIR / pdf_filename

        if not compile_ok or not pdf_path.exists():
            logger.error("Tectonic compilation failed to create PDF.")
            self._send_json(500, {
                "error": "LaTeX generated but PDF compilation failed.",
                "tex_code": tailored_tex,
                "tex_path": str(tex_path),
            })
            return

        # 4. Generate diff summary
        diff_lines = list(difflib.unified_diff(
            master_tex.splitlines(),
            tailored_tex.splitlines(),
            fromfile="master_resume.tex",
            tofile=tex_filename,
            lineterm="",
        ))
        diff_summary = "\n".join(diff_lines[:100])

        download_url = f"http://127.0.0.1:{PORT}/download/{pdf_filename}"
        view_url = f"http://127.0.0.1:{PORT}/view/{pdf_filename}"
        logger.info(f"Successfully compiled: {pdf_filename} -> {download_url}")

        self._send_json(200, {
            "success": True,
            "filename": pdf_filename,
            "download_url": download_url,
            "view_url": view_url,
            "tex_path": str(tex_path),
            "pdf_path": str(pdf_path),
            "diff": diff_summary,
            "status": "ready",
        })

    def handle_refine(self):
        """Refines existing tailored resume based on user feedback and recompiles via Tectonic."""
        body = self._read_json_body()
        filename = body.get("filename", "").strip()
        feedback = body.get("feedback", "").strip()
        jd_text = body.get("jd_text", "").strip()
        company = body.get("company", "").strip() or "Company"
        role = body.get("role", "").strip() or "Software_Engineer"

        if not feedback:
            self._send_json(400, {"error": "Missing 'feedback' text in request."})
            return

        base_name = os.path.splitext(os.path.basename(filename))[0] if filename else f"Chinmay_Maheshwari_{company}_{role}"
        base_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", base_name).strip("_")
        tex_path = OUTPUT_DIR / f"{base_name}.tex"
        pdf_path = OUTPUT_DIR / f"{base_name}.pdf"

        if tex_path.exists():
            with open(tex_path, "r", encoding="utf-8") as f:
                current_tex = f.read()
        elif MASTER_RESUME_PATH.exists():
            with open(MASTER_RESUME_PATH, "r", encoding="utf-8") as f:
                current_tex = f.read()
        else:
            self._send_json(404, {"error": "Resume file not found to refine."})
            return

        logger.info(f"Refining resume for {company} — {role} with feedback: '{feedback[:60]}...'")

        try:
            refined_tex = refine_resume(
                current_tex=current_tex,
                feedback=feedback,
                job_description=jd_text,
                company=company,
                role=role,
            )
        except Exception as e:
            logger.error(f"Refinement failed: {e}")
            self._send_json(500, {"error": f"Refinement failed: {e}"})
            return

        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(refined_tex)

        compile_ok = compile_pdf(str(tex_path))
        if not compile_ok or not pdf_path.exists():
            self._send_json(500, {
                "error": "LaTeX updated but PDF compilation failed.",
                "tex_code": refined_tex,
            })
            return

        diff_lines = list(difflib.unified_diff(
            current_tex.splitlines(),
            refined_tex.splitlines(),
            fromfile="previous.tex",
            tofile=f"{base_name}.tex",
            lineterm="",
        ))
        diff_summary = "\n".join(diff_lines[:100])

        pdf_filename = f"{base_name}.pdf"
        download_url = f"http://127.0.0.1:{PORT}/download/{pdf_filename}"
        view_url = f"http://127.0.0.1:{PORT}/view/{pdf_filename}"

        self._send_json(200, {
            "success": True,
            "filename": pdf_filename,
            "download_url": download_url,
            "view_url": view_url,
            "diff": diff_summary,
            "message": "Resume refined and recompiled successfully!",
        })

    def handle_download(self, filename: str, as_attachment: bool = True, head_only: bool = False):
        """Serves compiled PDF directly to browser with full Range header support for Chrome PDF Viewer."""
        # Prevent directory traversal
        safe_name = os.path.basename(filename)
        file_path = OUTPUT_DIR / safe_name
        if not file_path.exists() or not file_path.is_file():
            fallback_path = BUILDER_DIR / safe_name
            if fallback_path.exists() and fallback_path.is_file():
                file_path = fallback_path

        if not file_path.exists() or not file_path.is_file():
            if head_only:
                self.send_response(404)
                self._set_cors_headers("application/json")
                self.end_headers()
            else:
                self._send_json(404, {"error": "File not found", "filename": safe_name})
            return

        with open(file_path, "rb") as f:
            pdf_bytes = f.read()

        total_size = len(pdf_bytes)
        disp_type = "attachment" if as_attachment else "inline"

        # Check for HTTP Range header (crucial for Chrome's native PDF Viewer)
        range_header = self.headers.get("Range") if hasattr(self, "headers") else None
        if range_header and range_header.startswith("bytes="):
            try:
                ranges = range_header[6:].split("-")
                start = int(ranges[0]) if ranges[0] else 0
                end = int(ranges[1]) if len(ranges) > 1 and ranges[1] else total_size - 1
                if start >= total_size:
                    start = total_size - 1
                if end >= total_size:
                    end = total_size - 1
                if start > end:
                    start = 0
                chunk = pdf_bytes[start : end + 1]

                self.send_response(206)
                self._set_cors_headers("application/pdf")
                self.send_header("Content-Disposition", f'{disp_type}; filename="{safe_name}"')
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes {start}-{end}/{total_size}")
                self.send_header("Content-Length", str(len(chunk)))
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                if not head_only:
                    try:
                        self.wfile.write(chunk)
                    except (ConnectionResetError, BrokenPipeError):
                        pass
                return
            except Exception as e:
                logger.warning(f"Error handling Range request: {e}")

        # Standard 200 OK
        self.send_response(200)
        self._set_cors_headers("application/pdf")
        self.send_header("Content-Disposition", f'{disp_type}; filename="{safe_name}"')
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(total_size))
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        if not head_only:
            try:
                self.wfile.write(pdf_bytes)
            except (ConnectionResetError, BrokenPipeError):
                pass

    def handle_record_job(self):
        """Records the job into gcc_jobs.db."""
        body = self._read_json_body()
        company = body.get("company", "").strip()
        title = body.get("title", "").strip()
        apply_url = body.get("url", "").strip()
        status = body.get("status", "EVALUATED").strip()
        notes = body.get("notes", "")

        if not company or not title:
            self._send_json(400, {"error": "Company and Title are required."})
            return

        db_path = get_db_path()
        init_db(db_path)

        # Import SQLite connection directly to insert or update seen_jobs
        import sqlite3
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.cursor()
            job_id = f"chrome_{re.sub(r'[^a-zA-Z0-9]', '_', company.lower())}_{re.sub(r'[^a-zA-Z0-9]', '_', title.lower())[:30]}"
            cur.execute("""
                INSERT INTO seen_jobs (
                    id, company, title, location, apply_url, provider, status, notes, relevance_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    notes = excluded.notes,
                    last_seen_at = CURRENT_TIMESTAMP
            """, (
                job_id, company, title, "India/Remote", apply_url, "chrome_ext",
                status, notes, body.get("score", 70)
            ))
            conn.commit()
            logger.info(f"Recorded job '{title}' at '{company}' with status '{status}'")
            self._send_json(200, {"success": True, "job_id": job_id, "status": status})
        except Exception as e:
            logger.error(f"Failed to record job: {e}")
            self._send_json(500, {"error": str(e)})
        finally:
            conn.close()

    def handle_dismiss(self):
        """Dismisses a job / company and registers suppression in Radar database and dismissals registry."""
        body = self._read_json_body()
        company = body.get("company", "").strip()
        title = body.get("title", "").strip() or "All Roles"
        apply_url = body.get("url", "").strip()
        reason = body.get("reason", "").strip() or "Dismissed via Chrome Extension"
        score = body.get("score", 0)

        if not company:
            self._send_json(400, {"error": "Company name is required to dismiss."})
            return

        db_path = get_db_path()
        init_db(db_path)

        try:
            # 1. Register suppression across DB & dismissals registry (dismissals.json & dismissed_companies)
            dismiss_res = dismiss_selectors_or_companies(selector=company, notes=reason, db_path=db_path)

            # 2. Also ensure this specific job ID / entry is explicitly marked DISMISSED in seen_jobs
            job_id = f"chrome_{re.sub(r'[^a-zA-Z0-9]', '_', company.lower())}_{re.sub(r'[^a-zA-Z0-9]', '_', title.lower())[:30]}"
            import sqlite3
            conn = sqlite3.connect(db_path)
            try:
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO seen_jobs (
                        id, company, title, location, apply_url, provider, status, notes, relevance_score, is_active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                    ON CONFLICT(id) DO UPDATE SET
                        status = 'DISMISSED',
                        is_active = 0,
                        notes = excluded.notes,
                        last_seen_at = CURRENT_TIMESTAMP
                """, (
                    job_id, company, title, "India/Remote", apply_url, "chrome_ext",
                    "DISMISSED", f"Dismissed via Chrome Extension: {reason}", score
                ))
                conn.commit()
            finally:
                conn.close()

            # 3. Explicitly persist to dismissals registry for url/job as well
            save_dismissal_to_registry(
                job_dict={"company": company, "title": title, "apply_url": apply_url, "id": job_id},
                company_name=company,
                db_path=db_path,
            )

            logger.info(f"Dismissed job/company '{company}' - '{title}': {dismiss_res}")
            self._send_json(200, {
                "success": True,
                "company": company,
                "title": title,
                "status": "DISMISSED",
                "details": dismiss_res,
            })
        except Exception as e:
            logger.error(f"Failed to dismiss job: {e}", exc_info=True)
            self._send_json(500, {"error": str(e)})

    def handle_lookup_company(self, company: str, title: str = "", url: str = ""):
        """Resolves company against Radar registry, canonical ATS portal, and DB history."""
        try:
            from tools.company_resolver import resolve_company
            db_path = get_db_path()
            res = resolve_company(company, title=title, current_url=url, db_path=db_path)
            self._send_json(200, res)
        except Exception as e:
            logger.error(f"Company resolution error for '{company}': {e}", exc_info=True)
            self._send_json(500, {"error": str(e), "found": False})

    def handle_generate_outreach(self):
        """Generates cold outreach messages (LinkedIn note, cold email, referral request)."""
        body = self._read_json_body()
        company = body.get("company", "").strip() or "Company"
        role = body.get("title", "") or body.get("role", "") or "Software Engineer"
        jd_text = body.get("jd_text", "").strip()
        matched_skills = body.get("matched_skills", [])
        recipient_type = body.get("recipient_type", "recruiter").strip()

        try:
            from tools.outreach_generator import generate_all_outreach
            res = generate_all_outreach(
                company=company,
                role=role,
                jd_text=jd_text,
                detected_skills=matched_skills,
                recipient_type=recipient_type,
            )
            self._send_json(200, res)
        except Exception as e:
            logger.error(f"Outreach generation error: {e}", exc_info=True)
            self._send_json(500, {"error": str(e), "success": False})

    def handle_get_candidate_profile(self):
        """Returns candidate profile data for 1-click ATS form filling."""
        try:
            from tools.autofill_engine import get_candidate_profile
            profile = get_candidate_profile()
            self._send_json(200, {"success": True, "profile": profile})
        except Exception as e:
            logger.error(f"Error fetching candidate profile: {e}", exc_info=True)
            self._send_json(500, {"error": str(e), "success": False})

    def handle_save_candidate_profile(self):
        """Updates and persists custom candidate profile overrides."""
        try:
            from tools.autofill_engine import save_candidate_profile
            body = self._read_json_body()
            profile = save_candidate_profile(body)
            self._send_json(200, {"success": True, "profile": profile})
        except Exception as e:
            logger.error(f"Error saving candidate profile: {e}", exc_info=True)
            self._send_json(500, {"error": str(e), "success": False})

    def handle_answer_screening_question(self):
        """Generates crisp answer to ATS application screening questions."""
        body = self._read_json_body()
        question = body.get("question", "").strip()
        company = body.get("company", "").strip() or "Company"
        role = body.get("role", "") or body.get("title", "") or "Software Engineer"
        jd_text = body.get("jd_text", "").strip()

        try:
            from tools.autofill_engine import answer_screening_question
            res = answer_screening_question(
                question=question,
                company=company,
                role=role,
                jd_text=jd_text,
            )
            self._send_json(200, res)
        except Exception as e:
            logger.error(f"Error answering screening question: {e}", exc_info=True)
            self._send_json(500, {"error": str(e), "success": False})



class ThreadingBridgeServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def run_bridge(port: int = PORT):
    print("=" * 70)
    print(f"🚀 GCC Job Radar Chrome Extension Bridge running on http://127.0.0.1:{port}")
    print("   - Health check:     http://127.0.0.1:8765/health")
    print("   - PDF output dir:   builder/output/")
    print("   - Tectonic engine:  tools/bin/tectonic.exe")
    print("=" * 70)

    with ThreadingBridgeServer(("127.0.0.1", port), BridgeHTTPRequestHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down bridge server...")
            httpd.shutdown()


if __name__ == "__main__":
    port_arg = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else PORT
    run_bridge(port_arg)
