"""Tests for outreach generator and extension bridge /generate_outreach endpoint."""

import io
import json
import pytest

from tools.outreach_generator import (
    extract_key_stack,
    generate_connection_note,
    generate_cold_email,
    generate_referral_request,
    generate_all_outreach,
)
from tools.extension_bridge import BridgeHTTPRequestHandler


class DummyBridgeHandler(BridgeHTTPRequestHandler):
    """Subclass of BridgeHTTPRequestHandler bypassing socket initialization for isolated unit testing."""

    def __init__(self, method="POST", path="/generate_outreach", body=None):
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


def test_extract_key_stack_from_jd():
    """Verify key target stack identification."""
    stack1 = extract_key_stack("Need Java, Spring Boot, and Kafka experience.")
    assert "Java" in stack1
    assert "Spring Boot" in stack1

    stack2 = extract_key_stack("Frontend role with React and Node.js")
    assert "React.js" in stack2
    assert "Node.js" in stack2


def test_generate_connection_note_character_limit():
    """Verify LinkedIn Connection Note strictly obeys the 300 character limit."""
    note_recruiter = generate_connection_note(
        company="Stripe",
        role="Backend Software Engineer (Platform Infrastructure)",
        stack=["Java", "Spring Boot", "Kafka"],
        recipient_type="recruiter",
    )
    assert len(note_recruiter) <= 300
    assert "Stripe" in note_recruiter

    note_hm = generate_connection_note(
        company="Databricks",
        role="Distributed Systems Engineer",
        stack=["Java", "Kafka"],
        recipient_type="hiring_manager",
    )
    assert len(note_hm) <= 300
    assert "Databricks" in note_hm

    note_peer = generate_connection_note(
        company="Amazon",
        role="Software Development Engineer I",
        stack=["Java", "Spring Boot"],
        recipient_type="peer_engineer",
    )
    assert len(note_peer) <= 300


def test_generate_cold_email_content():
    """Verify cold email generates relevant subject line and project metrics."""
    res = generate_cold_email(
        company="Razorpay",
        role="Backend Engineer",
        stack=["Java", "Spring Boot", "Kafka"],
        recipient_type="hiring_manager",
    )
    assert "Subject:" in f"Subject: {res['subject']}"
    assert "Razorpay" in res["body"]
    assert "Chinmay Maheshwari" in res["body"]
    assert "Distributed Microservices" in res["body"] or "Skill-Bridge" in res["body"]


def test_generate_all_outreach():
    """Verify full outreach bundle generation."""
    bundle = generate_all_outreach(
        company="Mastercard",
        role="Software Engineer",
        jd_text="Build payment APIs with Java and Spring Boot.",
        detected_skills=["Java", "Spring Boot"],
        recipient_type="recruiter",
    )
    assert bundle["success"] is True
    assert bundle["connection_note_char_count"] <= 300
    assert "cold_email_subject" in bundle
    assert "cold_email_body" in bundle
    assert "referral_note" in bundle


def test_bridge_handle_generate_outreach_endpoint():
    """Verify /generate_outreach HTTP handler on BridgeHTTPRequestHandler."""
    handler = DummyBridgeHandler(
        method="POST",
        path="/generate_outreach",
        body={
            "company": "Stripe",
            "title": "Backend Engineer",
            "jd_text": "Java, Spring Boot, Kafka, Docker",
            "matched_skills": ["Java", "Spring Boot"],
            "recipient_type": "recruiter",
        },
    )
    handler.handle_generate_outreach()

    assert handler.status_code == 200
    res = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert res["success"] is True
    assert res["connection_note_char_count"] <= 300
    assert "Stripe" in res["connection_note"]
    assert "Stripe" in res["cold_email_body"]
