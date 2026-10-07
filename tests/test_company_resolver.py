"""Tests for company ATS resolver and Radar DB status tracking."""

import io
import json
from pathlib import Path
import sqlite3
import pytest

from gcc_job_radar.db import init_db
from tools.company_resolver import (
    lookup_in_radar_registry,
    get_canonical_ats_url,
    get_radar_db_status,
    resolve_company,
)
from tools.extension_bridge import BridgeHTTPRequestHandler


class DummyBridgeHandler(BridgeHTTPRequestHandler):
    """Subclass of BridgeHTTPRequestHandler bypassing socket initialization for isolated unit testing."""

    def __init__(self, method="GET", path="/lookup_company", body=None):
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


def test_lookup_in_radar_registry_monitored_companies():
    """Verify registry lookup finds monitored companies across suffixes, geo tokens, and abbreviations."""
    stripe = lookup_in_radar_registry("Stripe")
    assert stripe is not None
    assert stripe.name.lower() == "stripe"

    databricks_geo = lookup_in_radar_registry("Databricks India")
    assert databricks_geo is not None
    assert "databricks" in databricks_geo.name.lower()

    amazon_suffix = lookup_in_radar_registry("Amazon Inc.")
    assert amazon_suffix is not None
    assert amazon_suffix.name.lower() == "amazon"

    ti_abbrev = lookup_in_radar_registry("TI")
    assert ti_abbrev is not None
    assert "texas instruments" in ti_abbrev.name.lower()

    unknown = lookup_in_radar_registry("Unknown Stealth Startup 12345")
    assert unknown is None


def test_get_canonical_ats_url():
    """Verify canonical direct ATS URLs are generated accurately per provider."""
    stripe_cfg = lookup_in_radar_registry("Stripe")
    assert stripe_cfg is not None
    url, label = get_canonical_ats_url(stripe_cfg)
    assert "boards.greenhouse.io" in url
    assert "Greenhouse" in label

    amazon_cfg = lookup_in_radar_registry("Amazon")
    assert amazon_cfg is not None
    url, label = get_canonical_ats_url(amazon_cfg)
    assert "amazon.jobs" in url
    assert "Amazon" in label


def test_get_radar_db_status_applied(tmp_path: Path):
    """Verify get_radar_db_status detects previously applied jobs in seen_jobs."""
    test_db = tmp_path / "test_seen.db"
    init_db(test_db)

    with sqlite3.connect(test_db) as conn:
        conn.execute(
            """
            INSERT INTO seen_jobs (id, company, title, location, apply_url, provider, status, applied_at, is_active)
            VALUES ('job-1', 'Stripe', 'Software Engineer', 'Bengaluru', 'https://example.com/apply', 'greenhouse', 'APPLIED', '2026-10-02T10:00:00', 1)
            """
        )
        conn.commit()

    status = get_radar_db_status("Stripe", db_path=test_db)
    assert status["applied"] is True
    assert status["job_status"] == "APPLIED"
    assert "Oct 02, 2026" in status["applied_date"] or "2026-10-02" in status["applied_date"]
    assert status["applied_role"] == "Software Engineer"


def test_resolve_company_3rd_party_aggregator_and_badges(tmp_path: Path):
    """Verify resolve_company flags 3rd party aggregators and generates appropriate badges."""
    test_db = tmp_path / "test_seen.db"
    init_db(test_db)

    # 1. Monitored GCC on LinkedIn
    res_li = resolve_company(
        "Stripe",
        current_url="https://www.linkedin.com/jobs/view/1234567890",
        db_path=test_db,
    )
    assert res_li["found"] is True
    assert res_li["monitored"] is True
    assert res_li["is_3rd_party_aggregator"] is True
    assert "Greenhouse" in res_li["badge_label"]
    assert "boards.greenhouse.io" in res_li["direct_ats_url"]

    # 2. Unmonitored company on Indeed
    res_unknown = resolve_company(
        "Random Tiny Shop",
        current_url="https://www.indeed.com/viewjob?jk=abcdef",
        db_path=test_db,
    )
    assert res_unknown["found"] is True
    assert res_unknown["monitored"] is False
    assert res_unknown["is_3rd_party_aggregator"] is True
    assert "Unmonitored" in res_unknown["badge_label"]
    assert "google.com/search" in res_unknown["direct_ats_url"]


def test_bridge_handle_lookup_company_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify /lookup_company HTTP endpoint on BridgeHTTPRequestHandler."""
    test_db = tmp_path / "test_bridge.db"
    init_db(test_db)
    monkeypatch.setattr("tools.extension_bridge.get_db_path", lambda *args, **kwargs: test_db)

    handler = DummyBridgeHandler(method="GET", path="/lookup_company")
    handler.handle_lookup_company("Stripe", title="Software Engineer", url="https://www.linkedin.com/jobs/view/123")

    assert handler.status_code == 200
    res = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert res["found"] is True
    assert res["monitored"] is True
    assert res["company_name"] == "Stripe"
    assert "Greenhouse" in res["badge_label"]
    assert res["is_3rd_party_aggregator"] is True
