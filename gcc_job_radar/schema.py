"""Canonical database schema definitions and DDL statements for GCC Job Radar.

Single source of truth for both local SQLite (gcc_jobs.db) and Turso cloud database.
"""

from __future__ import annotations

from typing import List

MANAGED_TABLES: List[str] = [
    "seen_jobs",
    "dispatched_alerts",
    "seen_emails",
    "dormant_companies",
    "apply_prep_attempts",
]

BASE_TABLE_STATEMENTS: List[str] = [
    """CREATE TABLE IF NOT EXISTS seen_jobs (
        id TEXT PRIMARY KEY,
        company TEXT NOT NULL,
        title TEXT NOT NULL,
        location TEXT NOT NULL,
        apply_url TEXT NOT NULL,
        provider TEXT NOT NULL,
        published_date TEXT,
        application_start_date TEXT,
        application_end_date TEXT,
        is_active INTEGER DEFAULT 1,
        is_remote INTEGER DEFAULT 0,
        status TEXT DEFAULT 'NEW',
        applied_at TIMESTAMP NULL,
        notes TEXT NULL,
        direct_search_url TEXT NULL,
        relevance_score INTEGER DEFAULT 0,
        first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS dispatched_alerts (
        job_id TEXT NOT NULL,
        platform TEXT NOT NULL,
        sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (job_id, platform)
    )""",
    """CREATE TABLE IF NOT EXISTS seen_emails (
        uid TEXT PRIMARY KEY,
        processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS dormant_companies (
        company_name TEXT PRIMARY KEY,
        reason TEXT,
        paused_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        consecutive_zero_scans INTEGER DEFAULT 0,
        is_active INTEGER DEFAULT 0,
        notes TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS apply_prep_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT NOT NULL,
        company TEXT NOT NULL,
        title TEXT NOT NULL,
        provider TEXT NOT NULL,
        status TEXT NOT NULL,
        details TEXT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
]

INDEX_AND_VIEW_STATEMENTS: List[str] = [
    "CREATE INDEX IF NOT EXISTS idx_dispatched_alerts_platform ON dispatched_alerts(platform)",
    "CREATE INDEX IF NOT EXISTS idx_seen_jobs_company ON seen_jobs(company)",
    "CREATE INDEX IF NOT EXISTS idx_seen_jobs_status ON seen_jobs(status)",
    "CREATE INDEX IF NOT EXISTS idx_seen_emails_uid ON seen_emails(uid)",
    "CREATE INDEX IF NOT EXISTS idx_apply_prep_job_id ON apply_prep_attempts(job_id)",
    "CREATE INDEX IF NOT EXISTS idx_seen_jobs_end_date ON seen_jobs(application_end_date)",
    "CREATE VIEW IF NOT EXISTS jobs AS SELECT rowid AS numeric_id, * FROM seen_jobs",
]

UNIQUE_INDEX_STATEMENTS: List[str] = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_seen_jobs_company_title_loc ON seen_jobs(lower(company), lower(title), lower(location))",
]

CORE_SCHEMA_STATEMENTS: List[str] = (
    BASE_TABLE_STATEMENTS + INDEX_AND_VIEW_STATEMENTS + UNIQUE_INDEX_STATEMENTS
)
