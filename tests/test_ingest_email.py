"""Unit tests for tools/ingest_email.py and sync-mail CLI integration."""

from email.message import EmailMessage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from gcc_job_radar.cli import app
from gcc_job_radar.db import get_latest_jobs, init_db, is_email_seen, record_seen_email_uids
from gcc_job_radar.models import ATSProvider, JobPosting
from tools.ingest_email import (
    ALERT_IMAP_FILTER,
    MissingEmailCredentialsError,
    RawParsedJob,
    check_credentials,
    clean_text_punctuation,
    extract_html_from_email_message,
    fetch_unread_alert_emails,
    filter_and_convert_jobs,
    format_imap_date,
    get_configured_email_accounts,
    parse_email_alert_html,
    parse_glassdoor_alert_html,
    parse_indeed_alert_html,
    parse_linkedin_alert_html,
    parse_naukri_alert_html,
    parse_schema_org_json_ld,
    strip_glassdoor_rating,
    strip_tracking_params,
    sync_email_alerts,
)

runner = CliRunner()


# ==============================================================================
# 1. Credentials & Configuration Tests
# ==============================================================================

def test_check_credentials_explicit():
    """Verify check_credentials prioritizes explicit arguments."""
    u, p = check_credentials(user="test@example.com", password="secret-password")
    assert u == "test@example.com"
    assert p == "secret-password"


def test_check_credentials_from_env(monkeypatch):
    """Verify check_credentials picks up environment variables."""
    monkeypatch.setenv("EMAIL_USER", "env_user@example.com")
    monkeypatch.setenv("EMAIL_PASSWORD", "env-secret-1234")
    u, p = check_credentials()
    assert u == "env_user@example.com"
    assert p == "env-secret-1234"


def test_check_credentials_missing(monkeypatch):
    """Verify check_credentials raises MissingEmailCredentialsError with helpful instructions."""
    monkeypatch.delenv("EMAIL_USER", raising=False)
    monkeypatch.delenv("EMAIL_PASSWORD", raising=False)
    with pytest.raises(MissingEmailCredentialsError) as excinfo:
        check_credentials(user=None, password=None)
    assert "Google App Password" in str(excinfo.value)
    assert "EMAIL_PASSWORD" in str(excinfo.value)


def test_get_configured_email_accounts_explicit():
    """Verify get_configured_email_accounts uses explicit args when provided."""
    accs = get_configured_email_accounts(user="my_user@gmail.com", password="pwd")
    assert accs == [("my_user@gmail.com", "pwd")]


def test_get_configured_email_accounts_multi_env_file(tmp_path: Path):
    """Verify parsing multiple EMAIL_USER / EMAIL_PASSWORD blocks from a custom .env file."""
    env_content = (
        "EMAIL_USER=user1@gmail.com\n"
        "EMAIL_PASSWORD=pass1\n\n"
        "EMAIL_USER=user2@gmail.com\n"
        "EMAIL_PASSWORD=pass2\n\n"
        "EMAIL_USER=user3@jecrc.ac.in\n"
        "EMAIL_PASSWORD=pass3\n"
    )
    env_file = tmp_path / "mock.env"
    env_file.write_text(env_content, encoding="utf-8")

    accs = get_configured_email_accounts(env_path=env_file)
    assert len(accs) == 3
    assert accs[0] == ("user1@gmail.com", "pass1")
    assert accs[1] == ("user2@gmail.com", "pass2")
    assert accs[2] == ("user3@jecrc.ac.in", "pass3")


def test_get_configured_email_accounts_indexed_env_vars(monkeypatch, tmp_path: Path):
    """Verify parsing indexed EMAIL_USER_1 / EMAIL_PASSWORD_1 environment variables."""
    empty_env = tmp_path / "empty.env"
    empty_env.write_text("", encoding="utf-8")

    monkeypatch.setenv("EMAIL_USER_1", "index1@gmail.com")
    monkeypatch.setenv("EMAIL_PASSWORD_1", "indexpass1")
    monkeypatch.setenv("EMAIL_USER_2", "index2@gmail.com")
    monkeypatch.setenv("EMAIL_PASSWORD_2", "indexpass2")

    accs = get_configured_email_accounts(env_path=empty_env)
    assert ("index1@gmail.com", "indexpass1") in accs
    assert ("index2@gmail.com", "indexpass2") in accs



# ==============================================================================
# 2. Text & URL Normalization Tests
# ==============================================================================

def test_clean_text_punctuation():
    """Verify unicode hyphens, spaces, quotes, and HTML entities are cleaned."""
    assert clean_text_punctuation("") == ""
    assert clean_text_punctuation(None) == ""
    
    # Non-breaking hyphen \u2011, en-dash \u2013, em-dash \u2014
    raw_str = "Software\u2011Engineer \u2013 Backend\u2014Developer"
    cleaned = clean_text_punctuation(raw_str)
    assert cleaned == "Software-Engineer - Backend-Developer"

    # Non-breaking space \u00a0, zero-width space \u200b
    raw_str2 = "Bangalore\u00a0\u00a0Urban\u200b"
    assert clean_text_punctuation(raw_str2) == "Bangalore Urban"

    # Smart quotes & HTML entities
    raw_str3 = "Don&#39;t miss &quot;Google&quot; &amp; &middot; Alert"
    assert clean_text_punctuation(raw_str3) == "Don't miss \"Google\" & · Alert"


def test_strip_tracking_params_linkedin():
    """Verify LinkedIn job links are reduced to canonical /jobs/view/{id}/ format."""
    url = "https://www.linkedin.com/jobs/view/4451912864/?trk=flagship-job-alert&refId=abc&midToken=xyz"
    assert strip_tracking_params(url) == "https://www.linkedin.com/jobs/view/4451912864/"

    comm_url = "https://www.linkedin.com/comm/jobs/view/9876543210?alertAction=view&ref=alert"
    assert strip_tracking_params(comm_url) == "https://www.linkedin.com/jobs/view/9876543210/"


def test_strip_tracking_params_indeed():
    """Verify Indeed job links are canonicalized with viewjob?jk={jk}."""
    url1 = "https://in.indeed.com/rc/clk?jk=1234abcd5678efgh&from=jobalerts&tk=xyz"
    assert strip_tracking_params(url1) == "https://www.indeed.com/viewjob?jk=1234abcd5678efgh"

    url2 = "https://www.indeed.com/viewjob?jk=999988887777&utm_source=email"
    assert strip_tracking_params(url2) == "https://www.indeed.com/viewjob?jk=999988887777"


def test_strip_tracking_params_naukri():
    """Verify Naukri links have all tracking query strings and fragments stripped."""
    url = "https://www.naukri.com/job-listings-software-engineer-acme-pune-0-to-2-years-12345?src=jobsearchDesk&utm_campaign=daily#apply"
    assert strip_tracking_params(url) == "https://www.naukri.com/job-listings-software-engineer-acme-pune-0-to-2-years-12345"


def test_strip_tracking_params_glassdoor():
    """Verify Glassdoor job links are canonicalized to /job-listing/?jl={id}."""
    url = "https://www.glassdoor.co.in/partner/jobListing.htm?pos=101&ao=1110586&jobListingId=1010109494169&utm_medium=email"
    assert strip_tracking_params(url) == "https://www.glassdoor.com/job-listing/?jl=1010109494169"

    url_jl = "https://www.glassdoor.com/job-listing/role-title-jl=9876543210.htm?utm_source=alert"
    assert strip_tracking_params(url_jl) == "https://www.glassdoor.com/job-listing/?jl=9876543210"


def test_strip_glassdoor_rating():
    """Verify star ratings and review suffixes are stripped from Glassdoor company names."""
    assert strip_glassdoor_rating("American Express Global Business Travel 3.9 \u2605") == "American Express Global Business Travel"
    assert strip_glassdoor_rating("Amazon 3.6 ?") == "Amazon"
    assert strip_glassdoor_rating("Capgemini 4.1") == "Capgemini"
    assert strip_glassdoor_rating("ChatFin") == "ChatFin"


def test_strip_tracking_params_generic():
    """Verify generic URLs strip UTM and tracking query parameters while preserving others."""
    url = "https://example.com/careers/job123?utm_source=alert&utm_medium=email&custom_param=val"
    clean = strip_tracking_params(url)
    assert "utm_source" not in clean
    assert "utm_medium" not in clean
    assert "custom_param=val" in clean


# ==============================================================================
# 3. HTML Parsing Tests
# ==============================================================================

def test_parse_linkedin_alert_html():
    """Verify extraction of job cards from mock LinkedIn alert HTML."""
    html_content = """
    <html>
      <body>
        <table>
          <tr>
            <td>
              <a href="https://www.linkedin.com/comm/jobs/view/4451912864?trk=eml-job_alert" class="job-title">
                Software Engineer I
              </a>
              <div>
                <span>Insurity</span> &middot; <span>Noida, Uttar Pradesh, India</span>
              </div>
            </td>
          </tr>
          <tr>
            <td>
              <a href="https://www.linkedin.com/jobs/view/4451912865" class="job-title">
                Graduate Engineer Trainee
              </a>
              <div>
                <span>Tata Elxsi</span>
                <span>Bengaluru, Karnataka, India</span>
              </div>
            </td>
          </tr>
          <!-- Noise/footer links that should be skipped -->
          <tr>
            <td>
              <a href="https://www.linkedin.com/jobs/view/4451912864">View Job</a>
              <a href="https://www.linkedin.com/jobs/view/4451912864">Apply</a>
              <a href="https://www.linkedin.com/help">Unsubscribe</a>
            </td>
          </tr>
        </table>
      </body>
    </html>
    """
    jobs = parse_linkedin_alert_html(html_content)
    assert len(jobs) == 2

    assert jobs[0].title == "Software Engineer I"
    assert jobs[0].company == "Insurity"
    assert "Noida" in jobs[0].location
    assert jobs[0].url == "https://www.linkedin.com/jobs/view/4451912864/"
    assert jobs[0].source_platform == "linkedin"

    assert jobs[1].title == "Graduate Engineer Trainee"
    assert jobs[1].company == "Tata Elxsi"
    assert "Bengaluru" in jobs[1].location
    assert jobs[1].url == "https://www.linkedin.com/jobs/view/4451912865/"


def test_parse_naukri_alert_html():
    """Verify extraction of job cards from mock Naukri alert HTML."""
    html_content = """
    <div>
      <div>
        <a href="https://www.naukri.com/job-listings-junior-qa-engineer-infosys-hyderabad-10101?src=alert">
          Junior QA Engineer
        </a>
        <div>Infosys Limited</div>
        <div>Hyderabad/Secunderabad, Telangana</div>
      </div>
      <div>
        <a href="https://www.naukri.com/job-listings-python-developer-wipro-bangalore-20202?src=alert">
          Associate Software Developer
        </a>
        <div>Wipro Technologies</div>
        <div>Bengaluru, Karnataka</div>
      </div>
    </div>
    """
    jobs = parse_naukri_alert_html(html_content)
    assert len(jobs) == 2
    assert jobs[0].title == "Junior QA Engineer"
    assert jobs[0].company == "Infosys Limited"
    assert "Hyderabad" in jobs[0].location
    assert jobs[0].source_platform == "naukri"

    assert jobs[1].title == "Associate Software Developer"
    assert jobs[1].company == "Wipro Technologies"


def test_parse_indeed_alert_html():
    """Verify extraction of job cards from mock Indeed alert HTML."""
    html_content = """
    <div>
      <div>
        <a href="https://in.indeed.com/rc/clk?jk=abc987654321&from=vj">
          Entry Level Software Engineer
        </a>
        <div>Cisco Systems</div>
        <div>Bengaluru, Karnataka</div>
      </div>
      <div>
        <a href="https://www.indeed.com/viewjob?jk=xyz123456789&from=ja">
          Software Development Engineer I
        </a>
        <div>Amazon India</div>
        <div>Hyderabad, Telangana</div>
      </div>
    </div>
    """
    jobs = parse_indeed_alert_html(html_content)
    assert len(jobs) == 2
    assert jobs[0].title == "Entry Level Software Engineer"
    assert jobs[0].company == "Cisco Systems"
    assert jobs[0].url == "https://www.indeed.com/viewjob?jk=abc987654321"
    assert jobs[0].source_platform == "indeed"

    assert jobs[1].title == "Software Development Engineer I"
    assert jobs[1].company == "Amazon India"
    assert jobs[1].url == "https://www.indeed.com/viewjob?jk=xyz123456789"


def test_parse_glassdoor_alert_html():
    """Verify extraction of job cards from mock Glassdoor alert HTML."""
    html_content = """
    <div>
      <a href="https://www.glassdoor.co.in/partner/jobListing.htm?pos=101&amp;jobListingId=1010109494169&amp;ao=111">
        <div>
          <div>American Express Global 3.9 ★</div>
          <div>Graduate Engineer Trainee</div>
          <div>Bengaluru, India</div>
        </div>
      </a>
      <a href="https://www.glassdoor.com/job-listing/role-jl=1010249204624.htm">
        <div>
          <div>Capgemini 4.1</div>
          <div>Associate Software Engineer</div>
          <div>Pune, India</div>
        </div>
      </a>
    </div>
    """
    jobs = parse_glassdoor_alert_html(html_content)
    assert len(jobs) == 2
    assert jobs[0].title == "Graduate Engineer Trainee"
    assert jobs[0].company == "American Express Global"
    assert "Bengaluru" in jobs[0].location
    assert jobs[0].url == "https://www.glassdoor.com/job-listing/?jl=1010109494169"
    assert jobs[0].source_platform == "glassdoor"

    assert jobs[1].title == "Associate Software Engineer"
    assert jobs[1].company == "Capgemini"
    assert "Pune" in jobs[1].location
    assert jobs[1].url == "https://www.glassdoor.com/job-listing/?jl=1010249204624"


def test_parse_schema_org_json_ld():
    """Verify extraction of JobPosting items from schema.org JSON-LD scripts."""
    html_content = """
    <html>
      <head>
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "JobPosting",
          "title": "Graduate Trainee Software Engineer",
          "hiringOrganization": {
            "@type": "Organization",
            "name": "Acme Global Tech"
          },
          "jobLocation": {
            "@type": "Place",
            "address": {
              "addressLocality": "Pune",
              "addressCountry": "India"
            }
          },
          "url": "https://acme.com/jobs/grad-trainee-123"
        }
        </script>
      </head>
      <body></body>
    </html>
    """
    jobs = parse_schema_org_json_ld(html_content)
    assert len(jobs) == 1
    assert jobs[0].title == "Graduate Trainee Software Engineer"
    assert jobs[0].company == "Acme Global Tech"
    assert jobs[0].location == "Pune"
    assert jobs[0].url == "https://acme.com/jobs/grad-trainee-123"
    assert jobs[0].source_platform == "schema_jsonld"


def test_parse_email_alert_html_routing():
    """Verify sender-based parser routing and fallbacks."""
    linkedin_html = """
    <a href="https://www.linkedin.com/jobs/view/11223344">Associate Engineer</a>
    <div>Microsoft · Hyderabad, India</div>
    """
    # 1. Routing to LinkedIn parser via sender header
    res1 = parse_email_alert_html(
        linkedin_html,
        sender="LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>",
    )
    assert len(res1) == 1
    assert res1[0].title == "Associate Engineer"

    # 2. Routing to Glassdoor parser via sender header
    glassdoor_html = """
    <a href="https://www.glassdoor.com/job-listing/?jl=55555">
      <div>Persistent Systems 4.0 ★</div>
      <div>Software Engineer 1</div>
      <div>Noida, India</div>
    </a>
    """
    res_gd = parse_email_alert_html(
        glassdoor_html,
        sender="Glassdoor Jobs <noreply@glassdoor.com>",
    )
    assert len(res_gd) == 1
    assert res_gd[0].title == "Software Engineer 1"
    assert res_gd[0].company == "Persistent Systems"

    # 3. Unknown sender fallback
    res2 = parse_email_alert_html(
        linkedin_html,
        sender="notifications@customalertservice.com",
    )
    assert len(res2) == 1
    assert res2[0].title == "Associate Engineer"


# ==============================================================================
# 4. Email MIME Message Extraction Tests
# ==============================================================================

def test_extract_html_singlepart():
    """Verify extraction of HTML body from singlepart EmailMessage."""
    msg = EmailMessage()
    msg.set_content("<html><body><h1>Singlepart Job Alert</h1></body></html>", subtype="html")
    extracted = extract_html_from_email_message(msg)
    assert "Singlepart Job Alert" in extracted


def test_extract_html_multipart():
    """Verify extraction of HTML body from multipart/alternative EmailMessage."""
    msg = MIMEMultipart("alternative")
    part_text = MIMEText("Plain text fallback", "plain")
    part_html = MIMEText("<html><body><p>Multipart HTML Job Alert</p></body></html>", "html")
    msg.attach(part_text)
    msg.attach(part_html)

    # Convert to EmailMessage
    raw = msg.as_bytes()
    from email import policy
    import email
    parsed_msg = email.message_from_bytes(raw, policy=policy.default)

    extracted = extract_html_from_email_message(parsed_msg)
    assert "Multipart HTML Job Alert" in extracted
    assert "Plain text fallback" not in extracted


# ==============================================================================
# 5. Filtering & DB Conversion Tests
# ==============================================================================

def test_filter_and_convert_jobs():
    """Verify that filter_and_convert_jobs adheres to fresher & India/remote criteria."""
    raw_list = [
        # 1. Valid entry-level India role
        RawParsedJob(
            title="Software Engineer I",
            company="Insurity",
            location="Noida, India",
            url="https://www.linkedin.com/jobs/view/4451912864/",
            source_platform="linkedin",
        ),
        # 2. Senior role (should be filtered out)
        RawParsedJob(
            title="Staff Principal Software Engineer",
            company="Mega Corp",
            location="Bengaluru, India",
            url="https://www.linkedin.com/jobs/view/9999999999/",
            source_platform="linkedin",
        ),
        # 3. Foreign role (should be filtered out)
        RawParsedJob(
            title="Junior Software Developer",
            company="London Tech",
            location="London, United Kingdom",
            url="https://www.linkedin.com/jobs/view/8888888888/",
            source_platform="linkedin",
        ),
        # 4. Valid remote entry-level role
        RawParsedJob(
            title="Associate Backend Developer",
            company="Distributed Inc",
            location="Remote",
            url="https://www.linkedin.com/jobs/view/7777777777/",
            source_platform="linkedin",
        ),
    ]

    qualified = filter_and_convert_jobs(raw_list, date_header="Fri, 05 Sep 2026 10:00:00 +0000")
    assert len(qualified) == 2

    titles = [q.title for q in qualified]
    assert "Software Engineer I" in titles
    assert "Associate Backend Developer" in titles
    assert "Staff Principal Software Engineer" not in titles
    assert "Junior Software Developer" not in titles

    for q in qualified:
        assert isinstance(q, JobPosting)
        assert q.provider == ATSProvider.EMAIL_ALERT
        assert q.published_date == "Fri, 05 Sep 2026 10:00:00 +0000"


def test_filter_and_convert_jobs_glassdoor_resolves_portal():
    """Verify that Glassdoor email alert jobs resolve to direct company portals."""
    raw_list = [
        RawParsedJob(
            title="Associate engineer",
            company="BT Group",
            location="Bengaluru, India",
            url="https://www.glassdoor.com/job-listing/?jl=1010251913049",
            source_platform="glassdoor",
        ),
        RawParsedJob(
            title="Junior Full Stack Developer",
            company="UnknownStartupTech",
            location="Remote",
            url="https://www.glassdoor.com/job-listing/?jl=88888",
            source_platform="glassdoor",
        ),
    ]

    qualified = filter_and_convert_jobs(raw_list)
    assert len(qualified) == 2

    # BT Group has known portal -> resolved to jobs.bt.com
    bt_job = qualified[0]
    assert "jobs.bt.com" in str(bt_job.apply_url)
    assert "BT+Group" in bt_job.direct_search_url

    # UnknownStartupTech has unblocked direct search fallback
    unknown_job = qualified[1]
    assert "UnknownStartupTech" in unknown_job.direct_search_url
    assert "careers" in unknown_job.direct_search_url


def test_filter_and_convert_jobs_deduplication():
    """Verify that multiple alert emails reporting the same job are deduplicated in-memory."""
    raw_list = [
        # First occurrence of a job
        RawParsedJob(
            title="Software Engineer I",
            company="Insurity",
            location="Noida, India",
            url="https://www.linkedin.com/jobs/view/4451912864/?trk=alert_1",
            source_platform="linkedin",
        ),
        # Second occurrence with different tracking params (same canonical URL)
        RawParsedJob(
            title="Software Engineer I",
            company="Insurity",
            location="Noida, India",
            url="https://www.linkedin.com/jobs/view/4451912864/?trk=alert_2&refId=abc",
            source_platform="linkedin",
        ),
        # Third occurrence with slightly different URL but identical role (semantic match)
        RawParsedJob(
            title="Software Engineer I",
            company="Insurity",
            location="Noida, India",
            url="https://www.linkedin.com/jobs/view/4451912864/",
            source_platform="linkedin",
        ),
    ]

    qualified = filter_and_convert_jobs(raw_list)
    assert len(qualified) == 1
    assert qualified[0].company == "Insurity"
    assert qualified[0].title == "Software Engineer I"


# ==============================================================================
# 6. IMAP Client & sync_email_alerts Integration Tests
# ==============================================================================

def test_fetch_unread_alert_emails_with_mock_client(tmp_path: Path):
    """Verify IMAP interactions: select, UID search, batch PEEK fetch, and mark_read flag handling."""
    mock_imap = MagicMock()
    mock_imap.select.return_value = ("OK", [b"10"])

    sample_email_bytes = (
        b"From: jobalerts-noreply@linkedin.com\r\n"
        b"Subject: 5 New Jobs for Software Engineer\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n\r\n"
        b"<html><body><a href=\"https://www.linkedin.com/jobs/view/12345\">Engineer Trainee</a>"
        b"<div>Infosys &middot; Pune, India</div></body></html>"
    )

    def mock_uid(cmd, *args):
        if cmd == "SEARCH":
            return ("OK", [b"1 2"])
        if cmd == "FETCH":
            return ("OK", [
                (b"1 (UID 1 BODY[] {120})", sample_email_bytes),
                b")",
                (b"2 (UID 2 BODY[] {120})", sample_email_bytes),
                b")",
            ])
        if cmd == "STORE":
            return ("OK", [b"1 2"])
        return ("OK", [])

    mock_imap.uid.side_effect = mock_uid

    db1 = tmp_path / "mock_part1.db"

    # Test with mark_read = False (should use BODY.PEEK[] and NOT call STORE)
    results = fetch_unread_alert_emails(
        imap_client=mock_imap,
        folder="INBOX",
        limit=2,
        days=7,
        mark_read=False,
        unread_only=True,
        db_path=db1,
    )

    assert len(results) == 2
    mock_imap.select.assert_called_with("INBOX", readonly=True)
    # UID SEARCH was called
    search_calls = [c for c in mock_imap.uid.call_args_list if c[0][0] == "SEARCH"]
    assert len(search_calls) > 0
    # UID FETCH was called with PEEK
    mock_imap.uid.assert_any_call("FETCH", "1,2", "(BODY.PEEK[])")
    # STORE was NOT called
    store_calls = [c for c in mock_imap.uid.call_args_list if c[0][0] == "STORE"]
    assert len(store_calls) == 0

    # Test with mark_read = True (should set \Seen flag via UID STORE)
    mock_imap.reset_mock()
    mock_imap.select.return_value = ("OK", [b"10"])

    def mock_uid_single(cmd, *args):
        if cmd == "SEARCH":
            return ("OK", [b"3"])
        if cmd == "FETCH":
            return ("OK", [(b"1 (UID 3 BODY[] {120})", sample_email_bytes), b")"])
        if cmd == "STORE":
            return ("OK", [b"3"])
        return ("OK", [])

    mock_imap.uid.side_effect = mock_uid_single

    db2 = tmp_path / "mock_part2.db"
    results_read = fetch_unread_alert_emails(
        imap_client=mock_imap,
        folder="INBOX",
        limit=1,
        days=7,
        mark_read=True,
        db_path=db2,
    )
    assert len(results_read) == 1
    mock_imap.uid.assert_any_call("STORE", "3", "+FLAGS", "(\\Seen)")


def test_sync_email_alerts_end_to_end(tmp_path: Path):
    """Verify complete sync_email_alerts pipeline persists records to SQLite and tracks UIDs."""
    db_file = tmp_path / "test_mail_sync.db"
    init_db(db_file)

    mock_imap = MagicMock()
    mock_imap.select.return_value = ("OK", [b"10"])

    sample_email_bytes = (
        b"From: jobalerts-noreply@linkedin.com\r\n"
        b"Subject: Software Engineer Roles in India\r\n"
        b"Date: Fri, 05 Sep 2026 12:00:00 +0530\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n\r\n"
        b"<html><body>"
        b"<a href=\"https://www.linkedin.com/jobs/view/8881234\">Associate Software Engineer</a>"
        b"<div>Persistent Systems &middot; Pune, Maharashtra, India</div>"
        b"</body></html>"
    )

    def mock_uid(cmd, *args):
        if cmd == "SEARCH":
            return ("OK", [b"5001"])
        if cmd == "FETCH":
            return ("OK", [(b"1 (UID 5001 BODY[] {150})", sample_email_bytes), b")"])
        if cmd == "STORE":
            return ("OK", [b"5001"])
        return ("OK", [])

    mock_imap.uid.side_effect = mock_uid

    jobs = sync_email_alerts(
        imap_client=mock_imap,
        limit=5,
        days=7,
        mark_read=False,
        db_path=db_file,
    )

    assert len(jobs) == 1
    assert jobs[0].company == "Persistent Systems"
    assert jobs[0].title == "Associate Software Engineer"
    assert jobs[0].provider == ATSProvider.EMAIL_ALERT

    # Verify SQLite database contains the recorded job
    db_jobs = get_latest_jobs(limit=10, status="ALL", db_path=db_file)
    assert len(db_jobs) == 1
    assert db_jobs[0]["company"] == "Persistent Systems"
    assert db_jobs[0]["title"] == "Associate Software Engineer"
    assert db_jobs[0]["provider"] == "email_alert"

    # Verify UID 5001 was recorded in seen_emails
    assert is_email_seen("5001", db_path=db_file) is True


def test_format_imap_date():
    """Verify format_imap_date formats correctly in dd-Mmm-yyyy standard format."""
    from datetime import datetime, timezone

    d1 = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)
    assert format_imap_date(d1) == "29-Aug-2026"

    d2 = datetime(2026, 1, 5, 0, 0, 0, tzinfo=timezone.utc)
    assert format_imap_date(d2) == "05-Jan-2026"

    d3 = datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    assert format_imap_date(d3) == "31-Dec-2026"


def test_server_side_date_windowing_criteria():
    """Verify SINCE date filter injection when days > 0 and omission when days = 0."""
    mock_imap = MagicMock()
    mock_imap.select.return_value = ("OK", [b"10"])

    searched_criteria = []

    def mock_uid(cmd, *args):
        if cmd == "SEARCH":
            searched_criteria.append(args[1] if len(args) > 1 else args[0])
            return ("OK", [b""])
        return ("OK", [])

    mock_imap.uid.side_effect = mock_uid

    # With days = 7: should contain SINCE
    fetch_unread_alert_emails(imap_client=mock_imap, days=7, unread_only=True)
    assert any("SINCE " in str(crit) for crit in searched_criteria)

    # With days = 0: should NOT contain SINCE
    searched_criteria.clear()
    fetch_unread_alert_emails(imap_client=mock_imap, days=0, unread_only=True)
    assert not any("SINCE " in str(crit) for crit in searched_criteria)


def test_seen_email_uids_skipping_in_fetch(tmp_path: Path):
    """Verify that previously seen email UIDs are skipped from fetching."""
    db_file = tmp_path / "test_seen_skip.db"
    init_db(db_file)

    # Record UID 2001 as seen
    record_seen_email_uids(["2001"], db_path=db_file)

    mock_imap = MagicMock()
    mock_imap.select.return_value = ("OK", [b"10"])

    fetched_uids = []

    def mock_uid(cmd, *args):
        if cmd == "SEARCH":
            # IMAP returns both 2001 and 2002
            return ("OK", [b"2001 2002"])
        if cmd == "FETCH":
            fetched_uids.append(args[0])
            sample_bytes = b"From: alerts@naukri.com\r\nSubject: Job\r\n\r\n"
            return ("OK", [(b"1 (UID 2002 BODY[] {40})", sample_bytes), b")"])
        return ("OK", [])

    mock_imap.uid.side_effect = mock_uid

    results = fetch_unread_alert_emails(
        imap_client=mock_imap,
        days=7,
        db_path=db_file,
    )

    # UID 2001 was already seen, so only UID 2002 was fetched
    assert fetched_uids == ["2002"]
    # UID 2002 should now also be marked as seen
    assert is_email_seen("2002", db_path=db_file) is True


# ==============================================================================
# 7. CLI Typer sync-mail Command Tests
# ==============================================================================

def test_cli_sync_mail_missing_credentials(monkeypatch):
    """Verify CLI sync-mail exits with code 1 and prints instructions when credentials missing."""
    monkeypatch.delenv("EMAIL_USER", raising=False)
    monkeypatch.delenv("EMAIL_PASSWORD", raising=False)

    result = runner.invoke(app, ["sync-mail"])
    assert result.exit_code == 1
    assert "Missing Credentials" in result.output
    assert "EMAIL_PASSWORD" in result.output


def test_cli_sync_mail_mocked_success(tmp_path: Path):
    """Verify CLI sync-mail command successfully runs and prints table when mocked."""
    db_file = tmp_path / "cli_mail_sync.db"
    mock_posting = JobPosting(
        id="email_linkedin_12345678",
        company="Mock Company India",
        title="Software Engineer I",
        location="Bengaluru, India",
        apply_url="https://www.linkedin.com/jobs/view/12345678/",
        provider=ATSProvider.EMAIL_ALERT,
        published_date="Recent",
        status="NEW",
    )

    with patch("tools.ingest_email.sync_email_alerts", return_value=[mock_posting]):
        result = runner.invoke(
            app,
            ["sync-mail", "--limit", "2", "--db", str(db_file)],
        )
        assert result.exit_code == 0
        assert "Mock Company India" in result.output
        assert "Software Engineer I" in result.output


def test_cli_sync_mail_days_option(tmp_path: Path):
    """Verify CLI sync-mail --days option forwards argument properly."""
    db_file = tmp_path / "cli_days_sync.db"

    with patch("tools.ingest_email.sync_email_alerts", return_value=[]) as mock_sync:
        result = runner.invoke(
            app,
            ["sync-mail", "--days", "14", "--db", str(db_file)],
        )
        assert result.exit_code == 0
        mock_sync.assert_called_once()
        assert mock_sync.call_args.kwargs["days"] == 14


# ==============================================================================
# 10. Snippet Pre-Filtering in filter_and_convert_jobs
# ==============================================================================

def test_filter_and_convert_jobs_rejects_snippet_with_high_yoe():
    """Verify that a job with a snippet specifying 2.6+ YOE is rejected even if title passes."""
    raw = [
        RawParsedJob(
            title="Full Stack Developer",
            company="Acme GCC India",
            location="Bengaluru, India",
            url="https://www.linkedin.com/jobs/view/99991111/",
            source_platform="linkedin",
            snippet="Experience: 2.6 – 5 Years | Immediate joiners preferred",
        )
    ]
    result = filter_and_convert_jobs(raw)
    assert result == [], "Role with 2.6–5 YOE snippet should be rejected"


def test_filter_and_convert_jobs_accepts_snippet_with_low_yoe():
    """Verify that a job with a fresher-friendly snippet is accepted for a generic title."""
    raw = [
        RawParsedJob(
            title="Full Stack Developer",
            company="Acme GCC India",
            location="Bengaluru, India",
            url="https://www.linkedin.com/jobs/view/99992222/",
            source_platform="linkedin",
            snippet="0 – 2 Years | Freshers and recent graduates welcome",
        )
    ]
    result = filter_and_convert_jobs(raw)
    assert len(result) == 1, "Fresher-friendly snippet should allow the role through"
    assert result[0].title == "Full Stack Developer"


def test_filter_and_convert_jobs_title_only_no_snippet():
    """Verify that title-only cards (no snippet) still work via title heuristics."""
    raw_pass = [
        RawParsedJob(
            title="Software Engineer I",
            company="Infosys GCC",
            location="Hyderabad, India",
            url="https://www.linkedin.com/jobs/view/88880001/",
            source_platform="linkedin",
            snippet=None,
        )
    ]
    raw_fail = [
        RawParsedJob(
            title="Senior Software Engineer",
            company="Infosys GCC",
            location="Hyderabad, India",
            url="https://www.linkedin.com/jobs/view/88880002/",
            source_platform="linkedin",
            snippet=None,
        )
    ]
    assert len(filter_and_convert_jobs(raw_pass)) == 1, "Entry-level title without snippet should pass"
    assert filter_and_convert_jobs(raw_fail) == [], "Senior title without snippet should be rejected"


def test_parse_glassdoor_alert_html_extracts_snippet():
    """Verify parse_glassdoor_alert_html populates the snippet field from lines beyond location."""
    html = (
        '<a href="https://www.glassdoor.com/partner/jobListing.htm?jobListingId=1234567890">'
        "Acme Corp 4.2★"
        "<br>Software Engineer"
        "<br>Bengaluru, India"
        "<br>Experience: 0–2 years"
        "<br>Freshers welcome"
        "</a>"
    )
    jobs = parse_glassdoor_alert_html(html)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "Software Engineer"
    assert job.snippet is not None
    assert "Experience" in job.snippet or "Freshers" in job.snippet


def test_sync_email_alerts_multi_account_pipeline(tmp_path: Path):
    """Verify sync_email_alerts iterates through multiple configured accounts and aggregates jobs."""
    db_file = tmp_path / "multi_sync.db"
    
    mock_accounts = [
        ("user1@example.com", "pass1"),
        ("user2@example.com", "pass2"),
    ]

    msg1 = EmailMessage()
    msg1["From"] = "jobalerts-noreply@linkedin.com"
    msg1["Subject"] = "Alert 1"
    msg1.set_content(
        '<a href="https://www.linkedin.com/jobs/view/10000001/">Software Engineer I</a>'
        '<div><span>Alpha GCC</span> · <span>Bengaluru, India</span></div>',
        subtype="html",
    )

    msg2 = EmailMessage()
    msg2["From"] = "jobalerts-noreply@linkedin.com"
    msg2["Subject"] = "Alert 2"
    msg2.set_content(
        '<a href="https://www.linkedin.com/jobs/view/10000002/">Software Engineer I</a>'
        '<div><span>Beta GCC</span> · <span>Hyderabad, India</span></div>',
        subtype="html",
    )

    def mock_fetch(server, port, user, password, **kwargs):
        if user == "user1@example.com":
            return [("uid_1", msg1)]
        elif user == "user2@example.com":
            return [("uid_2", msg2)]
        return []

    with patch("tools.ingest_email.get_configured_email_accounts", return_value=mock_accounts), \
         patch("tools.ingest_email.fetch_unread_alert_emails", side_effect=mock_fetch):
        jobs = sync_email_alerts(db_path=db_file)
        assert len(jobs) == 2
        companies = {j.company for j in jobs}
        assert "Alpha GCC" in companies
        assert "Beta GCC" in companies


def test_sync_email_alerts_notify(tmp_path: Path):
    """Verify sync_email_alerts triggers dispatch_notifications when notify=True."""
    db_file = tmp_path / "email_notify.db"
    init_db(db_file)

    msg = EmailMessage()
    msg["From"] = "jobalerts-noreply@linkedin.com"
    msg["Subject"] = "Jobs for you"
    msg.set_content(
        '<a href="https://www.linkedin.com/jobs/view/10000001/">Software Engineer I</a>'
        '<div><span>Acme Tech</span> · <span>Bengaluru, India</span></div>',
        subtype="html",
    )

    with patch("tools.ingest_email.get_configured_email_accounts", return_value=[("u@x.com", "p")]), \
         patch("tools.ingest_email.fetch_unread_alert_emails", return_value=[("1", msg)]), \
         patch("gcc_job_radar.notifier.dispatch_notifications") as mock_dispatch:
        jobs = sync_email_alerts(db_path=db_file, notify=True)
        assert len(jobs) >= 1
        assert mock_dispatch.called


# ==============================================================================
# 7. AccioJob Email Ingestion Tests
# ==============================================================================

def test_parse_acciojob_alert_html_assessment_invitation():
    """Verify parse_acciojob_alert_html extracts assessment invitation details and unwraps URLs."""
    html_content = (
        '<!DOCTYPE html><html><head><title>GoComet | Full Stack Developer Intern | Slot 1 Assessment Link</title></head>'
        '<body><div class="header">GoComet | Full Stack Developer Intern</div>'
        '<a href="https://acciojob-communications-prod.action.azurecomm.net/api/v2/a/c?url=https%3a%2f%2facciomatrix.com%2fverify-candidate%2fe8da110b-4325">'
        'Start Assessment</a></body></html>'
    )
    from tools.ingest_email import parse_acciojob_alert_html

    jobs = parse_acciojob_alert_html(
        html_content,
        sender="DoNotReply@acciojob.com",
        subject="Assessment Link for GoComet | Full Stack Developer Intern | Slot 1st",
    )
    assert len(jobs) == 1
    job = jobs[0]
    assert job.company == "GoComet"
    assert job.title == "Full Stack Developer Intern"
    assert "acciomatrix.com/verify-candidate/e8da110b-4325" in job.url
    assert job.source_platform == "acciojob"
    assert job.location == "India"


def test_parse_acciojob_alert_html_digest_table():
    """Verify parse_acciojob_alert_html parses TSV/text table digests from AccioJob."""
    raw_text = (
        "ReliaQuest\tAssociate Software Engineer\t\n"
        "Java\nSpringBoot\n"
        "19 Sept 2026\tCompany's Career Page\t\n"
        "⚡ Fast Track Placement Program\n₹55,000\t\n"
        "Docusign\tSoftware Engineer\t\n"
        "React\nNode.js\n"
        "19 Sept 2026\tCompany's Career Page\t"
    )
    from tools.ingest_email import parse_acciojob_alert_html

    jobs = parse_acciojob_alert_html(raw_text, sender="alerts@acciojob.com", subject="Daily Job Alert")
    assert len(jobs) == 2
    comps = {j.company for j in jobs}
    assert "ReliaQuest" in comps
    assert "Docusign" in comps
    # Fast Track promo should be excluded
    assert not any("fast track" in j.company.lower() for j in jobs)


def test_parse_acciojob_alert_html_skips_otp():
    """Verify parse_acciojob_alert_html ignores OTP and verification messages."""
    from tools.ingest_email import parse_acciojob_alert_html

    jobs = parse_acciojob_alert_html(
        "<div>Your OTP is 123456</div>",
        sender="DoNotReply@acciojob.com",
        subject="Login OTP for AccioMatrix",
    )
    assert jobs == []


def test_parse_email_alert_html_routes_acciojob():
    """Verify parse_email_alert_html routes AccioJob senders to parse_acciojob_alert_html."""
    from tools.ingest_email import parse_email_alert_html

    html = '<a href="https://acciomatrix.com/test">Start Assessment</a>'
    jobs = parse_email_alert_html(
        html,
        sender="donotreply@acciojob.com",
        subject="Assessment Link for TechCorp | SDE 1 Intern",
    )
    assert len(jobs) == 1
    assert jobs[0].company == "TechCorp"
    assert jobs[0].title == "SDE 1 Intern"


def test_parse_email_alert_html_does_not_fall_through_to_acciojob_for_indeed():
    """Verify an Indeed email with no valid job cards does not falsely match AccioJob parser or hang."""
    from tools.ingest_email import parse_email_alert_html
    import time

    # Large nested HTML email with title with pipe
    nested_html = "<html><head><title>Senior Architect | Indeed</title></head><body>" + "<div><table><tr><td>Some promo</td></tr></table></div>" * 500 + "</body></html>"
    t0 = time.perf_counter()
    jobs = parse_email_alert_html(
        nested_html,
        sender="donotreply@match.indeed.com",
        subject="Jobs you may like | Indeed",
    )
    duration = time.perf_counter() - t0
    assert jobs == []
    assert duration < 0.2, f"Indeed parsing took too long: {duration:.3f}s"


def test_parse_acciojob_alert_html_fast_bailout_on_unrelated():
    """Verify parse_acciojob_alert_html immediately bails out on unrelated emails."""
    from tools.ingest_email import parse_acciojob_alert_html

    jobs = parse_acciojob_alert_html(
        "<html><body>Just a normal newsletter</body></html>",
        sender="newsletter@medium.com",
        subject="Weekly digest",
    )
    assert jobs == []



