"""Data models for job postings and ATS metadata."""

from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, HttpUrl


class ATSProvider(str, Enum):
    """Supported canonical ATS providers."""

    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    SMARTRECRUITERS = "smartrecruiters"
    WORKDAY = "workday"
    PHENOM_SUCCESSFACTORS = "phenom_successfactors"
    EMAIL_ALERT = "email_alert"
    UNSTOP = "unstop"
    ACCIOJOB = "acciojob"
    AMAZON = "amazon"
    MICROSOFT = "microsoft"
    APPLE = "apple"
    EA = "ea"
    INTERNET_SEARCH = "internet_search"
    CUSTOM = "custom"


class JobStatus(str, Enum):
    """Job application tracking status values."""

    NEW = "NEW"
    APPLIED = "APPLIED"
    INTERVIEWING = "INTERVIEWING"
    REJECTED = "REJECTED"
    DISMISSED = "DISMISSED"
    NEEDS_RESOLVE = "NEEDS_RESOLVE"
    EXPIRED = "EXPIRED"


class CompanyConfig(BaseModel):
    """Configuration for an ATS job board or custom career site to scan."""

    name: str
    provider: ATSProvider
    board_token: str = ""
    career_url: str = ""
    cluster: Optional[str] = "3"
    extra: Optional[dict[str, Any]] = None


class JobPosting(BaseModel):
    """Normalized job posting structure across all ATS platforms."""

    id: str
    numeric_id: Optional[int] = None
    company: str
    title: str
    location: str
    apply_url: HttpUrl
    published_date: Optional[str] = "Recent"
    provider: ATSProvider
    is_remote: bool = False
    status: str = "NEW"
    applied_at: Optional[str] = None
    notes: Optional[str] = None
    direct_search_url: Optional[str] = None
    description: Optional[str] = None
    relevance_score: Optional[int] = 0
    matched_reasons: list[str] = []
    why: Optional[str] = None
    tailored_tex_path: Optional[str] = None
    tailored_pdf_path: Optional[str] = None
    tier: Optional[str] = None
    application_start_date: Optional[str] = None
    application_end_date: Optional[str] = None
    is_expired: bool = False


# Backward compatible alias referenced in legacy tests and scripts
JobOpening = JobPosting

