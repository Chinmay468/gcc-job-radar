"""Tests for autofill engine, profile store, and ATS screening question assistant."""

import io
import json
import pytest

from tools.autofill_engine import (
    get_candidate_profile,
    classify_question_intent,
    generate_heuristic_screening_answer,
    answer_screening_question,
)
from tools.extension_bridge import BridgeHTTPRequestHandler


class DummyBridgeHandler(BridgeHTTPRequestHandler):
    """Subclass of BridgeHTTPRequestHandler bypassing socket initialization for isolated unit testing."""

    def __init__(self, method="GET", path="/candidate_profile", body=None):
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


def test_candidate_profile_defaults():
    profile = get_candidate_profile()
    assert profile["first_name"] == "Chinmay"
    assert profile["last_name"] == "Maheshwari"
    assert "chinmaymaheshwari.it27@gmail.com" in profile["email"]
    assert "9460449962" in profile["phone"]
    assert "linkedin.com" in profile["linkedin"]
    assert "github.com" in profile["github"]
    assert "Java" in profile["core_stack"]


def test_classify_question_intent():
    assert classify_question_intent("Why do you want to join Google?") == "why_company"
    assert classify_question_intent("What is your current notice period in days?") == "notice_period"
    assert classify_question_intent("Are you legally authorized to work in India?") == "work_auth"
    assert classify_question_intent("Are you willing to relocate to Bangalore?") == "relocation"
    assert classify_question_intent("What is your expected salary / CTC?") == "salary"
    assert classify_question_intent("Describe your experience with Spring Boot & Kafka") == "experience"
    assert classify_question_intent("What is your greatest technical accomplishment?") == "strengths"


def test_generate_heuristic_answers():
    ans_why = generate_heuristic_screening_answer(
        question="Why work at Datadog?",
        company="Datadog",
        role="Backend Engineer",
    )
    assert "Datadog" in ans_why
    assert "Java" in ans_why or "distributed" in ans_why

    ans_notice = generate_heuristic_screening_answer(
        question="Notice period?",
        intent="notice_period",
    )
    assert "immediate" in ans_notice.lower() or "0 days" in ans_notice.lower()

    ans_auth = generate_heuristic_screening_answer(
        question="Visa sponsorship?",
        intent="work_auth",
    )
    assert "authorized" in ans_auth.lower()


def test_answer_screening_question_wrapper():
    res = answer_screening_question(
        question="When can you join?",
        company="Uber",
        role="Software Engineer",
    )
    assert res["intent"] == "notice_period"
    assert len(res["answer"]) > 10


def test_bridge_get_candidate_profile():
    handler = DummyBridgeHandler(method="GET", path="/candidate_profile")
    handler.handle_get_candidate_profile()

    assert handler.status_code == 200
    res = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert res["success"] is True
    assert res["profile"]["first_name"] == "Chinmay"


def test_bridge_answer_screening_question():
    handler = DummyBridgeHandler(
        method="POST",
        path="/answer_screening_question",
        body={
            "question": "Why do you want to work at Atlassian?",
            "company": "Atlassian",
            "role": "Grad Software Developer",
        },
    )
    handler.handle_answer_screening_question()

    assert handler.status_code == 200
    res = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert "Atlassian" in res["answer"]
    assert res["intent"] == "why_company"
