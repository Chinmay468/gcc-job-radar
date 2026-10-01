"""Direct ATS URL Resolver and Fallback Search Engine for GCC Job Radar.

Unwraps destination URLs from aggregator tracking parameters, discovers direct
ATS application links from HTML email cards, and generates formatted search queries
targeting Greenhouse, Lever, Ashby, and SmartRecruiters portals.
"""

import re
from typing import Any, Optional
import urllib.parse

DIRECT_ATS_DOMAINS: tuple[str, ...] = (
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "smartrecruiters.com",
    "myworkdayjobs.com",
    "workday.com",
    "jobs.jobvite.com",
    "breezy.hr",
    "recruitee.com",
    "icims.com",
    "amazon.jobs",
    "jobs.apple.com",
    "apply.careers.microsoft.com",
    "microsoft.eightfold.ai",
)

AGGREGATOR_DOMAINS: tuple[str, ...] = (
    "glassdoor.com",
    "linkedin.com",
    "indeed.com",
    "naukri.com",
)

NESTED_URL_PARAM_KEYS: tuple[str, ...] = (
    "url",
    "dest",
    "destination",
    "redirect_url",
    "redirect",
    "apply_url",
    "target",
    "link",
    "target_url",
    "q",
)

DIRECT_ATS_SEARCH_FILTER: str = "(site:greenhouse.io OR site:lever.co OR site:ashbyhq.com OR site:smartrecruiters.com)"

GLASSDOOR_DOMAINS: tuple[str, ...] = (
    "glassdoor.com",
    "glassdoor.co.in",
)

KNOWN_CAREER_PORTALS: dict[str, str] = {
    "bt group": "https://jobs.bt.com",
    "bt": "https://jobs.bt.com",
    "amazon": "https://amazon.jobs",
    "microsoft": "https://careers.microsoft.com",
    "google": "https://www.google.com/about/careers",
    "apple": "https://jobs.apple.com",
    "electronic arts": "https://jobs.ea.com/en_US/careers",
    "ea": "https://jobs.ea.com/en_US/careers",
    "meta": "https://www.metacareers.com",
    "cisco": "https://jobs.cisco.com",
    "ibm": "https://www.ibm.com/careers",
    "intel": "https://jobs.intel.com",
    "oracle": "https://careers.oracle.com",
    "amd": "https://careers.amd.com",
    "unisys": "https://jobs.unisys.com",
    "ericsson": "https://jobs.ericsson.com",
    "smiths medical": "https://smithsmedical.wd3.myworkdayjobs.com/External",
    "philips": "https://philips.wd3.myworkdayjobs.com/jobs-and-careers",
    "walmart": "https://walmart.wd5.myworkdayjobs.com/WalmartExternal",
    "walmart global tech": "https://walmart.wd5.myworkdayjobs.com/WalmartExternal",
    "salesforce": "https://salesforce.wd1.myworkdayjobs.com/External_Career_Site",
    "adobe": "https://adobe.wd5.myworkdayjobs.com/external_experienced",
    "nvidia": "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite",
    "qualcomm": "https://qualcomm.wd5.myworkdayjobs.com/External",
    "goldman sachs": "https://www.goldmansachs.com/careers",
    "jpmorgan chase": "https://careers.jpmorgan.com",
    "jpmorgan": "https://careers.jpmorgan.com",
    "morgan stanley": "https://www.morganstanley.com/about-us/careers",
    "barclays": "https://search.jobs.barclays",
    "hsbc": "https://www.hsbc.com/careers",
    "standard chartered": "https://www.sc.com/en/careers",
    "deutsche bank": "https://careers.db.com",
    "ubs": "https://www.ubs.com/global/en/careers.html",
    "bnp paribas": "https://group.bnpparibas/en/careers",
    "societe generale": "https://careers.societegenerale.com",
    "target": "https://corporate.target.com/careers",
    "accenture": "https://www.accenture.com/in-en/careers",
    "tcs": "https://www.tcs.com/careers",
    "infosys": "https://www.infosys.com/careers",
    "wipro": "https://careers.wipro.com",
    "cognizant": "https://careers.cognizant.com",
    "capgemini": "https://www.capgemini.com/careers",
    "deloitte": "https://jobs.deloitte.com",
    "pwc": "https://jobs.pwc.com",
    "ey": "https://careers.ey.com",
    "kpmg": "https://kpmg.com/careers",
    "sap": "https://jobs.sap.com",
    "siemens": "https://jobs.siemens.com",
    "schneider electric": "https://careers.se.com",
    "uber": "https://www.uber.com/us/en/careers",
    "netflix": "https://jobs.netflix.com",
    "stripe": "https://stripe.com/jobs",
    "spotify": "https://www.lifeatspotify.com",
    "airbnb": "https://careers.airbnb.com",
    "atlassian": "https://www.atlassian.com/company/careers",
    "twilio": "https://www.twilio.com/company/jobs",
    "dropbox": "https://jobs.dropbox.com",
    "slack": "https://slack.com/careers",
    "zoom": "https://careers.zoom.us",
    "dell": "https://jobs.dell.com",
    "hp": "https://jobs.hp.com",
    "hpe": "https://careers.hpe.com",
    "lenovo": "https://jobs.lenovo.com",
    "sony": "https://www.sony.com/en/SonyInfo/Careers",
    "thomson reuters": "https://careers.thomsonreuters.com",
    "huron consulting group": "https://www.huronconsultinggroup.com/careers",
    "huron consulting": "https://www.huronconsultinggroup.com/careers",
    "schlumberger": "https://careers.slb.com",
    "metlife": "https://www.metlifecareers.com",
    "shaadi.com": "https://people.shaadi.com",
    "shaadi": "https://people.shaadi.com",
    "enterpret": "https://enterpret.com/careers",
    "prometheum": "https://www.prometheum.com/careers",
    "platinumrx": "https://platinumrx.in/careers",
    "diversio": "https://www.diversio.com/careers",
    "eventtitans": "https://www.eventtitans.com/careers",
    "datastraw technologies": "https://datastraw.com/careers",
    "datastraw": "https://datastraw.com/careers",
    "particle14": "https://particle14.com/careers",
    "mhtechin": "https://mhtechnologies.in/careers",
    "tychr": "https://tychr.com/careers",
    "autolligent technologies": "https://autolligent.com/careers",
    "autolligent": "https://autolligent.com/careers",
    "ihsuk tech": "https://ihsuk.com/careers",
    "sirma business consulting": "https://sirmabc.com/careers",
    "sadhana it solutions": "https://sadhanasolutions.com/careers",
    "partyfly": "https://partyfly.in/careers",
    "parqis": "https://parqis.com/careers",
    "metaminds studio": "https://metaminds.studio",
    "metaminds": "https://metaminds.studio",
    "bargad.ai": "https://bargad.ai/careers",
    "big wings llc": "https://bigwingsllc.com/careers",
    "big wings": "https://bigwingsllc.com/careers",
    "dhiora groups": "https://dhioragroups.com/careers",
    "learnilm world": "https://learnilm.com/careers",
    "nexasec cyber solution": "https://nexasec.in/careers",
    "nexasec": "https://nexasec.in/careers",
    "p2intern": "https://p2intern.com/careers",
    "sourceo": "https://sourceo.in/careers",
    "storygame": "https://storygame.in/careers",
    "sutra sync": "https://sutrasync.com/careers",
    "the print story": "https://theprintstory.com/careers",
    "whinta": "https://whinta.com/careers",
    "devmani traders": "https://devmanitraders.com/careers",
    "domain expansion": "https://domainexpansion.in/careers",
}


def is_direct_ats_url(url: str) -> bool:
    """Check if a URL points directly to an enterprise ATS job board."""
    if not url:
        return False
    try:
        parsed = urllib.parse.urlparse(str(url).strip())
        netloc = parsed.netloc.lower()
        return any(domain in netloc for domain in DIRECT_ATS_DOMAINS)
    except Exception:
        return False


def is_aggregator_url(url: str) -> bool:
    """Check if a URL points to a third-party job aggregator (Glassdoor, LinkedIn, Indeed, etc.)."""
    if not url:
        return False
    try:
        parsed = urllib.parse.urlparse(str(url).strip())
        netloc = parsed.netloc.lower()
        return any(domain in netloc for domain in AGGREGATOR_DOMAINS)
    except Exception:
        return False


def is_glassdoor_url(url: str) -> bool:
    """Check if a URL points to Glassdoor job listings or portal."""
    if not url:
        return False
    try:
        parsed = urllib.parse.urlparse(str(url).strip())
        netloc = parsed.netloc.lower()
        return any(domain in netloc for domain in GLASSDOOR_DOMAINS)
    except Exception:
        return False


def is_specific_aggregator_job_url(url: str) -> bool:
    """Check if an aggregator URL points to a specific job opening rather than a generic search or portal page."""
    if not url:
        return False
    u = url.lower()
    if "linkedin.com" in u:
        return bool(re.search(r"/jobs/(?:view|collections)/|\bcurrentJobId=\d+", u) or "/comm/jobs/view/" in u)
    if "indeed.com" in u:
        return bool("/viewjob" in u or "/rc/clk" in u or "jk=" in u)
    if "naukri.com" in u:
        return bool("/job-listings" in u or "/jd/" in u)
    return False


def _normalize_company_name(name: str) -> str:
    """Normalize company name for fuzzy matching by removing common legal suffixes and punctuation."""
    if not name:
        return ""
    n = name.lower().strip()
    n = re.sub(
        r"\b(inc|corp|corporation|ltd|llc|pvt|private|limited|technologies|technology|tech|solutions|india)\b",
        "",
        n,
    )
    n = re.sub(r"[^\w\s]", " ", n)
    return " ".join(n.split())


def resolve_company_career_portal(company: str) -> Optional[str]:
    """Find the verified official career portal or canonical ATS board URL for a company."""
    if not company:
        return None

    raw_lower = company.strip().lower()
    norm_comp = _normalize_company_name(company)

    # 1. Exact lookup in KNOWN_CAREER_PORTALS
    if raw_lower in KNOWN_CAREER_PORTALS:
        return KNOWN_CAREER_PORTALS[raw_lower]
    if norm_comp in KNOWN_CAREER_PORTALS:
        return KNOWN_CAREER_PORTALS[norm_comp]

    # Whole-word or phrase matching in KNOWN_CAREER_PORTALS
    raw_words = set(re.findall(r"\b\w+\b", raw_lower))
    for k, portal in KNOWN_CAREER_PORTALS.items():
        if " " in k:
            if re.search(rf"\b{re.escape(k)}\b", raw_lower):
                return portal
        else:
            if k in raw_words:
                return portal

    # 2. Check COMPANIES registry in gcc_job_radar.config
    try:
        from gcc_job_radar.config import COMPANIES
        from gcc_job_radar.models import ATSProvider

        matched_config = None
        for c in COMPANIES:
            c_raw = c.name.strip().lower()
            c_norm = _normalize_company_name(c.name)
            if c_raw == raw_lower or (c_norm and c_norm == norm_comp):
                matched_config = c
                break
            # Prefix/phrase word matching (e.g. "Walmart" vs "Walmart Global Tech")
            if norm_comp and c_norm:
                if c_norm.startswith(norm_comp + " ") or norm_comp.startswith(c_norm + " "):
                    matched_config = c
                    break

        if matched_config:
            prov = matched_config.provider
            token = matched_config.board_token
            if prov == ATSProvider.GREENHOUSE:
                return f"https://job-boards.greenhouse.io/{token}"
            elif prov == ATSProvider.LEVER:
                return f"https://jobs.lever.co/{token}"
            elif prov == ATSProvider.ASHBY:
                return f"https://jobs.ashbyhq.com/{token}"
            elif prov == ATSProvider.SMARTRECRUITERS:
                return f"https://jobs.smartrecruiters.com/{token}"
            elif prov == ATSProvider.WORKDAY:
                parts = token.split("/", 1)
                cluster = getattr(matched_config, "cluster", None) or "3"
                if len(parts) == 2:
                    return f"https://{parts[0]}.wd{cluster}.myworkdayjobs.com/{parts[1]}"
                return f"https://{token}.wd{cluster}.myworkdayjobs.com"
            elif prov == ATSProvider.PHENOM_SUCCESSFACTORS:
                return token if token.startswith(("http://", "https://")) else f"https://{token}"
            elif prov == ATSProvider.AMAZON:
                return "https://www.amazon.jobs"
            elif prov == ATSProvider.MICROSOFT:
                return "https://apply.careers.microsoft.com"
            elif prov == ATSProvider.APPLE:
                return "https://jobs.apple.com/en-in/search"
            elif prov == ATSProvider.EA:
                return "https://jobs.ea.com/en_US/careers"

    except Exception:
        pass

    return None


def unwrap_destination_url(url: str, max_depth: int = 3) -> Optional[str]:
    """Inspect tracking/redirect URL for nested direct destination links.

    Unwraps query parameters like url=..., dest=..., redirect_url=..., apply_url=...
    recursively up to max_depth.
    """
    if not url or max_depth <= 0:
        return None

    try:
        parsed = urllib.parse.urlparse(str(url).strip())
        if not parsed.query:
            return None

        query_params = urllib.parse.parse_qs(parsed.query, keep_blank_values=False)
        for key in NESTED_URL_PARAM_KEYS:
            for candidate_key, values in query_params.items():
                if candidate_key.lower() == key:
                    for val in values:
                        unquoted = urllib.parse.unquote(val).strip()
                        if unquoted.startswith(("http://", "https://")):
                            # If it directly matches an ATS domain, return immediately
                            if is_direct_ats_url(unquoted):
                                return unquoted
                            # Try deeper unwrapping
                            deeper = unwrap_destination_url(unquoted, max_depth=max_depth - 1)
                            if deeper and is_direct_ats_url(deeper):
                                return deeper
                            # Return candidate if valid external URL
                            return deeper or unquoted
    except Exception:
        pass

    return None


def find_direct_ats_link_in_html(html_snippet: str) -> Optional[str]:
    """Search HTML markup (e.g. email alert card vicinity) for direct ATS anchor links."""
    if not html_snippet:
        return None

    # Pattern for anchor tags linking directly to ATS domains
    pattern = re.compile(
        r"""href=["'](?P<url>https?://[^"']*(?:greenhouse\.io|lever\.co|ashbyhq\.com|smartrecruiters\.com|myworkdayjobs\.com)[^"']*)["']""",
        re.IGNORECASE,
    )
    m = pattern.search(html_snippet)
    if m:
        return m.group("url")

    return None


def build_direct_search_url(company: str, title: str, engine: str = "google") -> str:
    """Construct search query URL targeting company ATS postings on major ATS portals.

    Format:
    https://www.google.com/search?q="{company}"+"{title}"+careers+(site:greenhouse.io+OR+site:lever.co+OR+site:ashbyhq.com+OR+site:smartrecruiters.com)
    """
    clean_company = re.sub(r'["\']', "", company or "").strip()
    clean_title = re.sub(r'["\']', "", title or "").strip()

    query = f'"{clean_company}" "{clean_title}" careers {DIRECT_ATS_SEARCH_FILTER}'
    encoded_query = urllib.parse.quote_plus(query)

    if engine.lower() == "duckduckgo":
        return f"https://html.duckduckgo.com/html/?q={encoded_query}"

    return f"https://www.google.com/search?q={encoded_query}"


def build_direct_careers_search_url(company: str, title: str = "", engine: str = "google") -> str:
    """Construct an unblocked high-precision search query targeting the company's careers portal.

    Unlike build_direct_search_url which restricts to 4 ATS site domains, this search query
    is unconstrained and allows Google to surface official company career pages, Workday,
    Taleo, SuccessFactors, or direct in-house portals.
    """
    clean_company = re.sub(r'["\']', "", company or "").strip()
    clean_title = re.sub(r'["\']', "", title or "").strip()

    if clean_title:
        query = f'"{clean_company}" "{clean_title}" careers jobs apply'
    else:
        query = f'"{clean_company}" careers jobs apply'

    encoded_query = urllib.parse.quote_plus(query)
    if engine.lower() == "duckduckgo":
        return f"https://html.duckduckgo.com/html/?q={encoded_query}"
    return f"https://www.google.com/search?q={encoded_query}"


def build_direct_careers_redirect_url(company: str, title: str = "") -> str:
    """Construct a direct redirect URL that lands straight on the company's careers portal.

    Uses Google Feeling Lucky (btnI=1) which 302-redirects the browser directly to the
    company's official career portal or job listing, bypassing aggregator Cloudflare 403 blocks.
    """
    clean_company = re.sub(r'["\']', "", company or "").strip()
    clean_title = re.sub(r'["\']', "", title or "").strip()
    if clean_company and clean_title:
        query = f"{clean_company} {clean_title} careers"
    elif clean_company:
        query = f"{clean_company} careers"
    elif clean_title:
        query = f"{clean_title} careers"
    else:
        query = "careers jobs apply"

    encoded = urllib.parse.quote_plus(query)
    return f"https://www.google.com/search?btnI=1&q={encoded}"


def is_job_legitimate(company: str, title: str, location: str = "") -> tuple[bool, str]:
    """Validate whether a job posting is genuine and qualifies for entry-level tracking.

    Returns:
        (is_legit, reason)
    """
    from gcc_job_radar.filters import (
        is_entry_level,
        is_potential_india_location,
        is_remote_opening,
        requires_experienced_candidate,
    )

    comp = (company or "").strip()
    tit = (title or "").strip()
    loc = (location or "").strip()

    if not comp or len(comp) < 2:
        return False, "Missing or invalid company name"
    if not tit or len(tit) < 2:
        return False, "Missing or invalid job title"

    # Block spam or scam indicators often seen in raw job aggregators
    spam_patterns = (
        r"\b(?:earn\s+money|without\s+investment|data\s+entry\s+work\s+from\s+home|part\s*time\s*income|sms\s*sending|captcha\s*typing)\b",
        r"\b(?:5000\+?\s*vacancies|urgent\s+hiring\s+for\s+all)\b",
    )
    for pat in spam_patterns:
        if re.search(pat, tit, re.IGNORECASE) or re.search(pat, comp, re.IGNORECASE):
            return False, "Flagged as promotional/spam posting"

    if not is_entry_level(tit):
        return False, "Role does not match entry-level / junior software criteria"

    if requires_experienced_candidate(tit):
        return False, "Role explicitly requires senior/experienced candidate"

    if loc:
        if not (is_potential_india_location(loc) or is_remote_opening({"location": loc, "title": tit, "is_remote": False})):
            return False, f"Location '{loc}' outside India / remote criteria"

    return True, "Valid entry-level opportunity"


def resolve_effective_apply_url(job: Any) -> tuple[str, str, str]:
    """Resolve the best clickable application link, direct search fallback, and descriptive label.

    Redirects to the company's verified careers portal (if known), or directly to the job listing
    on the hosting platform (Indeed, LinkedIn, Naukri, etc.). Glassdoor URLs are strictly never
    returned because they trigger Cloudflare 403 / security challenges for candidates; instead,
    the company's direct careers portal or redirect is provided.

    Returns:
        (effective_apply_url, direct_search_url, link_label)
        - effective_apply_url: Direct ATS URL, company careers portal, or direct platform URL.
        - direct_search_url: Formatted Google direct search link.
        - link_label: User-friendly button/link label (e.g. 'Apply on ATS', 'Official Careers Portal',
                      'Apply on LinkedIn', '{Company} Careers').
    """
    def _extract(field: str) -> str:
        val = getattr(job, field, None)
        if val is not None:
            return str(val)
        if hasattr(job, "__getitem__"):
            try:
                val = job[field]
                if val is not None:
                    return str(val)
            except (KeyError, IndexError, TypeError):
                pass
        return ""

    company = _extract("company")
    title = _extract("title")
    orig_url = _extract("apply_url")
    orig_url_str = orig_url.strip()
    stored_search = _extract("direct_search_url") or None

    # 1. Construct high-precision direct search fallback (for secondary search)
    fallback_search = stored_search or build_direct_careers_search_url(company, title)

    # 2. Try unwrapping nested destination URL
    unwrapped = unwrap_destination_url(orig_url_str)
    if unwrapped and is_direct_ats_url(unwrapped):
        return unwrapped, fallback_search, "Apply on ATS"

    # 3. If original URL is directly a valid ATS link, use it!
    if is_direct_ats_url(orig_url_str):
        return orig_url_str, fallback_search, "Apply on ATS"

    # 4. If URL is a Glassdoor link:
    # Glassdoor links NEVER work (Cloudflare 403 / bot challenge blocks candidates).
    # Strictly return the company's careers page link instead!
    if is_glassdoor_url(orig_url_str):
        portal = resolve_company_career_portal(company)
        if portal:
            return portal, fallback_search, "Official Careers Portal"

        # Direct Feeling Lucky redirect link landing straight on the company's career portal
        careers_url = build_direct_careers_redirect_url(company, title)
        label = f"{company} Careers" if company else "Official Careers Portal"
        return careers_url, fallback_search, label

    # 5. If URL is another aggregator (LinkedIn, Indeed, Naukri, etc.):
    if is_aggregator_url(orig_url_str):
        # If this points to a specific job opening on LinkedIn, Indeed, or Naukri,
        # KEEP the direct job URL so the candidate lands directly on the application page!
        if is_specific_aggregator_job_url(orig_url_str):
            url_lower = orig_url_str.lower()
            if "linkedin.com" in url_lower:
                platform_name = "LinkedIn"
            elif "indeed.com" in url_lower:
                platform_name = "Indeed"
            elif "naukri.com" in url_lower:
                platform_name = "Naukri"
            else:
                platform_name = "Platform"
            return orig_url_str, fallback_search, f"Apply on {platform_name}"

        portal = resolve_company_career_portal(company)
        if portal:
            return portal, fallback_search, "Official Careers Portal"

        if orig_url_str.startswith(("http://", "https://")):
            url_lower = orig_url_str.lower()
            if "linkedin.com" in url_lower:
                platform_name = "LinkedIn"
            elif "indeed.com" in url_lower:
                platform_name = "Indeed"
            elif "naukri.com" in url_lower:
                platform_name = "Naukri"
            else:
                platform_name = "Platform"
            return orig_url_str, fallback_search, f"Apply on {platform_name}"

        careers_url = build_direct_careers_redirect_url(company, title)
        label = f"{company} Careers" if company else "Official Careers Portal"
        return careers_url, fallback_search, label

    # 5. Non-aggregator custom URL
    if orig_url_str.startswith(("http://", "https://")):
        return orig_url_str, fallback_search, "Apply on Portal"

    return fallback_search, fallback_search, "Search & Apply on Company Careers"


def resolve_job_link(job: dict[str, Any]) -> tuple[str, str]:
    """Resolve primary apply URL and generate fallback direct ATS search link for a job record."""
    company = job.get("company") or ""
    title = job.get("title") or ""
    original_url = str(job.get("apply_url") or "")

    # Check for direct destination URL
    unwrapped = unwrap_destination_url(original_url)
    if unwrapped and is_direct_ats_url(unwrapped):
        resolved_apply_url = unwrapped
    elif is_glassdoor_url(original_url):
        portal = resolve_company_career_portal(company)
        resolved_apply_url = portal if portal else build_direct_careers_redirect_url(company, title)
    else:
        resolved_apply_url = original_url

    # Build direct search fallback
    direct_search_url = (
        build_direct_careers_search_url(company, title)
        if is_glassdoor_url(original_url)
        else build_direct_search_url(company, title)
    )

    return resolved_apply_url, direct_search_url
