"""Turso Cloud Database Synchronization for GCC Job Radar.

Enables bidirectional synchronization between local SQLite (gcc_jobs.db) and
Turso (cloud-hosted libSQL/SQLite), ensuring zero data loss and preventing
duplicate alerts across GitHub Actions runners and local development.
Uses delta-only push synchronization to minimize Turso row-write operations.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple, Union

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


def _remote_schema_ready(client: Any) -> bool:
    """Check via read-only query whether all MANAGED_TABLES already exist on Turso."""
    try:
        res = client.execute("SELECT name FROM sqlite_master WHERE type='table'")
        rows = getattr(res, "rows", None)
        if not isinstance(rows, (list, tuple)) or not rows:
            return False
        existing = {str(r[0]) for r in rows if r}
        return all(t in existing for t in MANAGED_TABLES)
    except Exception:
        return False


def prune_local_sync_bloat(conn: sqlite3.Connection) -> int:
    """Prune orphaned dispatched_alerts and stale seen_emails locally before syncing."""
    cur = conn.cursor()
    pruned = 0
    try:
        cur.execute(
            """DELETE FROM dispatched_alerts
               WHERE job_id NOT IN (SELECT id FROM seen_jobs)
                 AND sent_at < datetime('now', '-7 days')"""
        )
        pruned += max(0, cur.rowcount or 0)
        cur.execute(
            """DELETE FROM seen_emails
               WHERE processed_at < datetime('now', '-60 days')"""
        )
        pruned += max(0, cur.rowcount or 0)
        if pruned > 0:
            conn.commit()
            logger.info(f"Pruned {pruned} stale/orphaned sync records from local DB.")
    except Exception as e:
        logger.debug(f"Local sync bloat pruning skipped: {e}")
    return pruned


def _filter_delta_rows(
    table: str,
    cols: List[str],
    local_rows: List[Tuple[Any, ...]],
    client: Any,
) -> List[Tuple[Any, ...]]:
    """Return only local rows that are new or meaningfully modified compared to Turso."""
    if not local_rows:
        return []

    try:
        res = client.execute(f"SELECT * FROM {table}")
        remote_cols = list(getattr(res, "columns", []) or [])
        remote_rows = getattr(res, "rows", None)
        if not isinstance(remote_rows, (list, tuple)) or not remote_cols:
            return local_rows
    except Exception:
        return local_rows

    if not remote_rows:
        return local_rows

    col_idx = {c: i for i, c in enumerate(cols)}
    rem_idx = {c: i for i, c in enumerate(remote_cols)}

    # Append-only tables: only push primary keys missing from Turso
    if table == "dispatched_alerts" and "job_id" in col_idx and "platform" in col_idx:
        ji, pi = col_idx["job_id"], col_idx["platform"]
        r_ji, r_pi = rem_idx.get("job_id", 0), rem_idx.get("platform", 1)
        remote_keys: Set[Tuple[Any, Any]] = {(r[r_ji], r[r_pi]) for r in remote_rows}
        return [row for row in local_rows if (row[ji], row[pi]) not in remote_keys]

    if table == "seen_emails" and "uid" in col_idx:
        ui = col_idx["uid"]
        r_ui = rem_idx.get("uid", 0)
        remote_uids: Set[Any] = {r[r_ui] for r in remote_rows}
        return [row for row in local_rows if row[ui] not in remote_uids]

    # Mutable tables: ignore volatile heartbeat columns that tick on every scan
    volatile_cols = {
        "seen_jobs": {"last_seen_at", "first_seen_at"},
        "dormant_companies": {"consecutive_zero_scans", "paused_at"},
    }.get(table, set())

    pk_col = {
        "seen_jobs": "id",
        "dormant_companies": "company_name",
        "apply_prep_attempts": "id",
    }.get(table, cols[0])

    if pk_col not in col_idx or pk_col not in rem_idx:
        remote_set = {tuple(r) for r in remote_rows}
        return [row for row in local_rows if tuple(row) not in remote_set]

    compare_cols = [c for c in cols if c not in volatile_cols and c in rem_idx]
    l_pk = col_idx[pk_col]
    r_pk = rem_idx[pk_col]

    remote_sig_by_pk: Dict[Any, Tuple[Any, ...]] = {}
    for r in remote_rows:
        sig = tuple(r[rem_idx[c]] for c in compare_cols)
        remote_sig_by_pk[r[r_pk]] = sig

    delta: List[Tuple[Any, ...]] = []
    for row in local_rows:
        pk_val = row[l_pk]
        local_sig = tuple(row[col_idx[c]] for c in compare_cols)
        if remote_sig_by_pk.get(pk_val) != local_sig:
            delta.append(row)
    return delta


def push_to_turso(
    db_path: Optional[Union[str, Path]] = None,
    client: Optional[Any] = None,
    batch_size: int = 100,
    delta_only: bool = True,
) -> Dict[str, int]:
    """Push local SQLite records to Turso cloud database using batch statements.

    When delta_only=True (default), compares local rows against remote Turso state
    and only writes rows that are new or meaningfully updated, saving >99% of row writes.
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
        prune_local_sync_bloat(conn)
        if not _remote_schema_ready(client):
            init_turso_schema(client)

        cur = conn.cursor()

        for table in MANAGED_TABLES:
            cur.execute(f"PRAGMA table_info({table})")
            cols = [r[1] for r in cur.fetchall()]
            if not cols:
                continue

            cur.execute(f"SELECT * FROM {table}")
            rows = cur.fetchall()
            if not rows:
                results[table] = 0
                continue

            rows_to_push = (
                _filter_delta_rows(table, cols, rows, client)
                if delta_only
                else rows
            )
            results[table] = len(rows_to_push)
            if not rows_to_push:
                continue

            placeholders = ", ".join(["?"] * len(cols))
            col_names = ", ".join(cols)
            verb = (
                "INSERT OR IGNORE"
                if table in ("dispatched_alerts", "seen_emails")
                else "INSERT OR REPLACE"
            )
            insert_sql = f"{verb} INTO {table} ({col_names}) VALUES ({placeholders})"

            for i in range(0, len(rows_to_push), batch_size):
                chunk = rows_to_push[i : i + batch_size]
                stmts = [
                    libsql_client.Statement(insert_sql, list(row))
                    for row in chunk
                ]
                client.batch(stmts)

            logger.info(
                f"Pushed {len(rows_to_push)} delta row(s) (of {len(rows)} local) from table '{table}' to Turso."
            )
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
