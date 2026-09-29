import io
import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from tools.extension_bridge import BridgeHTTPRequestHandler
from gcc_job_radar.db import init_db


class DummyBridgeHandler(BridgeHTTPRequestHandler):
    """Subclass of BridgeHTTPRequestHandler bypassing socket initialization for isolated unit testing."""

    def __init__(self, method="POST", path="/dismiss", body=None):
        self.command = method
        self.path = path
        self.headers = {}
        body_bytes = json.dumps(body or {}).encode("utf-8")
        self.rfile = io.BytesIO(body_bytes)
        self.headers["Content-Length"] = str(len(body_bytes))
        self.wfile = io.BytesIO()
        self.status_code = None
        self.sent_headers = {}

    def send_response(self, code, message=None):
        self.status_code = code

    def send_header(self, keyword, value):
        self.sent_headers[keyword] = value

    def end_headers(self):
        pass


def test_handle_dismiss_missing_company(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    test_db = tmp_path / "test_jobs.db"
    init_db(test_db)
    monkeypatch.setattr("tools.extension_bridge.get_db_path", lambda *args, **kwargs: test_db)

    handler = DummyBridgeHandler(method="POST", path="/dismiss", body={
        "company": "",
        "title": "Software Engineer"
    })
    handler.handle_dismiss()

    assert handler.status_code == 400
    res = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert "error" in res
    assert "Company name is required" in res["error"]


def test_handle_dismiss_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    test_db = tmp_path / "test_jobs.db"
    init_db(test_db)
    monkeypatch.setattr("tools.extension_bridge.get_db_path", lambda *args, **kwargs: test_db)

    # Mock dismissals registry save so it doesn't touch disk in testing
    test_registry = tmp_path / "dismissals.json"
    monkeypatch.setattr("gcc_job_radar.db.DISMISSALS_REGISTRY_PATH", test_registry)

    handler = DummyBridgeHandler(method="POST", path="/dismiss", body={
        "company": "SpamCorp",
        "title": "Unwanted Dev",
        "url": "https://spamcorp.com/careers/123",
        "reason": "Not relevant stack",
        "score": 15
    })
    handler.handle_dismiss()

    assert handler.status_code == 200
    res = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert res.get("success") is True
    assert res.get("status") == "DISMISSED"
    assert res.get("company") == "SpamCorp"

    # Verify database was updated
    with sqlite3.connect(test_db) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM seen_jobs WHERE lower(company) = 'spamcorp'")
        rows = cur.fetchall()
        assert len(rows) > 0
        for r in rows:
            assert r["status"] == "DISMISSED"
            assert r["is_active"] == 0
