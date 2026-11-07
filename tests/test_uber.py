"""Unit tests for Uber Careers ATS client and scanner integration."""

import httpx
import pytest

from gcc_job_radar.clients.uber import UberClient
from gcc_job_radar.models import ATSProvider, CompanyConfig
from gcc_job_radar.scanner import DEFAULT_DOMAIN_LIMITS, get_company_domain
from tools.company_resolver import get_canonical_ats_url, lookup_in_radar_registry


@pytest.mark.asyncio
async def test_uber_client_success() -> None:
    """Test Uber client parses Oracle Cloud HCM CE payload, applies title/location/experience filters."""
    payload = {
        "items": [
            {
                "SearchId": 1,
                "TotalJobsCount": 4,
                "requisitionList": [
                    {
                        "Id": "304442",
                        "Title": "Software Engineer I - Backend",
                        "PrimaryLocation": "Bengaluru, Karnataka, India",
                        "PostedDate": "2026-11-04",
                        "ShortDescriptionStr": "Build high-throughput backend services in Go and Java.",
                        "ExternalQualificationsStr": "BS in CS or equivalent.",
                        "ExternalResponsibilitiesStr": "Design distributed microservices.",
                        "WorkplaceTypeCode": "ORA_ON_SITE",
                    },
                    {
                        "Id": "304409",
                        "Title": "Industry Lead, Auto, Uber Advertising",  # Disqualified: non-tech role
                        "PrimaryLocation": "Mumbai, Maharashtra, India",
                        "PostedDate": "2026-11-03",
                        "ShortDescriptionStr": "Drive ad sales.",
                    },
                    {
                        "Id": "304547",
                        "Title": "Software Engineer I - Backend",
                        "PrimaryLocation": "San Francisco, CA, United States",  # Disqualified: US location
                        "PostedDate": "2026-11-02",
                        "ShortDescriptionStr": "Core backend platform.",
                    },
                    {
                        "Id": "304999",
                        "Title": "Software Engineer I - Backend",  # Disqualified: requires 8+ years experience
                        "PrimaryLocation": "Hyderabad, Telangana, India",
                        "PostedDate": "2026-11-01",
                        "ShortDescriptionStr": "Senior role.",
                        "ExternalQualificationsStr": "Requires minimum 8+ years of industry experience.",
                    },
                ],
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        import urllib.parse
        url_decoded = urllib.parse.unquote(str(request.url))
        assert "recruitingCEJobRequisitions" in url_decoded
        assert "siteNumber=UberCareers" in url_decoded
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        company = CompanyConfig(
            name="Uber",
            provider=ATSProvider.UBER,
            board_token="uber",
            extra={"max_pages": 1},
        )
        jobs = await UberClient(client).fetch_jobs(company)

        assert len(jobs) == 1
        job = jobs[0]
        assert job.id == "304442"
        assert job.numeric_id == 304442
        assert job.company == "Uber"
        assert job.title == "Software Engineer I - Backend"
        assert job.location == "Bengaluru, Karnataka, India"
        assert str(job.apply_url) == "https://www.uber.com/global/en/careers/list/304442/"
        assert job.published_date == "2026-11-04"
        assert job.provider == ATSProvider.UBER
        assert job.is_remote is False


@pytest.mark.asyncio
async def test_uber_client_pagination() -> None:
    """Test Uber client traverses multiple pages via offset parameter."""
    def handler(request: httpx.Request) -> httpx.Response:
        import urllib.parse
        url_str = urllib.parse.unquote(str(request.url))
        if "offset=0" in url_str:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "TotalJobsCount": 2,
                            "requisitionList": [
                                {
                                    "Id": "101",
                                    "Title": "Software Engineer I - Backend",
                                    "PrimaryLocation": "Bengaluru, Karnataka, India",
                                    "PostedDate": "2026-11-04",
                                }
                            ],
                        }
                    ]
                },
            )
        elif "offset=1" in url_str:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "TotalJobsCount": 2,
                            "requisitionList": [
                                {
                                    "Id": "102",
                                    "Title": "Associate Software Engineer",
                                    "PrimaryLocation": "Hyderabad, Telangana, India",
                                    "PostedDate": "2026-11-03",
                                }
                            ],
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"items": [{"TotalJobsCount": 2, "requisitionList": []}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        company = CompanyConfig(
            name="Uber",
            provider=ATSProvider.UBER,
            board_token="uber",
            extra={"max_pages": 3, "result_limit": 1},
        )
        jobs = await UberClient(client).fetch_jobs(company)

        assert len(jobs) == 2
        assert jobs[0].id == "101"
        assert jobs[1].id == "102"


def test_uber_domain_and_resolver() -> None:
    """Verify domain mapping and company resolver integration for Uber."""
    company = CompanyConfig(name="Uber", provider=ATSProvider.UBER, board_token="uber")
    domain = get_company_domain(company)
    assert domain == "uber.com"
    assert "uber.com" in DEFAULT_DOMAIN_LIMITS

    resolved = lookup_in_radar_registry("Uber")
    assert resolved is not None
    assert resolved.name == "Uber"
    assert resolved.provider == ATSProvider.UBER

    portal_url, label = get_canonical_ats_url(resolved)
    assert portal_url == "https://www.uber.com/global/en/careers/list/"
    assert "Uber Careers" in label
