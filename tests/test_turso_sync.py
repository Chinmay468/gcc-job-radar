"""Tests for Turso Cloud Database Synchronization module."""

from pathlib import Path
import sqlite3
from unittest.mock import MagicMock, patch
import pytest

from gcc_job_radar.turso_sync import (
    get_sync_status,
    get_turso_client,
    get_turso_credentials,
    init_turso_schema,
    is_turso_configured,
    normalize_turso_url,
    pull_from_turso,
    push_to_turso,
    sync_turso,
)


def test_normalize_turso_url() -> None:
    """Verify conversion of libsql:// and http:// schemes to https://."""
    assert normalize_turso_url("libsql://my-db-user.turso.io") == "https://my-db-user.turso.io"
    assert normalize_turso_url("http://my-db-user.turso.io") == "https://my-db-user.turso.io"
    assert normalize_turso_url("https://my-db-user.turso.io") == "https://my-db-user.turso.io"
    assert normalize_turso_url("  libsql://test.turso.io  ") == "https://test.turso.io"
    assert normalize_turso_url("") == ""


def test_get_turso_credentials_and_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify detection of credentials in environment."""
    monkeypatch.setenv("TURSO_FORCE_SYNC", "1")
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://test.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "secret-token")

    url, token = get_turso_credentials()
    assert url == "https://test.turso.io"
    assert token == "secret-token"
    assert is_turso_configured() is True

    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    assert is_turso_configured() is False


def test_get_turso_client_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify get_turso_client returns None when unconfigured."""
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
    assert get_turso_client() is None


def test_init_turso_schema() -> None:
    """Verify init_turso_schema executes batch DDL on the client."""
    mock_client = MagicMock()
    init_turso_schema(mock_client)
    assert mock_client.batch.called
    statements = mock_client.batch.call_args[0][0]
    assert any("CREATE TABLE IF NOT EXISTS seen_jobs" in s for s in statements)
    assert any("CREATE TABLE IF NOT EXISTS dispatched_alerts" in s for s in statements)


def test_push_to_turso_with_mock(tmp_path: Path) -> None:
    """Verify push_to_turso reads local SQLite tables and calls client.batch."""
    db_file = tmp_path / "test_push.db"
    from gcc_job_radar.db import init_db
    init_db(db_file)

    with sqlite3.connect(db_file) as conn:
        conn.execute(
            """INSERT INTO seen_jobs (id, company, title, location, apply_url, provider)
               VALUES ('test_1', 'Acme', 'Dev', 'Bangalore', 'https://example.com', 'greenhouse')"""
        )
        conn.commit()

    mock_client = MagicMock()
    stats = push_to_turso(db_path=db_file, client=mock_client, batch_size=50)

    assert stats.get("seen_jobs") == 1
    assert mock_client.batch.called


def test_pull_from_turso_with_mock(tmp_path: Path) -> None:
    """Verify pull_from_turso writes remote data into local SQLite."""
    db_file = tmp_path / "test_pull.db"
    from gcc_job_radar.db import init_db
    init_db(db_file)

    mock_client = MagicMock()
    mock_res = MagicMock()
    mock_res.columns = ["id", "company", "title", "location", "apply_url", "provider"]
    mock_res.rows = [("pull_1", "CloudCorp", "SDE 1", "Remote", "https://cloud.com", "ashby")]

    def mock_execute(sql: str):
        if "seen_jobs" in sql:
            return mock_res
        empty_res = MagicMock()
        empty_res.columns = []
        empty_res.rows = []
        return empty_res

    mock_client.execute.side_effect = mock_execute

    stats = pull_from_turso(db_path=db_file, client=mock_client)
    assert stats.get("seen_jobs") == 1

    with sqlite3.connect(db_file) as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, company, title FROM seen_jobs WHERE id = 'pull_1'")
        row = cur.fetchone()
        assert row is not None
        assert row[1] == "CloudCorp"


def test_sync_turso_skip_when_unconfigured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify sync_turso safely returns skipped status when not configured."""
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)

    db_file = tmp_path / "test_skip.db"
    res = sync_turso(db_path=db_file, direction="pull")
    assert res.get("status") == "skipped"


def test_get_sync_status_unconfigured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify get_sync_status reports unconfigured state correctly."""
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)

    db_file = tmp_path / "test_status.db"
    st = get_sync_status(db_path=db_file)
    assert st.get("configured") is False
