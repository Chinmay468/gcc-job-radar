from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Optional, Union
import urllib.parse

from gcc_job_radar.models import ATSProvider, JobPosting

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(os.getenv("GCC_RADAR_DB_PATH", "gcc_jobs.db"))
DISMISSALS_REGISTRY_PATH = Path(__file__).parent / "dismissals.json"


def get_db_path(custom_path: Optional[Union[Path, str]] = None) -> Path:
    """Resolve active SQLite database path and ensure parent directories exist."""
    if custom_path is not None:
        target = Path(custom_path)
    else:
        target = Path(os.getenv("GCC_RADAR_DB_PATH", DEFAULT_DB_PATH))
    if target.parent and not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
    return target


def canonicalize_url(url: str) -> str:
    """Normalize and strip tracking query parameters (gh_jid, utm_*, etc.) while preserving job IDs for platforms like Glassdoor and Indeed."""
    if not url:
        return ""
    try:
        url_str = str(url).strip()
        parsed = urllib.parse.urlparse(url_str)
        netloc = parsed.netloc.lower()

        # Preserve Glassdoor job ID in query param (?jl=...)
        if "glassdoor." in netloc:
            m_jl = re.search(r"(?:jl|jobListingId)=([0-9]+)", url_str)
            if m_jl:
                return f"https://www.glassdoor.com/job-listing/?jl={m_jl.group(1)}"

        # Preserve Indeed job ID in query param (?jk=...)
        if "indeed." in netloc:
            query_dict = urllib.parse.parse_qs(parsed.query, keep_blank_values=False)
            jk_val = query_dict.get("jk", [None])[0]
            if jk_val:
                return f"https://www.indeed.com/viewjob?jk={jk_val}"

        # Canonicalize LinkedIn job URLs: https://www.linkedin.com/jobs/view/<id>/
        m_li = re.search(r"linkedin\.com/(?:comm/)?jobs/view/([0-9]+)", url_str)
        if m_li:
            return f"https://www.linkedin.com/jobs/view/{m_li.group(1)}/"

        path = parsed.path.rstrip("/")
        # Standard ATS paths (Greenhouse, Lever, Ashby, Workday) identify the job listing in the path.
        clean_url = urllib.parse.urlunparse(
            (parsed.scheme.lower(), netloc, path, "", "", "")
        )
        return clean_url
    except Exception:
        return str(url).strip().rstrip("/")


def make_job_key(job: JobPosting) -> str:
    """Build unique canonical key for a posting across all ATS providers."""
    return f"{job.provider.value}_{job.company.lower()}_{job.id}".strip()


def cleanup_duplicate_jobs(db_path: Optional[Path] = None) -> int:
    """Delete duplicate job postings from seen_jobs, retaining the newest last_seen_at record."""
    target_path = get_db_path(db_path)
    if not target_path.exists():
        return 0

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='seen_jobs'")
        if not cursor.fetchone():
            return 0

        # Step 1: Normalize all existing apply_url values
        cursor.execute("SELECT id, apply_url FROM seen_jobs")
        rows = cursor.fetchall()
        updates = []
        for row_id, raw_url in rows:
            clean = canonicalize_url(raw_url)
            if clean != raw_url:
                updates.append((clean, row_id))
        if updates:
            cursor.executemany("UPDATE seen_jobs SET apply_url = ? WHERE id = ?", updates)

        # Step 2: Delete duplicate records by canonical apply_url (keeping newest last_seen_at)
        cursor.execute(
            """
            DELETE FROM seen_jobs
            WHERE id NOT IN (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY lower(company), lower(apply_url)
                               ORDER BY last_seen_at DESC, first_seen_at DESC, id ASC
                           ) as rn
                    FROM seen_jobs
                ) WHERE rn = 1
            );
            """
        )
        deleted_by_url = cursor.rowcount

        # Step 3: Delete duplicate records by semantic role (company, lower(title), lower(location))
        cursor.execute(
            """
            DELETE FROM seen_jobs
            WHERE id NOT IN (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY lower(company), lower(title), lower(location)
                               ORDER BY last_seen_at DESC, first_seen_at DESC, id ASC
                           ) as rn
                    FROM seen_jobs
                ) WHERE rn = 1
            );
            """
        )
        deleted_by_semantic = cursor.rowcount

        # Step 4: Delete cross-platform duplicate records for same (company, title) in India/Remote,
        # ensuring that APPLIED/INTERVIEWING/REJECTED/DISMISSED statuses and canonical ATS links are preserved.
        deleted_by_role = 0
        cursor.execute("PRAGMA table_info(seen_jobs)")
        cols = {row[1] for row in cursor.fetchall()}
        if "status" in cols:
            cursor.execute(
                """
                DELETE FROM seen_jobs
                WHERE id NOT IN (
                    SELECT id FROM (
                        SELECT id,
                               ROW_NUMBER() OVER (
                                   PARTITION BY lower(company), lower(title)
                                   ORDER BY 
                                       CASE 
                                           WHEN status = 'APPLIED' THEN 1
                                           WHEN status = 'INTERVIEWING' THEN 2
                                           WHEN status = 'REJECTED' THEN 3
                                           WHEN status = 'DISMISSED' THEN 4
                                           ELSE 5 
                                       END ASC,
                                       CASE WHEN provider != 'email_alert' THEN 1 ELSE 2 END ASC,
                                       CASE WHEN lower(location) IN ('india', 'remote') THEN 2 ELSE 1 END ASC,
                                       last_seen_at DESC,
                                       first_seen_at DESC,
                                       id ASC
                               ) as rn
                        FROM seen_jobs
                        WHERE (lower(location) LIKE '%india%' OR lower(location) LIKE '%remote%' OR (is_remote IS NOT NULL AND is_remote = 1))
                    ) WHERE rn = 1
                )
                AND (lower(location) LIKE '%india%' OR lower(location) LIKE '%remote%' OR (is_remote IS NOT NULL AND is_remote = 1));
                """
            )
            deleted_by_role = cursor.rowcount
        conn.commit()

        return deleted_by_url + deleted_by_semantic + deleted_by_role


def purge_invalid_jobs(db_path: Optional[Path] = None) -> int:
    """Purge job records from database that fail strict entry-level tech title filters."""
    target_path = get_db_path(db_path)
    if not target_path.exists():
        return 0

    from gcc_job_radar.filters import matches_target_title

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='seen_jobs'")
        if not cursor.fetchone():
            return 0

        cursor.execute("SELECT id, title FROM seen_jobs WHERE status = 'NEW'")
        rows = cursor.fetchall()
        invalid_ids = [row_id for row_id, title in rows if not matches_target_title(title)]

        if invalid_ids:
            for i in range(0, len(invalid_ids), 500):
                chunk = invalid_ids[i : i + 500]
                placeholders = ",".join("?" for _ in chunk)
                cursor.execute(f"DELETE FROM seen_jobs WHERE id IN ({placeholders})", chunk)
            conn.commit()

        return len(invalid_ids)


def init_db(db_path: Optional[Path] = None) -> None:
    """Initialize SQLite database tables, indexes, and run deduplication cleanup."""
    target_path = get_db_path(db_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS seen_jobs (
                id TEXT PRIMARY KEY,
                company TEXT NOT NULL,
                title TEXT NOT NULL,
                location TEXT NOT NULL,
                apply_url TEXT NOT NULL,
                provider TEXT NOT NULL,
                published_date TEXT,
                is_active INTEGER DEFAULT 1,
                is_remote INTEGER DEFAULT 0,
                status TEXT DEFAULT 'NEW',
                applied_at TIMESTAMP NULL,
                notes TEXT NULL,
                direct_search_url TEXT NULL,
                relevance_score INTEGER DEFAULT 0,
                first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        # Migrate columns if missing from earlier migrations
        for tbl in ("seen_jobs", "jobs"):
            cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{tbl}'")
            if cursor.fetchone():
                cursor.execute(f"PRAGMA table_info({tbl})")
                columns = [row[1] for row in cursor.fetchall()]
                if "is_active" not in columns:
                    cursor.execute(f"ALTER TABLE {tbl} ADD COLUMN is_active INTEGER DEFAULT 1")
                if "is_remote" not in columns:
                    cursor.execute(f"ALTER TABLE {tbl} ADD COLUMN is_remote INTEGER DEFAULT 0")
                if "status" not in columns:
                    cursor.execute(f"ALTER TABLE {tbl} ADD COLUMN status TEXT DEFAULT 'NEW'")
                if "applied_at" not in columns:
                    cursor.execute(f"ALTER TABLE {tbl} ADD COLUMN applied_at TIMESTAMP NULL")
                if "notes" not in columns:
                    cursor.execute(f"ALTER TABLE {tbl} ADD COLUMN notes TEXT NULL")
                if "direct_search_url" not in columns:
                    cursor.execute(f"ALTER TABLE {tbl} ADD COLUMN direct_search_url TEXT NULL")
                if "relevance_score" not in columns:
                    cursor.execute(f"ALTER TABLE {tbl} ADD COLUMN relevance_score INTEGER DEFAULT 0")

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_seen_jobs_status ON seen_jobs(status);")
        # Recreate jobs view to expose direct_search_url and rowid as numeric_id
        cursor.execute("DROP VIEW IF EXISTS jobs;")
        cursor.execute("CREATE VIEW jobs AS SELECT rowid AS numeric_id, * FROM seen_jobs;")

        # Backfill is_remote for any pre-existing records matching remote patterns
        cursor.execute(
            """
            UPDATE seen_jobs 
            SET is_remote = 1 
            WHERE is_remote = 0 
              AND (
                  lower(location) LIKE '%remote%' 
                  OR lower(location) LIKE '%wfh%' 
                  OR lower(location) LIKE '%work from home%' 
                  OR lower(location) LIKE '%distributed%'
                  OR lower(location) LIKE '%anywhere in india%'
              )
              AND NOT (
                  lower(location) LIKE '%us remote%'
                  OR lower(location) LIKE '%remote - us%'
                  OR lower(location) LIKE '%remote (us)%'
                  OR lower(location) LIKE '%remote, us%'
                  OR lower(location) LIKE '%remote - usa%'
                  OR lower(location) LIKE '%remote - north america%'
                  OR lower(location) LIKE '%emea remote%'
                  OR lower(location) LIKE '%remote - emea%'
                  OR lower(location) LIKE '%remote - europe%'
                  OR lower(location) LIKE '%uk remote%'
                  OR lower(location) LIKE '%remote - uk%'
                  OR lower(location) LIKE '%canada remote%'
                  OR lower(location) LIKE '%remote - canada%'
                  OR lower(location) LIKE '%germany remote%'
                  OR lower(location) LIKE '%australia remote%'
                  OR lower(location) LIKE '%latam remote%'
                  OR lower(location) LIKE '%remote - latam%'
              )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS dispatched_alerts (
                job_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (job_id, platform)
            );
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_dispatched_alerts_platform ON dispatched_alerts(platform);
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS seen_emails (
                uid TEXT PRIMARY KEY,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_seen_emails_uid ON seen_emails(uid);
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS dormant_companies (
                company_name TEXT PRIMARY KEY,
                reason TEXT,
                paused_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                consecutive_zero_scans INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 0,
                notes TEXT
            );
            """
        )
        from gcc_job_radar.dormant_companies import DORMANT_REGISTRY
        for entry in DORMANT_REGISTRY:
            cursor.execute(
                """
                INSERT OR IGNORE INTO dormant_companies (company_name, reason, paused_at, consecutive_zero_scans, is_active, notes)
                VALUES (?, ?, ?, 0, 0, ?)
                """,
                (entry.config.name, entry.reason, entry.paused_at, entry.notes),
            )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS apply_prep_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                company TEXT NOT NULL,
                title TEXT NOT NULL,
                provider TEXT NOT NULL,
                status TEXT NOT NULL,
                details TEXT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_apply_prep_job_id ON apply_prep_attempts(job_id);
            """
        )
        conn.commit()

    # Clean up any existing duplicate entries
    cleanup_duplicate_jobs(target_path)

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_seen_jobs_company ON seen_jobs(company);
            """
        )
        cursor.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_seen_jobs_company_title_loc 
            ON seen_jobs(lower(company), lower(title), lower(location));
            """
        )
        conn.commit()

    # Sync pre-configured / git-tracked dismissals across all environments on the primary database
    if is_primary_db(db_path):
        sync_dismissals_registry(target_path)


def is_primary_db(db_path: Optional[Union[Path, str]] = None) -> bool:
    """Check if the provided database path corresponds to the primary production database."""
    if db_path is None:
        return True
    if os.getenv("GCC_RADAR_SYNC_DISMISSALS") == "1":
        return True
    try:
        return Path(db_path).resolve() == get_db_path(DEFAULT_DB_PATH).resolve()
    except Exception:
        return False


def sync_dismissals_registry(db_path: Optional[Path] = None) -> int:
    """Sync tracked dismissals from dismissals.json into seen_jobs and dispatched_alerts."""
    if not DISMISSALS_REGISTRY_PATH.exists():
        return 0

    target_path = get_db_path(db_path)
    if not target_path.exists():
        return 0

    try:
        data = json.loads(DISMISSALS_REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("Failed to read dismissals.json: %s", exc)
        return 0

    dismissed_jobs = data.get("dismissed_jobs", [])
    dismissed_companies = data.get("dismissed_companies", [])
    synced_count = 0

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()

        for j in dismissed_jobs:
            comp = (j.get("company") or "").strip()
            title = (j.get("title") or "").strip()
            apply_url = canonicalize_url(j.get("apply_url") or "")
            jid = j.get("id") or f"dismissed_{abs(hash((comp, title, apply_url)))}"

            # 1. Update any existing rows matching this URL, id, or (company, title)
            cursor.execute(
                """
                UPDATE seen_jobs
                SET status = 'DISMISSED',
                    notes = COALESCE(notes, 'Tracked dismissal')
                WHERE (apply_url != '' AND lower(apply_url) = lower(?))
                   OR (lower(company) = lower(?) AND lower(title) = lower(?))
                   OR id = ?
                """,
                (apply_url, comp, title, jid),
            )
            if cursor.rowcount == 0:
                # Insert tombstone row
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO seen_jobs (
                        id, company, title, location, apply_url, provider,
                        published_date, is_active, is_remote, status, notes
                    )
                    VALUES (?, ?, ?, 'India', ?, 'custom', 'Dismissed', 0, 0, 'DISMISSED', 'Tracked dismissal')
                    """,
                    (jid, comp or "Unknown", title or "Role", apply_url or "https://dismissed.local"),
                )

            # 2. Add to dispatched_alerts for both telegram and discord so it is never alerted
            for plat in ("telegram", "discord"):
                cursor.execute(
                    "INSERT OR IGNORE INTO dispatched_alerts (job_id, platform, sent_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                    (jid, plat),
                )
                if comp:
                    cursor.execute(
                        "INSERT OR IGNORE INTO dispatched_alerts (job_id, platform, sent_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                        (f"custom_{comp.lower()}_{jid}", plat),
                    )

            synced_count += 1

        for comp in dismissed_companies:
            c_clean = comp.strip()
            if not c_clean:
                continue
            cursor.execute(
                """
                UPDATE seen_jobs
                SET status = 'DISMISSED'
                WHERE lower(company) = ?
                """,
                (c_clean.lower(),),
            )
            cursor.execute(
                "SELECT 1 FROM seen_jobs WHERE lower(company) = ? AND status = 'DISMISSED' LIMIT 1",
                (c_clean.lower(),),
            )
            if not cursor.fetchone():
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO seen_jobs (
                        id, company, title, location, apply_url, provider,
                        published_date, is_active, is_remote, status, notes
                    )
                    VALUES (?, ?, 'All Roles', 'India', 'https://dismissed.local', 'custom', 'Dismissed', 0, 0, 'DISMISSED', 'Company dismissed by user')
                    """,
                    (f"adhoc_dismiss_{c_clean.lower()}", c_clean),
                )

        conn.commit()

    return synced_count


def save_dismissal_to_registry(
    job_dict: Optional[dict[str, Any]] = None,
    company_name: Optional[str] = None,
    db_path: Optional[Union[Path, str]] = None,
) -> None:
    """Persist newly dismissed jobs or companies to dismissals.json if file is writable on the primary database."""
    if not is_primary_db(db_path):
        return

    try:
        if not DISMISSALS_REGISTRY_PATH.exists():
            data = {"dismissed_jobs": [], "dismissed_companies": []}
        else:
            data = json.loads(DISMISSALS_REGISTRY_PATH.read_text(encoding="utf-8"))

        changed = False
        if job_dict:
            clean_url = canonicalize_url(str(job_dict.get("apply_url") or ""))
            comp = str(job_dict.get("company") or "").strip()
            title = str(job_dict.get("title") or "").strip()
            jid = job_dict.get("id") or job_dict.get("numeric_id")

            existing = [
                d for d in data.get("dismissed_jobs", [])
                if (clean_url and canonicalize_url(str(d.get("apply_url") or "")).lower() == clean_url.lower())
                or (comp and title and str(d.get("company") or "").lower() == comp.lower() and str(d.get("title") or "").lower() == title.lower())
                or (jid and str(d.get("id")) == str(jid))
            ]
            if not existing:
                data.setdefault("dismissed_jobs", []).append({
                    "id": str(jid) if jid else None,
                    "company": comp,
                    "title": title,
                    "apply_url": clean_url,
                })
                changed = True

        if company_name:
            c_name = company_name.strip()
            if c_name and c_name.lower() not in [str(c).lower() for c in data.get("dismissed_companies", [])]:
                data.setdefault("dismissed_companies", []).append(c_name)
                changed = True

        if changed:
            DISMISSALS_REGISTRY_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug("Could not write to dismissals.json: %s", exc)


def filter_unalerted_jobs(
    jobs: list[JobPosting], platform: str, db_path: Optional[Path] = None
) -> list[JobPosting]:
    """Filter out jobs that have already been alerted on a specific platform,
    or are marked as DISMISSED or APPLIED in seen_jobs.
    """
    init_db(db_path)
    target_path = get_db_path(db_path)

    if not jobs:
        return []

    keys = [make_job_key(j) for j in jobs]
    raw_ids = [str(j.id) for j in jobs]
    id_placeholders = ",".join("?" for _ in (keys + raw_ids))

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT job_id FROM dispatched_alerts WHERE platform = ? AND job_id IN ({id_placeholders})",
            [platform] + keys + raw_ids,
        )
        sent_ids = {row[0] for row in cursor.fetchall()}

        cursor.execute(
            """
            SELECT id, lower(apply_url), lower(company), lower(title), status
            FROM seen_jobs
            WHERE status IN ('DISMISSED', 'APPLIED')
            """
        )
        suppressed_rows = cursor.fetchall()
        suppressed_ids = {r[0] for r in suppressed_rows}
        suppressed_urls = {r[1] for r in suppressed_rows if r[1]}
        suppressed_semantic = {(r[2], r[3]) for r in suppressed_rows}

    unalerted: list[JobPosting] = []
    for j in jobs:
        k = make_job_key(j)
        raw_id = str(j.id)
        clean_u = canonicalize_url(str(j.apply_url)).lower()
        sem_k = (j.company.lower().strip(), j.title.lower().strip())
        status = getattr(j, "status", "NEW").upper()

        if status in ("DISMISSED", "APPLIED"):
            continue
        if k in sent_ids or raw_id in sent_ids:
            continue
        if k in suppressed_ids or raw_id in suppressed_ids:
            continue
        if clean_u in suppressed_urls:
            continue
        if sem_k in suppressed_semantic:
            continue

        unalerted.append(j)

    return unalerted


def record_dispatched_alert(
    job_id: str, platform: str, db_path: Optional[Path] = None
) -> None:
    """Record that an alert has been dispatched for a job on a platform."""
    init_db(db_path)
    target_path = get_db_path(db_path)

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR IGNORE INTO dispatched_alerts (job_id, platform, sent_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            """,
            (job_id, platform),
        )
        conn.commit()


def record_dispatched_alerts(
    jobs: list[JobPosting], platform: str, db_path: Optional[Path] = None
) -> None:
    """Record multiple dispatched alerts for a platform atomically."""
    if not jobs:
        return

    init_db(db_path)
    target_path = get_db_path(db_path)
    records = [(make_job_key(j), platform) for j in jobs]

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        cursor.executemany(
            """
            INSERT OR IGNORE INTO dispatched_alerts (job_id, platform, sent_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            """,
            records,
        )
        conn.commit()


def filter_new_jobs(
    jobs: list[JobPosting], db_path: Optional[Path] = None
) -> tuple[list[JobPosting], list[JobPosting]]:
    """Partition jobs into newly discovered postings and previously seen postings."""
    init_db(db_path)
    target_path = get_db_path(db_path)

    if not jobs:
        return [], []

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, lower(apply_url), lower(company), lower(title), lower(location), status FROM seen_jobs"
        )
        records = cursor.fetchall()
        seen_ids = {r[0] for r in records}
        seen_urls = {r[1] for r in records if r[1]}
        seen_semantic = {(r[2], r[3], r[4]) for r in records}
        # Dismissed jobs must never re-surface as new — treat them as permanently seen
        dismissed_ids = {r[0] for r in records if r[5] and r[5].upper() == "DISMISSED"}
        dismissed_urls = {r[1] for r in records if r[1] and r[5] and r[5].upper() == "DISMISSED"}
        dismissed_semantic = {(r[2], r[3], r[4]) for r in records if r[5] and r[5].upper() == "DISMISSED"}

    new_jobs: list[JobPosting] = []
    existing_jobs: list[JobPosting] = []

    for job in jobs:
        clean_url = canonicalize_url(str(job.apply_url)).lower()
        key = make_job_key(job)
        sem_key = (
            job.company.lower().strip(),
            job.title.lower().strip(),
            job.location.lower().strip(),
        )
        comp_lower = job.company.lower().strip()
        title_lower = job.title.lower().strip()
        loc_lower = job.location.lower().strip()

        # Fast-path: if this posting matches any dismissed record, treat as existing (never resurface)
        if key in dismissed_ids or clean_url in dismissed_urls or sem_key in dismissed_semantic:
            existing_jobs.append(job)
            continue

        cursor.execute(
            """
            SELECT id FROM seen_jobs
            WHERE id = ?
               OR lower(apply_url) = ?
               OR (lower(company) = ? AND lower(title) = ? AND lower(location) = ?)
               OR (lower(company) = ? AND lower(title) = ? AND (
                   lower(location) = 'india' OR ? = 'india'
                   OR lower(location) LIKE '%' || ? || '%'
                   OR ? LIKE '%' || lower(location) || '%'
               ))
            LIMIT 1
            """,
            (
                key, clean_url.lower(),
                comp_lower, title_lower, loc_lower,
                comp_lower, title_lower, loc_lower, loc_lower, loc_lower,
            ),
        )

        if cursor.fetchone() or key in seen_ids or clean_url in seen_urls or sem_key in seen_semantic:
            existing_jobs.append(job)
        else:
            new_jobs.append(job)
            seen_ids.add(key)
            seen_urls.add(clean_url)
            seen_semantic.add(sem_key)

    return new_jobs, existing_jobs


def record_jobs(jobs: list[JobPosting], db_path: Optional[Path] = None) -> None:
    """Record newly seen jobs and update last_seen_at timestamps for active ones."""
    if not jobs:
        return

    init_db(db_path)
    target_path = get_db_path(db_path)

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()

        # Deduplicate within incoming batch
        seen_sem: set[tuple[str, str, str]] = set()
        seen_urls: set[str] = set()
        deduped_batch: list[tuple[JobPosting, str]] = []

        for j in jobs:
            clean_url = canonicalize_url(str(j.apply_url))
            sem_key = (
                j.company.lower().strip(),
                j.title.lower().strip(),
                j.location.lower().strip(),
            )
            url_key = clean_url.lower().strip()
            if sem_key in seen_sem or url_key in seen_urls:
                continue
            seen_sem.add(sem_key)
            seen_urls.add(url_key)
            deduped_batch.append((j, clean_url))

        from gcc_job_radar.relevance import score_job_posting

        for job, clean_url in deduped_batch:
            job_key = make_job_key(job)
            comp_lower = job.company.lower().strip()
            title_lower = job.title.lower().strip()
            loc_lower = job.location.lower().strip()
            job_score = getattr(job, "relevance_score", None)
            if job_score is None or job_score == 0:
                job_score = score_job_posting(job)

            cursor.execute(
                """
                SELECT id, status, provider, apply_url, location FROM seen_jobs
                WHERE id = ?
                   OR lower(apply_url) = ?
                   OR (lower(company) = ? AND lower(title) = ? AND lower(location) = ?)
                   OR (lower(company) = ? AND lower(title) = ? AND (
                       lower(location) = 'india' OR ? = 'india'
                       OR lower(location) LIKE '%' || ? || '%'
                       OR ? LIKE '%' || lower(location) || '%'
                   ))
                ORDER BY
                    CASE
                        WHEN status = 'APPLIED' THEN 1
                        WHEN status = 'INTERVIEWING' THEN 2
                        WHEN status = 'REJECTED' THEN 3
                        WHEN status = 'DISMISSED' THEN 4
                        ELSE 5
                    END ASC,
                    CASE WHEN provider != 'email_alert' THEN 1 ELSE 2 END ASC
                LIMIT 1
                """,
                (
                    job_key, clean_url.lower(),
                    comp_lower, title_lower, loc_lower,
                    comp_lower, title_lower, loc_lower, loc_lower, loc_lower,
                ),
            )
            matched = cursor.fetchone()

            if matched:
                matched_id, existing_status, existing_provider, existing_url, existing_loc = matched
                # DISMISSED is a permanent tombstone — never re-open it to NEW.
                # Any other non-NEW status (APPLIED, INTERVIEWING, REJECTED) is also preserved.
                if existing_status == "DISMISSED":
                    continue  # Skip entirely — do not touch this record again
                target_status = existing_status if existing_status != "NEW" else job.status
                target_url = (
                    existing_url
                    if existing_provider != "email_alert" and job.provider == ATSProvider.EMAIL_ALERT
                    else clean_url
                )
                target_provider = (
                    existing_provider
                    if existing_provider != "email_alert" and job.provider == ATSProvider.EMAIL_ALERT
                    else job.provider.value
                )
                target_loc = (
                    existing_loc
                    if existing_loc.lower() != "india" and loc_lower == "india"
                    else job.location
                )

                cursor.execute(
                    """
                    UPDATE seen_jobs SET
                        last_seen_at = CURRENT_TIMESTAMP,
                        company = ?,
                        title = ?,
                        location = ?,
                        apply_url = ?,
                        provider = ?,
                        published_date = COALESCE(NULLIF(?, ''), published_date),
                        is_active = 1,
                        is_remote = ?,
                        status = ?,
                        direct_search_url = COALESCE(?, direct_search_url),
                        relevance_score = ?
                    WHERE id = ?
                    """,
                    (
                        job.company,
                        job.title,
                        target_loc,
                        target_url,
                        target_provider,
                        job.published_date or "Active",
                        1 if job.is_remote else 0,
                        target_status,
                        getattr(job, "direct_search_url", None),
                        job_score,
                        matched_id,
                    ),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO seen_jobs (
                        id, company, title, location, apply_url, provider, published_date, is_active, is_remote, status, applied_at, notes, direct_search_url, relevance_score, first_seen_at, last_seen_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT(id) DO UPDATE SET
                        last_seen_at = CURRENT_TIMESTAMP,
                        company = excluded.company,
                        title = excluded.title,
                        location = excluded.location,
                        apply_url = excluded.apply_url,
                        published_date = excluded.published_date,
                        is_active = 1,
                        is_remote = excluded.is_remote,
                        direct_search_url = COALESCE(excluded.direct_search_url, seen_jobs.direct_search_url),
                        relevance_score = excluded.relevance_score
                    """,
                    (
                        job_key,
                        job.company,
                        job.title,
                        job.location,
                        clean_url,
                        job.provider.value,
                        job.published_date or "Active",
                        1 if job.is_remote else 0,
                        getattr(job, "status", None) or "NEW",
                        getattr(job, "applied_at", None)
                        or (
                            datetime.now(timezone.utc).isoformat()
                            if getattr(job, "status", None) == "APPLIED"
                            else None
                        ),
                        getattr(job, "notes", None),
                        getattr(job, "direct_search_url", None),
                        job_score,
                    ),
                )

        conn.commit()

        # Attach persisted database rowid, status, applied_at, notes, direct_search_url, and relevance_score back to the JobPosting instances
        cursor.execute(
            "SELECT rowid, id, lower(apply_url), lower(company), lower(title), lower(location), status, applied_at, notes, direct_search_url, relevance_score FROM seen_jobs"
        )
        rows = cursor.fetchall()
        id_map = {r[1]: (r[0], r[6], r[7], r[8], r[9], r[10]) for r in rows}
        url_map = {r[2]: (r[0], r[6], r[7], r[8], r[9], r[10]) for r in rows if r[2]}
        role_map = {(r[3], r[4], r[5]): (r[0], r[6], r[7], r[8], r[9], r[10]) for r in rows}

        for j in jobs:
            clean_u = canonicalize_url(str(j.apply_url)).lower()
            sem_k = (
                j.company.lower().strip(),
                j.title.lower().strip(),
                j.location.lower().strip(),
            )
            meta = id_map.get(make_job_key(j)) or url_map.get(clean_u) or role_map.get(sem_k)
            if meta:
                setattr(j, "numeric_id", meta[0])
                setattr(j, "status", meta[1])
                setattr(j, "applied_at", meta[2])
                setattr(j, "notes", meta[3])
                setattr(j, "direct_search_url", meta[4])
                setattr(j, "relevance_score", meta[5] if meta[5] is not None else 0)


def save_job(job: JobPosting, db_path: Optional[Path] = None) -> None:
    """Save a single job posting to the database, delegating to record_jobs."""
    record_jobs([job], db_path=db_path)


def get_stats(db_path: Optional[Path] = None) -> dict[str, Any]:
    """Retrieve historical tracking stats from the database."""
    init_db(db_path)
    target_path = get_db_path(db_path)

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM seen_jobs")
        total_tracked = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM seen_jobs WHERE is_remote = 1")
        total_remote = cursor.fetchone()[0]

        cursor.execute("SELECT MIN(first_seen_at), MAX(last_seen_at) FROM seen_jobs")
        first_recorded, last_active = cursor.fetchone()

        cursor.execute(
            """
            SELECT company, COUNT(*) 
            FROM seen_jobs 
            GROUP BY company 
            ORDER BY COUNT(*) DESC, company ASC
            """
        )
        company_counts = dict(cursor.fetchall())

        cursor.execute("SELECT UPPER(COALESCE(status, 'NEW')), COUNT(*) FROM seen_jobs GROUP BY UPPER(COALESCE(status, 'NEW'))")
        status_counts = dict(cursor.fetchall())

    return {
        "total_tracked": total_tracked,
        "total_remote": total_remote,
        "status_counts": status_counts,
        "active_count": status_counts.get("NEW", 0),
        "needs_resolve_count": status_counts.get("NEEDS_RESOLVE", 0),
        "dismissed_count": status_counts.get("DISMISSED", 0),
        "applied_count": status_counts.get("APPLIED", 0),
        "interviewing_count": status_counts.get("INTERVIEWING", 0),
        "rejected_count": status_counts.get("REJECTED", 0),
        "company_breakdown": company_counts,
        "first_recorded": first_recorded,
        "last_active": last_active,
        "db_path": str(target_path.resolve()),
    }


def get_stale_applications(
    days: int = 7,
    db_path: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """Retrieve jobs marked as APPLIED where applied_at is older than `days` ago."""
    init_db(db_path)
    target_path = get_db_path(db_path)

    with sqlite3.connect(target_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                rowid AS numeric_id,
                id,
                company,
                title,
                location,
                apply_url,
                direct_search_url,
                applied_at,
                notes,
                relevance_score
            FROM seen_jobs
            WHERE UPPER(status) = 'APPLIED' AND applied_at IS NOT NULL
            """
        )
        rows = cursor.fetchall()

    now_utc = datetime.now(timezone.utc)
    results = []
    threshold = max(0, days)

    for r in rows:
        raw_applied = r["applied_at"]
        elapsed_days = 0
        if raw_applied:
            try:
                date_str = str(raw_applied).strip()
                if date_str.endswith("Z"):
                    dt = datetime.fromisoformat(date_str[:-1] + "+00:00")
                else:
                    dt = datetime.fromisoformat(date_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                elapsed_days = max(0, (now_utc - dt).days)
            except Exception:
                elapsed_days = 0

        if elapsed_days >= threshold:
            item = dict(r)
            item["days_elapsed"] = elapsed_days
            item["relevance_score"] = item.get("relevance_score") or 0
            results.append(item)

    results.sort(key=lambda x: x.get("days_elapsed", 0), reverse=True)
    return results


def get_latest_jobs(
    limit: int = 5,
    status: Optional[str] = "NEW",
    db_path: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """Retrieve the most recently recorded or active jobs from the database, deduplicated by role."""
    init_db(db_path)
    target_path = get_db_path(db_path)

    inner_where = "WHERE 1=1"
    params: list[Any] = []
    if status is not None and status.strip():
        stat_norm = status.strip().upper()
        if stat_norm != "ALL":
            inner_where += " AND UPPER(status) = ?"
            params.append(stat_norm)

    with sqlite3.connect(target_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT rowid AS numeric_id, id, company, title, location, apply_url, provider, published_date, is_remote, status, applied_at, notes, direct_search_url, relevance_score, first_seen_at, last_seen_at
            FROM (
                SELECT rowid, id, company, title, location, apply_url, provider, published_date, is_remote, status, applied_at, notes, direct_search_url, relevance_score, first_seen_at, last_seen_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY lower(company), lower(title), lower(location)
                           ORDER BY relevance_score DESC, last_seen_at DESC, first_seen_at DESC
                       ) as rn
                FROM seen_jobs
                {inner_where}
            )
            WHERE rn = 1
            ORDER BY relevance_score DESC, last_seen_at DESC, first_seen_at DESC
            LIMIT ?
            """,
            params + [max(1, limit)],
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def get_applied_and_dismissed_companies(
    db_path: Optional[Path] = None,
) -> tuple[set[str], set[str]]:
    """Retrieve lowercase sets of company names that have at least one APPLIED or DISMISSED role.

    Returns:
        tuple[set[str], set[str]]: (applied_companies, dismissed_companies)
    """
    init_db(db_path)
    target_path = get_db_path(db_path)

    applied_companies: set[str] = set()
    dismissed_companies: set[str] = set()

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='seen_jobs'")
        table_name = "seen_jobs" if cursor.fetchone() else "jobs"

        cursor.execute(
            f"""
            SELECT DISTINCT lower(trim(company))
            FROM {table_name}
            WHERE UPPER(COALESCE(status, 'NEW')) = 'APPLIED'
              AND company IS NOT NULL AND trim(company) != ''
            """
        )
        applied_companies = {row[0] for row in cursor.fetchall() if row[0]}

        cursor.execute(
            f"""
            SELECT DISTINCT lower(trim(company))
            FROM {table_name}
            WHERE UPPER(COALESCE(status, 'NEW')) = 'DISMISSED'
              AND company IS NOT NULL AND trim(company) != ''
            """
        )
        dismissed_companies = {row[0] for row in cursor.fetchall() if row[0]}

    return applied_companies, dismissed_companies


def is_company_dismissed(company: str, db_path: Optional[Path] = None) -> bool:
    """Check if a company is in the dismissed list or registry."""
    _, dismissed = get_applied_and_dismissed_companies(db_path=db_path)
    return is_company_excluded(company, dismissed)


def is_company_excluded(company: str, excluded_companies: set[str]) -> bool:
    """Check if a company matches an applied or dismissed company name.

    Supports:
    - Exact lowercase match
    - Substring match (e.g. 'katalystcs' matches 'KATALYSTCS CONSULTING SERVICES PRIVATE LIMITED')
    - Whole-word regex match for short tokens (e.g. 'wsp' matches 'WSP India')
    """
    if not company or not excluded_companies:
        return False
    comp_lower = company.lower().strip()
    if comp_lower in excluded_companies:
        return True

    for excl in excluded_companies:
        excl_clean = str(excl).strip().lower()
        if not excl_clean:
            continue
        if len(excl_clean) >= 4 and excl_clean in comp_lower:
            return True
        if len(comp_lower) >= 4 and comp_lower in excl_clean:
            return True
        if re.search(r"\b" + re.escape(excl_clean) + r"\b", comp_lower):
            return True

    return False


def query_jobs(
    title_keyword: Optional[str] = None,
    location: Optional[str] = None,
    company: Optional[str] = None,
    is_remote: Optional[bool] = None,
    status: Optional[str] = "NEW",
    min_score: Optional[int] = None,
    limit: int = 5,
    db_path: Optional[Path] = None,
    exclude_applied_or_dismissed_companies: bool = False,
) -> list[dict[str, Any]]:
    """Query jobs from database with optional filters, deduplicated by role."""
    init_db(db_path)
    target_path = get_db_path(db_path)

    inner_where = "WHERE 1=1"
    params: list[Any] = []

    if status is not None and status.strip():
        stat_norm = status.strip().upper()
        if stat_norm != "ALL":
            inner_where += " AND UPPER(status) = ?"
            params.append(stat_norm)

    if min_score is not None and min_score > 0:
        inner_where += " AND relevance_score >= ?"
        params.append(min_score)

    if company and company.strip():
        inner_where += " AND company LIKE ?"
        params.append(f"%{company.strip()}%")
    elif exclude_applied_or_dismissed_companies and (status is None or status.strip().upper() == "NEW"):
        applied_comps, dismissed_comps = get_applied_and_dismissed_companies(db_path)
        excluded = applied_comps | dismissed_comps
        if excluded:
            placeholders = ",".join("?" for _ in excluded)
            inner_where += f" AND lower(trim(company)) NOT IN ({placeholders})"
            params.extend(list(excluded))

    if title_keyword and title_keyword.strip():
        inner_where += " AND title LIKE ?"
        params.append(f"%{title_keyword.strip()}%")

    if is_remote is True:
        inner_where += " AND is_remote = 1"
    elif is_remote is False:
        inner_where += " AND is_remote = 0"

    if location and location.strip():
        loc_str = location.strip().lower()
        if "bangalore" in loc_str or "bengaluru" in loc_str:
            inner_where += " AND (location LIKE ? OR location LIKE ?)"
            params.extend(["%bangalore%", "%bengaluru%"])
        elif "gurgaon" in loc_str or "gurugram" in loc_str:
            inner_where += " AND (location LIKE ? OR location LIKE ?)"
            params.extend(["%gurgaon%", "%gurugram%"])
        else:
            inner_where += " AND location LIKE ?"
            params.append(f"%{loc_str}%")

    query = f"""
        SELECT rowid AS numeric_id, id, company, title, location, apply_url, provider, published_date, is_remote, status, applied_at, notes, direct_search_url, relevance_score, first_seen_at, last_seen_at
        FROM (
            SELECT rowid, id, company, title, location, apply_url, provider, published_date, is_remote, status, applied_at, notes, direct_search_url, relevance_score, first_seen_at, last_seen_at,
                   ROW_NUMBER() OVER (
                       PARTITION BY lower(company), lower(title), lower(location)
                       ORDER BY relevance_score DESC, last_seen_at DESC, first_seen_at DESC
                   ) as rn
            FROM seen_jobs
            {inner_where}
        )
        WHERE rn = 1
        ORDER BY relevance_score DESC, last_seen_at DESC, first_seen_at DESC
        LIMIT ?
    """
    params.append(max(1, limit))

    with sqlite3.connect(target_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


VALID_JOB_STATUSES = {"NEW", "APPLIED", "INTERVIEWING", "REJECTED", "DISMISSED", "NEEDS_RESOLVE"}


def update_job_direct_search_url(
    job_id: Union[int, str],
    direct_search_url: str,
    db_path: Optional[Path] = None,
) -> bool:
    """Update direct_search_url for a job by numeric rowid or string ID."""
    target_job = get_job_by_id(job_id, db_path=db_path)
    if not target_job:
        return False

    init_db(db_path)
    target_path = get_db_path(db_path)
    target_rowid = target_job["numeric_id"]

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='seen_jobs'")
        table_name = "seen_jobs" if cursor.fetchone() else "jobs"
        cursor.execute(
            f"UPDATE {table_name} SET direct_search_url = ? WHERE rowid = ?",
            (direct_search_url, target_rowid),
        )
        conn.commit()
        return cursor.rowcount > 0


def mark_job_status(
    job_id: Union[int, str],
    status: str,
    notes: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> bool:
    """Update tracking status (NEW, APPLIED, INTERVIEWING, REJECTED, DISMISSED, NEEDS_RESOLVE) and notes for a job.

    Accepts numeric rowid (e.g. 12 or "12") or string ID (e.g. "greenhouse_celonis_7791267003").
    """
    if not status or not isinstance(status, str):
        raise ValueError("Job status must be a non-empty string.")

    status_norm = status.strip().upper()
    if status_norm not in VALID_JOB_STATUSES:
        raise ValueError(
            f"Invalid status '{status}'. Must be one of: {', '.join(sorted(VALID_JOB_STATUSES))}"
        )

    target_job = get_job_by_id(job_id, db_path=db_path)
    if not target_job:
        return False

    init_db(db_path)
    target_path = get_db_path(db_path)

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='seen_jobs'")
        table_name = "seen_jobs" if cursor.fetchone() else "jobs"

        target_rowid = target_job["numeric_id"]
        now_iso = datetime.now(timezone.utc).isoformat()

        if status_norm == "APPLIED":
            if notes is not None:
                cursor.execute(
                    f"UPDATE {table_name} SET status = ?, applied_at = COALESCE(applied_at, ?), notes = ? WHERE rowid = ?",
                    (status_norm, now_iso, notes, target_rowid),
                )
            else:
                cursor.execute(
                    f"UPDATE {table_name} SET status = ?, applied_at = COALESCE(applied_at, ?) WHERE rowid = ?",
                    (status_norm, now_iso, target_rowid),
                )
        else:
            if notes is not None:
                cursor.execute(
                    f"UPDATE {table_name} SET status = ?, notes = ? WHERE rowid = ?",
                    (status_norm, notes, target_rowid),
                )
            else:
                cursor.execute(
                    f"UPDATE {table_name} SET status = ? WHERE rowid = ?",
                    (status_norm, target_rowid),
                )
        conn.commit()


        if status_norm == "DISMISSED":
            jid = target_job.get("id") or str(target_rowid)
            comp = str(target_job.get("company") or "")
            prov = str(target_job.get("provider") or "")
            record_dispatched_alert(jid, "telegram", db_path=db_path)
            record_dispatched_alert(jid, "discord", db_path=db_path)
            if comp and prov:
                record_dispatched_alert(f"{prov}_{comp.lower()}_{jid}", "telegram", db_path=db_path)
                record_dispatched_alert(f"{prov}_{comp.lower()}_{jid}", "discord", db_path=db_path)
            save_dismissal_to_registry(job_dict=target_job, db_path=db_path)

        return cursor.rowcount > 0


def record_manual_job(
    company: str,
    title: str = "Software Engineer",
    location: str = "India",
    status: str = "APPLIED",
    notes: Optional[str] = None,
    apply_url: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Record an ad-hoc or manually tracked job application/posting into seen_jobs.

    If an existing record matches the company (and title), it updates its status,
    applied timestamp, and notes. Otherwise, it inserts a new record with a unique
    ID and returns the complete job record including numeric_id.
    """
    comp = str(company or "").strip()
    if not comp:
        raise ValueError("Company name must not be empty.")

    tit = str(title or "").strip() or "Software Engineer"
    loc = str(location or "").strip() or "India"
    status_norm = str(status or "APPLIED").strip().upper()
    if status_norm not in VALID_JOB_STATUSES:
        status_norm = "APPLIED"

    init_db(db_path)
    target_path = get_db_path(db_path)

    if not apply_url:
        from gcc_job_radar.link_resolver import build_direct_careers_redirect_url

        apply_url = build_direct_careers_redirect_url(comp, tit)

    clean_url = canonicalize_url(str(apply_url))
    now_iso = datetime.now(timezone.utc).isoformat()
    applied_at = now_iso if status_norm == "APPLIED" else None

    # Slug for unique ID
    comp_slug = re.sub(r"[^a-zA-Z0-9]+", "_", comp.lower()).strip("_") or "company"
    import time

    ts = int(time.time() * 1000)
    manual_id = f"manual_{comp_slug}_{ts}"

    with sqlite3.connect(target_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='seen_jobs'")
        table_name = "seen_jobs" if cursor.fetchone() else "jobs"

        # Check for existing job with matching company and title
        cursor.execute(
            f"""
            SELECT rowid AS numeric_id, * FROM {table_name}
            WHERE lower(trim(company)) = lower(trim(?))
              AND (lower(trim(title)) = lower(trim(?)) OR lower(title) LIKE '%' || lower(?) || '%')
            ORDER BY last_seen_at DESC LIMIT 1
            """,
            (comp, tit, tit),
        )
        existing = cursor.fetchone()

        if existing:
            target_rowid = existing["numeric_id"]
            if status_norm == "APPLIED":
                cursor.execute(
                    f"UPDATE {table_name} SET status = ?, applied_at = COALESCE(applied_at, ?), notes = COALESCE(?, notes), last_seen_at = CURRENT_TIMESTAMP WHERE rowid = ?",
                    (status_norm, now_iso, notes, target_rowid),
                )
            else:
                cursor.execute(
                    f"UPDATE {table_name} SET status = ?, notes = COALESCE(?, notes), last_seen_at = CURRENT_TIMESTAMP WHERE rowid = ?",
                    (status_norm, notes, target_rowid),
                )
            conn.commit()
            cursor.execute(f"SELECT rowid AS numeric_id, * FROM {table_name} WHERE rowid = ?", (target_rowid,))
            return dict(cursor.fetchone())

        # Check by company alone if title is generic
        cursor.execute(
            f"""
            SELECT rowid AS numeric_id, * FROM {table_name}
            WHERE lower(trim(company)) = lower(trim(?))
            ORDER BY last_seen_at DESC LIMIT 1
            """,
            (comp,),
        )
        existing_comp = cursor.fetchone()
        if existing_comp and tit in ("Software Engineer", "Role", ""):
            target_rowid = existing_comp["numeric_id"]
            if status_norm == "APPLIED":
                cursor.execute(
                    f"UPDATE {table_name} SET status = ?, applied_at = COALESCE(applied_at, ?), notes = COALESCE(?, notes), last_seen_at = CURRENT_TIMESTAMP WHERE rowid = ?",
                    (status_norm, now_iso, notes, target_rowid),
                )
            else:
                cursor.execute(
                    f"UPDATE {table_name} SET status = ?, notes = COALESCE(?, notes), last_seen_at = CURRENT_TIMESTAMP WHERE rowid = ?",
                    (status_norm, notes, target_rowid),
                )
            conn.commit()
            cursor.execute(f"SELECT rowid AS numeric_id, * FROM {table_name} WHERE rowid = ?", (target_rowid,))
            return dict(cursor.fetchone())

        # Otherwise insert fresh manual job
        cursor.execute(
            f"""
            INSERT INTO seen_jobs (
                id, company, title, location, apply_url, provider, published_date,
                is_active, is_remote, status, applied_at, notes, direct_search_url,
                relevance_score, first_seen_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, 'custom', 'Recent', 1, ?, ?, ?, ?, ?, 10, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                manual_id,
                comp,
                tit,
                loc,
                clean_url,
                1 if "remote" in loc.lower() else 0,
                status_norm,
                applied_at,
                notes or ("Application recorded" if status_norm == "APPLIED" else None),
                apply_url,
            ),
        )
        conn.commit()

        cursor.execute(f"SELECT rowid AS numeric_id, * FROM seen_jobs WHERE id = ?", (manual_id,))
        new_row = cursor.fetchone()
        return dict(new_row) if new_row else {}


def purge_or_dismiss_job(
    job_id: Union[int, str],
    reason: str,
    hard_delete: bool = False,
    db_path: Optional[Path] = None,
) -> bool:
    """Purge (delete) or dismiss a job by ID, recording the reason.

    If hard_delete is True, permanently deletes the record from seen_jobs.
    If hard_delete is False, marks status as 'DISMISSED' and sets/appends reason to notes.
    Accepts numeric rowid or string ID.
    """
    target_job = get_job_by_id(job_id, db_path=db_path)
    if not target_job:
        return False

    init_db(db_path)
    target_path = get_db_path(db_path)
    target_rowid = target_job["numeric_id"]

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='seen_jobs'")
        table_name = "seen_jobs" if cursor.fetchone() else "jobs"

        if hard_delete:
            cursor.execute(f"DELETE FROM {table_name} WHERE rowid = ?", (target_rowid,))
            conn.commit()
            return cursor.rowcount > 0
        else:
            existing_notes = target_job.get("notes") or ""
            note_str = f"Auto-dismissed: {reason.strip()}"
            if existing_notes and note_str not in existing_notes:
                final_notes = f"{existing_notes} | {note_str}"
            else:
                final_notes = note_str

            cursor.execute(
                f"UPDATE {table_name} SET status = 'DISMISSED', is_active = 0, notes = ? WHERE rowid = ?",
                (final_notes, target_rowid),
            )
            conn.commit()
            return cursor.rowcount > 0


def get_job_by_id(
    job_id: Union[int, str],
    db_path: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    """Retrieve a single job dictionary by numeric rowid or string ID."""
    init_db(db_path)
    target_path = get_db_path(db_path)

    with sqlite3.connect(target_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='seen_jobs'")
        table_name = "seen_jobs" if cursor.fetchone() else "jobs"

        is_num = isinstance(job_id, int) or (isinstance(job_id, str) and str(job_id).strip().isdigit())
        if is_num:
            # 1. Exact numeric rowid match first
            cursor.execute(
                f"SELECT rowid AS numeric_id, * FROM {table_name} WHERE rowid = ? LIMIT 1",
                (int(job_id),),
            )
            row = cursor.fetchone()
            if row:
                return dict(row)

            # 2. Fallback to exact string id match if an ATS ID is numeric
            cursor.execute(
                f"SELECT rowid AS numeric_id, * FROM {table_name} WHERE id = ? LIMIT 1",
                (str(job_id).strip(),),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        else:
            raw_id = str(job_id).strip()
            # 1. Exact string id match
            cursor.execute(
                f"SELECT rowid AS numeric_id, * FROM {table_name} WHERE id = ? LIMIT 1",
                (raw_id,),
            )
            row = cursor.fetchone()
            if row:
                return dict(row)

            # 2. Match by apply URL or canonical apply URL
            clean_url = canonicalize_url(raw_id).lower()
            cursor.execute(
                f"SELECT rowid AS numeric_id, * FROM {table_name} WHERE lower(apply_url) = ? OR lower(apply_url) = ? OR lower(apply_url) = ? LIMIT 1",
                (raw_id.lower(), raw_id.lower().rstrip("/"), clean_url),
            )
            row = cursor.fetchone()
            if row:
                return dict(row)

            # 3. Match ATS specific ID suffix with escaped underscore
            cursor.execute(
                f"SELECT rowid AS numeric_id, * FROM {table_name} WHERE id LIKE ? ESCAPE '\\' LIMIT 1",
                (f"%\\_{raw_id}",),
            )
            row = cursor.fetchone()
            return dict(row) if row else None


def get_jobs_by_status(
    status: str,
    limit: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """Retrieve jobs with a specific status ('NEW', 'APPLIED', 'INTERVIEWING', 'REJECTED', 'DISMISSED', or 'ALL')."""
    init_db(db_path)
    target_path = get_db_path(db_path)

    status_norm = status.strip().upper()
    with sqlite3.connect(target_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='seen_jobs'")
        table_name = "seen_jobs" if cursor.fetchone() else "jobs"

        if status_norm == "ALL":
            query = f"SELECT rowid AS numeric_id, * FROM {table_name} ORDER BY last_seen_at DESC"
            params: list[Any] = []
        else:
            query = f"SELECT rowid AS numeric_id, * FROM {table_name} WHERE UPPER(status) = ? ORDER BY last_seen_at DESC"
            params = [status_norm]

        if limit is not None and limit > 0:
            query += " LIMIT ?"
            params.append(limit)

        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]


def find_jobs_by_selector(
    selector: str,
    db_path: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """Resolve one or more jobs by numeric rowids, string IDs, or company/title text search.

    Supports:
    - Single numeric ID: "42", "#42"
    - Multiple comma- or space-separated numeric IDs: "1, 2, 3", "1 4 7", "#1, #2", "1 and 4"
    - Exact string ID / URL: "email_alert_...", "greenhouse_celonis_..."
    - Company name or role title substring match: "Devmani Traders", "BT Group", "Associate Engineer"
    """
    if not selector or not str(selector).strip():
        return []

    init_db(db_path)
    target_path = get_db_path(db_path)
    raw = str(selector).strip()

    # 1. Check if input is a list of comma-, semicolon-, or whitespace-separated numeric tokens
    tokens = [
        re.sub(r"^#", "", t).strip()
        for t in re.split(r"[,;\s]+", raw)
        if t.strip() and t.lower() not in ("and", "&")
    ]

    if tokens and all(t.isdigit() for t in tokens):
        results: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for tok in tokens:
            job = get_job_by_id(tok, db_path=db_path)
            if job and job["id"] not in seen_ids:
                seen_ids.add(job["id"])
                results.append(job)
        if results:
            return results

    # 2. Try single exact ID lookup (handles single number, #ID, or full unique key)
    cleaned_single = re.sub(r"^#", "", raw).strip()
    single_job = get_job_by_id(cleaned_single, db_path=db_path)
    if single_job:
        return [single_job]

    # 3. Check for multiple comma- or 'and'-separated company/role queries (e.g. "uipath, celonis", "BT Group and Devmani Traders")
    sub_queries = [
        s.strip()
        for s in re.split(r"[,;]|\s+(?:and|&)\s+", raw, flags=re.IGNORECASE)
        if s.strip() and s.lower() not in ("and", "&")
    ]

    with sqlite3.connect(target_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='seen_jobs'")
        table_name = "seen_jobs" if cursor.fetchone() else "jobs"

        if len(sub_queries) > 1:
            multi_results: list[dict[str, Any]] = []
            seen_ids: set[str] = set()
            for sq in sub_queries:
                cleaned_sq = re.sub(r"^#", "", sq).strip()
                if cleaned_sq.isdigit():
                    j = get_job_by_id(cleaned_sq, db_path=db_path)
                    if j and j["id"] not in seen_ids:
                        seen_ids.add(j["id"])
                        multi_results.append(j)
                    continue

                term = f"%{sq.lower()}%"
                cursor.execute(
                    f"""
                    SELECT rowid AS numeric_id, * FROM {table_name}
                    WHERE lower(company) LIKE ? OR lower(title) LIKE ?
                    ORDER BY last_seen_at DESC LIMIT 25
                    """,
                    (term, term),
                )
                for r in cursor.fetchall():
                    jd = dict(r)
                    if jd["id"] not in seen_ids:
                        seen_ids.add(jd["id"])
                        multi_results.append(jd)

            if multi_results:
                return multi_results

        # 4. Fallback: Search by company name or title keyword in seen_jobs
        search_term = f"%{raw.lower()}%"
        cursor.execute(
            f"""
            SELECT rowid AS numeric_id, * FROM {table_name}
            WHERE lower(company) LIKE ? OR lower(title) LIKE ?
            ORDER BY last_seen_at DESC LIMIT 25
            """,
            (search_term, search_term),
        )
        rows = cursor.fetchall()
        return [dict(r) for r in rows]


def dismiss_selectors_or_companies(
    selector: str,
    notes: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Dismiss jobs or companies by ID, comma-separated tokens, or company names.

    If matching jobs exist in seen_jobs, their status is updated to 'DISMISSED'.
    If a company token does not currently match any rows in seen_jobs (e.g. fresh deploy,
    alert from another environment, or ephemeral disk), an ad-hoc suppression record is
    created via record_manual_job(..., status='DISMISSED') so the company is permanently
    registered as dismissed and filtered out of all future scans, digests, and email alerts.

    Returns:
        dict with keys:
            - status: "success" or "empty"
            - dismissed_jobs: list of dicts for jobs marked DISMISSED
            - dismissed_adhoc: list of dicts for ad-hoc company suppressions created
            - dismissed_companies: list of all company names affected
            - total_jobs: int
            - total_adhoc: int
    """
    if not selector or not str(selector).strip():
        return {
            "status": "empty",
            "dismissed_jobs": [],
            "dismissed_adhoc": [],
            "dismissed_companies": [],
            "total_jobs": 0,
            "total_adhoc": 0,
        }

    init_db(db_path)
    target_path = get_db_path(db_path)
    raw = str(selector).strip()

    # Split into candidate tokens by comma, semicolon, newline, or 'and'/'&'
    sub_tokens = [
        s.strip()
        for s in re.split(r"[,;\n]|\s+(?:and|&)\s+", raw, flags=re.IGNORECASE)
        if s.strip() and s.lower() not in ("and", "&", "the")
    ]

    if not sub_tokens:
        sub_tokens = [raw]

    dismissed_jobs: list[dict[str, Any]] = []
    dismissed_adhoc: list[dict[str, Any]] = []
    dismissed_companies: set[str] = set()
    processed_job_ids: set[str] = set()

    with sqlite3.connect(target_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='seen_jobs'")
        table_name = "seen_jobs" if cursor.fetchone() else "jobs"

        for tok in sub_tokens:
            tok_clean = tok.strip().strip("'").strip('"').strip("`")
            if not tok_clean:
                continue

            clean_num = re.sub(r"^#", "", tok_clean).strip()
            if clean_num.isdigit():
                # Direct numeric job ID
                cursor.execute(
                    f"SELECT rowid AS numeric_id, * FROM {table_name} WHERE rowid = ? OR id = ? LIMIT 1",
                    (int(clean_num), clean_num),
                )
                r = cursor.fetchone()
                if r:
                    jd = dict(r)
                    jid = str(jd["id"])
                    if jid not in processed_job_ids:
                        processed_job_ids.add(jid)
                        rowid = jd.get("numeric_id") or jid
                        mark_job_status(job_id=rowid, status="DISMISSED", notes=notes, db_path=db_path)
                        jd["status"] = "DISMISSED"
                        dismissed_jobs.append(jd)
                        if jd.get("company"):
                            dismissed_companies.add(jd["company"])
                continue

            # Text token: search for matching company or title in seen_jobs
            term = f"%{tok_clean.lower()}%"
            cursor.execute(
                f"""
                SELECT rowid AS numeric_id, * FROM {table_name}
                WHERE lower(company) LIKE ? OR lower(title) LIKE ?
                ORDER BY last_seen_at DESC LIMIT 50
                """,
                (term, term),
            )
            matched_rows = cursor.fetchall()

            if matched_rows:
                for r in matched_rows:
                    jd = dict(r)
                    jid = str(jd["id"])
                    if jid not in processed_job_ids:
                        processed_job_ids.add(jid)
                        rowid = jd.get("numeric_id") or jid
                        mark_job_status(job_id=rowid, status="DISMISSED", notes=notes, db_path=db_path)
                        jd["status"] = "DISMISSED"
                        dismissed_jobs.append(jd)
                        if jd.get("company"):
                            dismissed_companies.add(jd["company"])
            else:
                # No rows exist yet for this company! Create an ad-hoc dismissal record
                comp_display = tok_clean if any(c.isupper() for c in tok_clean) else tok_clean.title()
                adhoc_job = record_manual_job(
                    company=comp_display,
                    title="All Roles",
                    status="DISMISSED",
                    notes=notes or "Company dismissed by user",
                    db_path=db_path,
                )
                if adhoc_job:
                    rowid = adhoc_job.get("numeric_id") or adhoc_job.get("id")
                    dismissed_adhoc.append({
                        "id": rowid,
                        "company": comp_display,
                        "title": "All Roles (Suppressed)",
                        "status": "DISMISSED",
                    })
                    dismissed_companies.add(comp_display)

    for jd in dismissed_jobs:
        save_dismissal_to_registry(job_dict=jd, db_path=db_path)
    for adh in dismissed_adhoc:
        save_dismissal_to_registry(company_name=adh.get("company"), db_path=db_path)

    return {
        "status": "success",
        "dismissed_jobs": dismissed_jobs,
        "dismissed_adhoc": dismissed_adhoc,
        "dismissed_companies": sorted(dismissed_companies),
        "total_jobs": len(dismissed_jobs),
        "total_adhoc": len(dismissed_adhoc),
    }



def is_email_seen(
    uid: str,
    db_path: Optional[Path] = None,
    account: Optional[str] = None,
) -> bool:
    """Check if an email UID has already been processed for a given account."""
    init_db(db_path)
    target_path = get_db_path(db_path)
    clean_u = str(uid).strip()
    keys = [f"{account}:{clean_u}"] if account else [clean_u]
    if account and account.lower() == "chinmay8064@gmail.com":
        keys.append(clean_u)
    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        placeholders = ",".join("?" for _ in keys)
        cursor.execute(f"SELECT 1 FROM seen_emails WHERE uid IN ({placeholders}) LIMIT 1", keys)
        return cursor.fetchone() is not None


def filter_unseen_email_uids(
    uids: list[str],
    db_path: Optional[Path] = None,
    account: Optional[str] = None,
) -> list[str]:
    """Filter out email UIDs that have already been recorded in seen_emails for a given account."""
    if not uids:
        return []
    init_db(db_path)
    target_path = get_db_path(db_path)
    clean_uids = [str(u).strip() for u in uids if str(u).strip()]
    if not clean_uids:
        return []

    # Map candidate keys to check in DB
    # If account is given, check f"{account}:{u}".
    # If account is chinmay8064@gmail.com, also check bare u for backward compatibility.
    keys_to_uid: dict[str, str] = {}
    for u in clean_uids:
        if account:
            keys_to_uid[f"{account}:{u}"] = u
            if account.lower() == "chinmay8064@gmail.com":
                keys_to_uid[u] = u
        else:
            keys_to_uid[u] = u

    all_keys = list(keys_to_uid.keys())
    seen_keys: set[str] = set()
    chunk_size = 500
    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        for i in range(0, len(all_keys), chunk_size):
            chunk = all_keys[i : i + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            cursor.execute(
                f"SELECT uid FROM seen_emails WHERE uid IN ({placeholders})",
                chunk,
            )
            seen_keys.update(row[0] for row in cursor.fetchall())

    seen_uids = {keys_to_uid[k] for k in seen_keys if k in keys_to_uid}
    return [u for u in clean_uids if u not in seen_uids]


def record_seen_email_uids(
    uids: list[str],
    db_path: Optional[Path] = None,
    account: Optional[str] = None,
) -> None:
    """Record email UIDs into seen_emails so they are skipped in subsequent syncs."""
    if not uids:
        return
    init_db(db_path)
    target_path = get_db_path(db_path)
    clean_keys = [
        (f"{account}:{str(u).strip()}" if account else str(u).strip(),)
        for u in uids
        if str(u).strip()
    ]
    if not clean_keys:
        return

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        cursor.executemany(
            """
            INSERT OR IGNORE INTO seen_emails (uid, processed_at)
            VALUES (?, CURRENT_TIMESTAMP)
            """,
            clean_keys,
        )
        conn.commit()


def record_companies_scan_activity(
    company_counts: list[tuple[str, int]],
    auto_dormant_threshold: int = 5,
    db_path: Optional[Path] = None,
) -> list[str]:
    """Batch track scan match activity for multiple companies in a single database transaction.

    Returns list of company names that transitioned to dormant.
    """
    if not company_counts:
        return []

    target_path = get_db_path(db_path)
    if not target_path.exists():
        init_db(db_path)

    transitioned_dormant: list[str] = []

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS dormant_companies (
                company_name TEXT PRIMARY KEY,
                reason TEXT,
                paused_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                consecutive_zero_scans INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 0,
                notes TEXT
            );
            """
        )
        for company_name, jobs_found in company_counts:
            if not company_name or not company_name.strip():
                continue
            comp_norm = company_name.strip()
            cursor.execute(
                "SELECT consecutive_zero_scans, is_active FROM dormant_companies WHERE lower(company_name) = lower(?)",
                (comp_norm,),
            )
            row = cursor.fetchone()

            if jobs_found > 0:
                if row:
                    cursor.execute(
                        "UPDATE dormant_companies SET consecutive_zero_scans = 0 WHERE lower(company_name) = lower(?)",
                        (comp_norm,),
                    )
            else:
                current_zero = row[0] if row else 0
                is_active = row[1] if row else 1
                new_zero = current_zero + 1

                if auto_dormant_threshold > 0 and new_zero >= auto_dormant_threshold and is_active == 1:
                    reason = f"Auto-dormant: {new_zero} consecutive scans with 0 matching entry-level openings"
                    cursor.execute(
                        """
                        INSERT INTO dormant_companies (company_name, reason, paused_at, consecutive_zero_scans, is_active, notes)
                        VALUES (?, ?, CURRENT_TIMESTAMP, ?, 0, 'Auto-paused after consecutive zero-match scans')
                        ON CONFLICT(company_name) DO UPDATE SET
                            consecutive_zero_scans = excluded.consecutive_zero_scans,
                            is_active = 0,
                            reason = excluded.reason,
                            paused_at = CURRENT_TIMESTAMP
                        """,
                        (comp_norm, reason, new_zero),
                    )
                    transitioned_dormant.append(comp_norm)
                else:
                    cursor.execute(
                        """
                        INSERT INTO dormant_companies (company_name, reason, paused_at, consecutive_zero_scans, is_active, notes)
                        VALUES (?, 'Tracking activity', CURRENT_TIMESTAMP, ?, 1, NULL)
                        ON CONFLICT(company_name) DO UPDATE SET
                            consecutive_zero_scans = excluded.consecutive_zero_scans
                        """,
                        (comp_norm, new_zero),
                    )
        conn.commit()

    return transitioned_dormant


def record_company_scan_activity(
    company_name: str,
    jobs_found: int,
    auto_dormant_threshold: int = 5,
    db_path: Optional[Path] = None,
) -> bool:
    """Track scan match activity for a company and mark dormant if consecutive zero matches reach threshold.

    Returns True if company was transitioned to dormant.
    """
    res = record_companies_scan_activity(
        [(company_name, jobs_found)],
        auto_dormant_threshold=auto_dormant_threshold,
        db_path=db_path,
    )
    return len(res) > 0


def get_dormant_company_names(db_path: Optional[Path] = None) -> set[str]:
    """Retrieve all company names (lowercase) currently marked as dormant."""
    init_db(db_path)
    target_path = get_db_path(db_path)

    from gcc_job_radar.dormant_companies import DORMANT_COMPANIES
    dormant_names = {c.name.strip().lower() for c in DORMANT_COMPANIES}

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT lower(company_name) FROM dormant_companies WHERE is_active = 0"
        )
        for r in cursor.fetchall():
            dormant_names.add(r[0])

        # Respect if any were explicitly reactivated (is_active = 1)
        cursor.execute(
            "SELECT lower(company_name) FROM dormant_companies WHERE is_active = 1"
        )
        reactivated = {r[0] for r in cursor.fetchall()}
        dormant_names -= reactivated

    return dormant_names


def get_dormant_companies_entries(db_path: Optional[Path] = None) -> list[dict[str, Any]]:
    """Retrieve full metadata for all dormant companies."""
    init_db(db_path)
    target_path = get_db_path(db_path)

    with sqlite3.connect(target_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT company_name, reason, paused_at, consecutive_zero_scans, is_active, notes
            FROM dormant_companies
            WHERE is_active = 0
            ORDER BY company_name ASC
            """
        )
        rows = [dict(r) for r in cursor.fetchall()]

    return rows


def reactivate_company(company_name: str, db_path: Optional[Path] = None) -> bool:
    """Reactivate a dormant company, resetting zero counts and marking active."""
    if not company_name or not company_name.strip():
        return False

    init_db(db_path)
    target_path = get_db_path(db_path)
    comp_norm = company_name.strip()

    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO dormant_companies (company_name, reason, paused_at, consecutive_zero_scans, is_active, notes)
            VALUES (?, 'Reactivated by user', CURRENT_TIMESTAMP, 0, 1, 'Manually reactivated')
            ON CONFLICT(company_name) DO UPDATE SET
                is_active = 1,
                consecutive_zero_scans = 0,
                notes = 'Manually reactivated'
            """,
            (comp_norm,),
        )
        conn.commit()
        return True


def get_applied_jobs(db_path: Optional[Path] = None) -> list[dict[str, Any]]:
    """Retrieve jobs with status 'APPLIED' for email revert correlation.
    Returns a list of dicts containing numeric_id, id, company, apply_url, status, notes, direct_search_url.
    """
    init_db(db_path)
    target_path = get_db_path(db_path)
    with sqlite3.connect(target_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT rowid AS numeric_id, id, company, apply_url, status, notes, direct_search_url
            FROM seen_jobs
            WHERE UPPER(status) = 'APPLIED'
            """
        )
        rows = cursor.fetchall()
    return [dict(row) for row in rows]


def record_apply_prep_attempt(
    job_id: Union[str, int],
    company: str,
    title: str,
    provider: str,
    status: str,
    details: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> int:
    """Log an apply preparation attempt in the audit table."""
    target_path = get_db_path(db_path)
    init_db(target_path)
    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO apply_prep_attempts (job_id, company, title, provider, status, details)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (str(job_id), company, title, provider, status, details),
        )
        conn.commit()
        return cursor.lastrowid or 0


def has_prepped_application(
    job_id: Union[str, int],
    db_path: Optional[Path] = None,
) -> bool:
    """Check if an application preparation has already been executed for a job."""
    target_path = get_db_path(db_path)
    if not target_path.exists():
        return False
    with sqlite3.connect(target_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM apply_prep_attempts WHERE job_id = ? AND status IN ('SUCCESS', 'FALLBACK') LIMIT 1",
            (str(job_id),),
        )
        return cursor.fetchone() is not None

