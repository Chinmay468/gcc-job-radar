"""Tests for Render free tier web service entrypoint (tools/web_entrypoint.py)."""

import json
import socket
import urllib.request
import pytest
from tools.web_entrypoint import start_http_server


def get_free_port() -> int:
    """Find a free local ephemeral port for testing."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def test_server():
    """Start test HTTP server on an available localhost port and cleanly shut it down."""
    port = get_free_port()
    server = start_http_server(host="127.0.0.1", port=port)
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


def test_health_check_root(test_server):
    """Verify GET / returns HTTP 200 with expected health payload."""
    url = f"{test_server}/"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=3.0) as resp:
        assert resp.status == 200
        assert "application/json" in resp.headers.get("Content-Type", "")
        body = json.loads(resp.read().decode("utf-8"))
        assert body["status"] == "healthy"
        assert body["service"] == "gcc-job-radar"
        assert "memory_rss_mb" in body
        assert isinstance(body["memory_rss_mb"], (int, float))


def test_health_check_endpoint(test_server):
    """Verify GET /health returns HTTP 200 with expected health payload."""
    url = f"{test_server}/health"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=3.0) as resp:
        assert resp.status == 200
        assert "application/json" in resp.headers.get("Content-Type", "")
        body = json.loads(resp.read().decode("utf-8"))
        assert body["status"] == "healthy"
        assert body["service"] == "gcc-job-radar"
        assert "memory_rss_mb" in body
        assert isinstance(body["memory_rss_mb"], (int, float))


def test_not_found_endpoint(test_server):
    """Verify GET /nonexistent returns HTTP 404."""
    url = f"{test_server}/nonexistent"
    req = urllib.request.Request(url)
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req, timeout=3.0)
    assert exc_info.value.code == 404
