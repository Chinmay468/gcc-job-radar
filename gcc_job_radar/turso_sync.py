"""Turso Cloud Database Synchronization for GCC Job Radar.

Enables bidirectional synchronization between local SQLite (gcc_jobs.db) and
Turso (cloud-hosted libSQL/SQLite), ensuring zero data loss and preventing
duplicate alerts across GitHub Actions runners and local development.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    import dotenv
    dotenv.load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)

from gcc_job_radar.schema import (
    CORE_SCHEMA_STATEMENTS as TURSO_SCHEMA_STATEMENTS,
    MANAGED_TABLES,
)



def normalize_turso_url(url: str) -> str:
    """Normalize Turso connection URL to HTTPS scheme for Hrana HTTP transport."""
    if not url:
        return ""
    clean = url.strip()
    if clean.startswith("libsql://"):
        clean = "https://" + clean[len("libsql://"):]
    elif clean.startswith("http://"):
        clean = "https://" + clean[len("http://"):]
    return clean


def get_turso_credentials() -> Tuple[Optional[str], Optional[str]]:
    """Retrieve TURSO_DATABASE_URL and TURSO_AUTH_TOKEN from environment."""
    url = os.getenv("TURSO_DATABASE_URL", "").strip() or None
    token = os.getenv("TURSO_AUTH_TOKEN", "").strip() or None
    if url:
        url = normalize_turso_url(url)
    return url, token


def is_turso_configured() -> bool:
    """Return True if Turso database credentials are fully configured.

    Automatically suppresses live cloud sync during automated test execution (pytest)
    unless explicitly forced via TURSO_FORCE_SYNC=1.
    """
    if os.getenv("PYTEST_CURRENT_TEST") and os.getenv("TURSO_FORCE_SYNC") != "1":
        return False
    url, token = get_turso_credentials()
    return bool(url and token)


def get_turso_client(
    url: Optional[str] = None, token: Optional[str] = None
) -> Optional[Any]:
    """Create a synchronous Turso libSQL client if credentials are provided."""
    if url is None or token is None:
        cfg_url, cfg_token = get_turso_credentials()
        url = url or cfg_url
        token = token or cfg_token

    if not url or not token:
        return None

    try:
        import libsql_client
        return libsql_client.create_client_sync(url=url, auth_token=token)
    except Exception as e:
        logger.warning(f"Failed to initialize Turso client: {e}")
        return None


def init_turso_schema(client: Any) -> None:
    """Ensure all required tables, indexes, and views exist on Turso."""
    try:
        client.batch(TURSO_SCHEMA_STATEMENTS)
        logger.info("Turso cloud database schema verified.")
    except Exception as e:
        logger.error(f"Failed to initialize schema on Turso: {e}")
        raise


def push_to_turso(
    db_path: Optional[Union[str, Path]] = None,
    client: Optional[Any] = None,
    batch_size: int = 100,
) -> Dict[str, int]:
    """Push local SQLite records to Turso cloud database using batch statements.

    Uses INSERT OR REPLACE to ensure idempotency and atomic chunking.
    """
    from gcc_job_radar.db import get_db_path, init_db

    resolved_path = get_db_path(db_path)
    init_db(resolved_path)

    own_client = False
    if client is None:
        client = get_turso_client()
        if client is None:
            raise ValueError("Turso credentials not configured.")
        own_client = True

    import libsql_client

    results: Dict[str, int] = {}
    conn = sqlite3.connect(resolved_path)
    try:
        init_turso_schema(client)
        cur = conn.cursor()

        for table in MANAGED_TABLES:
            cur.execute(f"PRAGMA table_info({table})")
            cols = [r[1] for r in cur.fetchall()]
            if not cols:
                continue

            cur.execute(f"SELECT * FROM {table}")
            rows = cur.fetchall()
            results[table] = len(rows)
            if not rows:
                continue

            placeholders = ", ".join(["?"] * len(cols))
            col_names = ", ".join(cols)
            insert_sql = f"INSERT OR REPLACE INTO {table} ({col_names}) VALUES ({placeholders})"

            for i in range(0, len(rows), batch_size):
                chunk = rows[i : i + batch_size]
                stmts = [
                    libsql_client.Statement(insert_sql, list(row))
                    for row in chunk
                ]
                client.batch(stmts)

            logger.info(f"Pushed {len(rows)} rows from table '{table}' to Turso.")
    finally:
        conn.close()
        if own_client and hasattr(client, "close"):
            try:
                client.close()
            except Exception:
                pass

    return results


def pull_from_turso(
    db_path: Optional[Union[str, Path]] = None,
    client: Optional[Any] = None,
) -> Dict[str, int]:
    """Pull tables from Turso cloud database and sync into local SQLite.

    Uses INSERT OR REPLACE to ensure local consistency without wiping existing state.
    """
    from gcc_job_radar.db import get_db_path, init_db

    resolved_path = get_db_path(db_path)
    init_db(resolved_path)

    own_client = False
    if client is None:
        client = get_turso_client()
        if client is None:
            raise ValueError("Turso credentials not configured.")
        own_client = True

    results: Dict[str, int] = {}
    conn = sqlite3.connect(resolved_path)
    try:
        cur = conn.cursor()
        for table in MANAGED_TABLES:
            try:
                res = client.execute(f"SELECT * FROM {table}")
            except Exception as e:
                logger.warning(f"Could not read table '{table}' from Turso: {e}")
                results[table] = 0
                continue

            cols = res.columns
            rows = res.rows
            results[table] = len(rows)
            if not rows:
                continue

            placeholders = ", ".join(["?"] * len(cols))
            col_names = ", ".join(cols)
            insert_sql = f"INSERT OR REPLACE INTO {table} ({col_names}) VALUES ({placeholders})"

            cur.executemany(insert_sql, [list(r) for r in rows])
            conn.commit()
            logger.info(f"Pulled {len(rows)} rows into local table '{table}' from Turso.")
    finally:
        conn.close()
        if own_client and hasattr(client, "close"):
            try:
                client.close()
            except Exception:
                pass

    return results


def get_sync_status(
    db_path: Optional[Union[str, Path]] = None,
    client: Optional[Any] = None,
) -> Dict[str, Any]:
    """Compare row counts across local SQLite and Turso cloud database."""
    from gcc_job_radar.db import get_db_path, init_db

    resolved_path = get_db_path(db_path)
    init_db(resolved_path)

    configured = is_turso_configured()
    if not configured:
        return {
            "configured": False,
            "message": "Turso credentials (TURSO_DATABASE_URL / TURSO_AUTH_TOKEN) are not set.",
        }

    own_client = False
    if client is None:
        client = get_turso_client()
        own_client = True

    status_data: Dict[str, Dict[str, int]] = {}
    conn = sqlite3.connect(resolved_path)
    try:
        cur = conn.cursor()
        for table in MANAGED_TABLES:
            cur.execute(f"SELECT count(*) FROM {table}")
            local_count = cur.fetchone()[0]

            try:
                res = client.execute(f"SELECT count(*) FROM {table}")
                turso_count = res.rows[0][0]
            except Exception:
                turso_count = -1

            status_data[table] = {
                "local": local_count,
                "turso": turso_count,
            }
    finally:
        conn.close()
        if own_client and hasattr(client, "close"):
            try:
                client.close()
            except Exception:
                pass

    url, _ = get_turso_credentials()
    return {
        "configured": True,
        "database_url": url,
        "tables": status_data,
    }


def sync_turso(
    db_path: Optional[Union[str, Path]] = None,
    direction: str = "auto",
) -> Dict[str, Any]:
    """Perform database synchronization with Turso based on specified direction.

    Directions:
      - 'pull': download Turso data into local SQLite
      - 'push': upload local SQLite data into Turso
      - 'auto': pull before operations; returns pull stats
    """
    if not is_turso_configured():
        return {
            "status": "skipped",
            "reason": "Turso credentials not configured (running in local-only mode)",
        }

    try:
        if direction == "push":
            stats = push_to_turso(db_path)
            return {"status": "success", "direction": "push", "stats": stats}
        elif direction in ("pull", "auto"):
            stats = pull_from_turso(db_path)
            return {"status": "success", "direction": "pull", "stats": stats}
        else:
            raise ValueError(f"Unknown sync direction: {direction}")
    except Exception as e:
        logger.error(f"Turso sync failed ({direction}): {e}")
        return {"status": "error", "direction": direction, "error": str(e)}


def main() -> None:
    """CLI runner for direct execution (e.g. via `python -m gcc_job_radar.turso_sync`)."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="GCC Job Radar — Turso Cloud Sync")
    parser.add_argument("--pull", action="store_true", help="Pull data from Turso into local SQLite")
    parser.add_argument("--push", action="store_true", help="Push data from local SQLite into Turso")
    parser.add_argument("--status", action="store_true", help="Show table row counts in local vs Turso")
    parser.add_argument("--db", type=str, default=None, help="Custom local SQLite database path")

    args = parser.parse_args()

    if args.status:
        st = get_sync_status(args.db)
        if not st.get("configured"):
            print("[!] Turso is not configured. Set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN.")
            sys.exit(1)
        print(f"[Turso] Database: {st.get('database_url')}")
        print("-" * 55)
        print(f"{'Table':<25} {'Local Count':<15} {'Turso Count':<15}")
        print("-" * 55)
        for tbl, counts in st.get("tables", {}).items():
            print(f"{tbl:<25} {counts['local']:<15} {counts['turso']:<15}")
        print("-" * 55)
        return

    if args.pull:
        print("[*] Pulling data from Turso to local database...")
        res = pull_from_turso(args.db)
        print("[+] Pull complete:")
        for t, c in res.items():
            print(f"  - {t}: {c} rows")
        return

    if args.push:
        print("[*] Pushing local data to Turso cloud database...")
        res = push_to_turso(args.db)
        print("[+] Push complete:")
        for t, c in res.items():
            print(f"  - {t}: {c} rows")
        return

    # Default action if no flag passed
    parser.print_help()


if __name__ == "__main__":
    main()
