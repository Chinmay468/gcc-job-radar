"""Unit tests for CustomCareerClient direct HTML career page scraper."""

import httpx
import pytest
from gcc_job_radar.clients.custom_career import CustomCareerClient, clean_company_slug
from gcc_job_radar.models import ATSProvider, CompanyConfig, JobPosting
from gcc_job_radar.scanner import fetch_single_company


SAMPLE_HTML = """
<!DOCTYPE html>
<html>
<head><title>Acme Careers</title></head>
<body>
    <header>
        <a href="/about">About Us</a>
        <a href="/contact">Contact</a>
    </header>
    <main>
        <h1>Open Positions</h1>
        <div class="job-list">
            <!-- Tech positions (should be extracted) -->
            <a href="/careers/sde-1" class="job-link">Software Development Engineer - 1</a>
            <a href="https://acme.com/jobs/frontend" class="job-link">Junior Frontend Developer</a>
            <a href="/jobs/devops-intern">Cloud & DevOps Engineer Intern</a>
            <a href="/jobs/data-analyst">Associate Data Analyst</a>

            <!-- Non-tech positions (must be filtered out) -->
            <a href="/careers/sales-lead">Regional Sales Executive</a>
            <a href="/jobs/hr-manager">Human Resources Specialist</a>
            <a href="/jobs/telecaller">Customer Support Telecaller</a>
            <a href="/jobs/nurse">Corporate Staff Nurse</a>

            <!-- Irrelevant / anchor links -->
            <a href="#apply-now">Apply Now</a>
            <a href="javascript:void(0)">Click Here</a>
            <a href="mailto:careers@acme.com">Email Us</a>
        </div>
    </main>
</body>
</html>
"""


@pytest.mark.asyncio
async def test_custom_career_fetch_jobs_success():
    """Verify that engineering links are extracted and non-tech links are excluded."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=SAMPLE_HTML, headers={"content-type": "text/html"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = CustomCareerClient(http_client)
        postings = await client.fetch_jobs("Acme Corp", "https://acme.com/careers")

        assert len(postings) == 4
        titles = [p.title for p in postings]
        assert "Software Development Engineer - 1" in titles
        assert "Junior Frontend Developer" in titles
        assert "Cloud & DevOps Engineer Intern" in titles
        assert "Associate Data Analyst" in titles

        # Verify non-tech titles excluded
        assert not any("Sales" in t for t in titles)
        assert not any("Human Resources" in t for t in titles)
        assert not any("Customer Support" in t for t in titles)
        assert not any("Nurse" in t for t in titles)

        # Verify URL resolution
        urls = [str(p.apply_url) for p in postings]
        assert "https://acme.com/careers/sde-1" in urls
        assert "https://acme.com/jobs/frontend" in urls

        # Verify deterministic ID format
        for p in postings:
            assert p.id.startswith("custom_acme_corp_")
            assert p.provider == ATSProvider.CUSTOM
            assert p.company == "Acme Corp"


@pytest.mark.asyncio
async def test_custom_career_fetch_with_company_config():
    """Verify fetch_jobs works with CompanyConfig instance."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=SAMPLE_HTML, headers={"content-type": "text/html"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = CustomCareerClient(http_client)
        company = CompanyConfig(
            name="TCS Enterprise",
            provider=ATSProvider.CUSTOM,
            career_url="https://tcs.com/careers",
        )
        postings = await client.fetch_jobs(company)
        assert len(postings) == 4
        assert postings[0].company == "TCS Enterprise"
        assert postings[0].id.startswith("custom_tcs_enterprise_")


@pytest.mark.asyncio
async def test_scanner_fetch_single_company_routes_custom():
    """Verify scanner.fetch_single_company correctly dispatches custom providers."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=SAMPLE_HTML, headers={"content-type": "text/html"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        company = CompanyConfig(
            name="Reliance Jio",
            provider=ATSProvider.CUSTOM,
            career_url="https://jio.com/careers",
        )
        postings = await fetch_single_company(company, http_client)
        assert len(postings) == 4
        assert postings[0].company == "Reliance Jio"
        assert postings[0].id.startswith("custom_reliance_jio_")


@pytest.mark.asyncio
async def test_custom_career_error_handling():
    """Verify graceful handling of HTTP 404/500 and network errors."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = CustomCareerClient(http_client)
        postings = await client.fetch_jobs("Broken Site", "https://broken.com")
        assert postings == []


def test_clean_company_slug():
    """Verify company slug normalization."""
    assert clean_company_slug("Reliance Jio 5G") == "reliance_jio_5g"
    assert clean_company_slug("TCS (Tata Consultancy Services)") == "tcs_tata_consultancy_services"
    assert clean_company_slug("!!!") == "company"


@pytest.mark.asyncio
async def test_custom_career_turbohire_strict_entry_level_filtering():
    """Verify that TurboHire scraper strictly keeps entry-level tech roles and rejects senior/operations/CA roles."""
    turbohire_sample = {
        "Result": [
            {"JobId": "11111111-0000-0000-0000-000000000001", "JobTitle": "Software Development Engineer 1", "Location": '[{"Address": "Bengaluru, India"}]', "Experience": {"MinExp": 0}},
            {"JobId": "22222222-0000-0000-0000-000000000002", "JobTitle": "SDE-1", "Location": '[{"Address": "Bengaluru, India"}]', "Experience": {"MinExp": 1}},
            {"JobId": "33333333-0000-0000-0000-000000000003", "JobTitle": "SDE II Mobile", "Location": '[{"Address": "Bengaluru, India"}]', "Experience": {"MinExp": 3}},
            {"JobId": "44444444-0000-0000-0000-000000000004", "JobTitle": "Software development Engineer - IV", "Location": '[{"Address": "Bengaluru, India"}]', "Experience": {"MinExp": 5}},
            {"JobId": "55555555-0000-0000-0000-000000000005", "JobTitle": "Alite QA SPOC Station Request - BBD'26", "Location": '[{"Address": "Bengaluru, India"}]', "Experience": {"MinExp": 0}},
            {"JobId": "66666666-0000-0000-0000-000000000006", "JobTitle": "Support Engineer - Dark Stores", "Location": '[{"Address": "Bengaluru, India"}]', "Experience": {"MinExp": 0}},
            {"JobId": "77777777-0000-0000-0000-000000000007", "JobTitle": "System Support Engineer", "Location": '[{"Address": "Jaipur, India"}]', "Experience": {"MinExp": 1}},
            {"JobId": "88888888-0000-0000-0000-000000000008", "JobTitle": "CA Intern", "Location": '[{"Address": "Bengaluru, India"}]', "Experience": {"MinExp": 0}},
            {"JobId": "99999999-0000-0000-0000-000000000009", "JobTitle": "Sanpka Gurgaon Intern BBD Hiring", "Location": '[{"Address": "Gurgaon, India"}]', "Experience": {"MinExp": 0}},
            {"JobId": "aaaaaaaa-0000-0000-0000-000000000010", "JobTitle": "Architect", "Location": '[{"Address": "Bengaluru, India"}]', "Experience": {"MinExp": 8}},
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "api/token/noauth" in str(request.url):
            return httpx.Response(200, json={"access_token": "mock_token"})
        if "filteredjobs" in str(request.url):
            return httpx.Response(200, json=turbohire_sample)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = CustomCareerClient(http_client)
        postings = await client.fetch_jobs(
            "Flipkart",
            "https://flipkart.turbohire.co/careerpage/4d757ba0-3d57-448a-b82c-238ed87ac90f",
        )

        # Only the 2 genuine entry-level roles must be returned
        assert len(postings) == 2
        titles = [p.title for p in postings]
        assert "Software Development Engineer 1" in titles
        assert "SDE-1" in titles

        # Verify all noisy and senior roles are strictly absent
        assert not any("SDE II" in t for t in titles)
        assert not any("Engineer - IV" in t for t in titles)
        assert not any("BBD" in t for t in titles)
        assert not any("Dark Stores" in t for t in titles)
        assert not any("System Support" in t for t in titles)
        assert not any("CA Intern" in t for t in titles)
        assert not any("Architect" in t for t in titles)


@pytest.mark.asyncio
async def test_custom_career_filters_landing_pages_pdfs_and_generates_deterministic_ids():
    """Verify that landing pages, PDF downloads, and search CTAs are rejected, and IDs are deterministic."""
    html_with_landing_pages = """
    <!DOCTYPE html>
    <html>
    <body>
        <!-- Portal navigation & search CTAs matching positive title keywords (MUST be rejected) -->
        <a href="/early-in-career">Search Internships and New Grad Jobs</a>
        <a href="/en/careers/young-professionals">Explore our Early Career Programs</a>
        <a href="/in-en/careers/life-at-accenture/entry-level">Early career professionals</a>
        <a href="/en/india">Explore Cybersecurity Careers and Jobs at Palo Alto Networks India</a>
        
        <!-- Static PDF flyers (MUST be rejected) -->
        <a href="https://uploads.strikinglycdn.com/files/123/JD-Wysa_Junior+Fullstack.pdf">Junior Backend Developer</a>
        <a href="/files/Associate_Full_Stack_Engineer.pdf">Associate Full Stack Engineer</a>

        <!-- Legitimate job listings (MUST be extracted) -->
        <a href="/jobs/8186184-ai-application-engineering-intern">AI Application Engineering Intern</a>
        <a href="/jobs/8369837-junior-test-analyst">Junior Test Analyst</a>
    </body>
    </html>
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html_with_landing_pages, headers={"content-type": "text/html"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = CustomCareerClient(http_client)
        postings = await client.fetch_jobs("TestCompany", "https://testcompany.com/careers")

        # Only the 2 legitimate postings must be kept
        assert len(postings) == 2
        titles = [p.title for p in postings]
        assert "AI Application Engineering Intern" in titles
        assert "Junior Test Analyst" in titles

        # Verify all landing page and PDF titles rejected
        assert "Search Internships and New Grad Jobs" not in titles
        assert "Explore our Early Career Programs" not in titles
        assert "Early career professionals" not in titles
        assert "Explore Cybersecurity Careers and Jobs at Palo Alto Networks India" not in titles
        assert not any("Strikingly" in str(p.apply_url) for p in postings)
        assert not any(str(p.apply_url).endswith(".pdf") for p in postings)

        # Verify deterministic IDs
        first_run_ids = [p.id for p in postings]
        postings_run_2 = await client.fetch_jobs("TestCompany", "https://testcompany.com/careers")
        second_run_ids = [p.id for p in postings_run_2]
        assert first_run_ids == second_run_ids
        for jid in first_run_ids:
            assert jid.startswith("custom_testcompany_")


