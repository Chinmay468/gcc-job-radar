"""Automated company registration and tracking module for gcc-job-radar.

Discovers, verifies, and auto-registers new hiring partner companies into
gcc_job_radar/config.py (COMPANIES list) and runtime memory.
"""

import asyncio
import logging
from pathlib import Path
import re
from typing import Optional

import httpx

from gcc_job_radar.config import COMPANIES
from gcc_job_radar.link_resolver import (
    build_direct_careers_search_url,
    resolve_company_career_portal,
)
from gcc_job_radar.models import ATSProvider, CompanyConfig
from tools.discovery_utils import generate_slug_candidates

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent / "config.py"

_INVALID_COMPANY_PATTERNS = re.compile(
    r"""(?ix)
    \b(
        acciojob|acciomatrix|accio\s*matrix|
        linkedin|naukri|indeed|glassdoor|unstop|cutshort|
        employer|confidential|hiring\s*company|various|client|
        fast\s*track|placement\s*program|scholarship|course|bootcamp
    )\b
    """
)


def is_generic_or_invalid_company(name: str) -> bool:
    """Return True if name is an aggregator, placeholder, or invalid company name."""
    clean = (name or "").strip()
    if not clean or len(clean) < 2 or len(clean) > 80:
        return True
    if _INVALID_COMPANY_PATTERNS.search(clean):
        return True
    return False


def get_known_company_names() -> set[str]:
    """Return set of normalized company names currently registered in config.COMPANIES."""
    return {c.name.strip().lower() for c in COMPANIES}


def is_known_company(company_name: str) -> bool:
    """Check if company_name is already tracked in config.COMPANIES."""
    name_clean = company_name.strip().lower()
    if not name_clean:
        return False
    known = get_known_company_names()
    if name_clean in known:
        return True
    # Also check board_tokens
    known_tokens = {c.board_token.strip().lower() for c in COMPANIES if c.board_token}
    slugs = generate_slug_candidates(company_name)
    return any(s in known_tokens for s in slugs if len(s) > 3)


def get_existing_company_config(company_name: str) -> Optional[CompanyConfig]:
    """Retrieve existing CompanyConfig matching company_name if present."""
    name_clean = company_name.strip().lower()
    for c in COMPANIES:
        if c.name.strip().lower() == name_clean:
            return c
    slugs = set(generate_slug_candidates(company_name))
    for c in COMPANIES:
        if c.board_token and c.board_token.strip().lower() in slugs:
            return c
    return None


async def probe_ats_for_company(company_name: str) -> Optional[tuple[ATSProvider, str]]:
    """Probe Ashby, Greenhouse, Lever, SmartRecruiters for live ATS boards."""
    from tools.probe_ats import probe_company

    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            res = await probe_company(company_name, client, existing_names=set())
            if res:
                return res.provider, res.board_token
    except Exception as exc:
        logger.debug("Error probing ATS for %s: %s", company_name, exc)
    return None


def append_company_config_to_file(
    cfg: CompanyConfig,
    config_path: Path = CONFIG_PATH,
) -> bool:
    """Append a single CompanyConfig entry to gcc_job_radar/config.py COMPANIES list."""
    if not config_path.exists():
        logger.warning("Config path does not exist: %s", config_path)
        return False

    content = config_path.read_text(encoding="utf-8")

    # Verify not already in file content
    name_check = f'name="{cfg.name}"'
    if name_check in content:
        return False

    # Format CompanyConfig line
    provider_name = (
        cfg.provider.name
        if hasattr(cfg.provider, "name")
        else str(cfg.provider.value if hasattr(cfg.provider, "value") else cfg.provider).upper()
    )

    extra_parts: list[str] = []
    if cfg.board_token:
        extra_parts.append(f'board_token="{cfg.board_token}"')
    if cfg.career_url:
        extra_parts.append(f'career_url="{cfg.career_url}"')
    if cfg.cluster and cfg.cluster != "3":
        extra_parts.append(f'cluster="{cfg.cluster}"')

    extra_str = f", {', '.join(extra_parts)}" if extra_parts else ""
    new_line = f'    CompanyConfig(name="{cfg.name}", provider=ATSProvider.{provider_name}{extra_str}),\n'

    # Match closing bracket of COMPANIES list: `(\n    CompanyConfig\([^\n]+\),\n)(\])`
    pattern = re.compile(r"(\n    CompanyConfig\([^\n]+\),\n)(\])")
    match = pattern.search(content)

    if match:
        new_content = content[: match.start(2)] + new_line + content[match.start(2) :]
    else:
        # Fallback: find `]\n\n# Strict entry-level`
        idx = content.find("]\n\n# Strict entry-level")
        if idx != -1:
            new_content = content[:idx] + new_line + content[idx:]
        else:
            logger.error("Could not locate COMPANIES list closing bracket in %s", config_path)
            return False

    config_path.write_text(new_content, encoding="utf-8")
    return True


def register_hiring_company(
    company_name: str,
    career_url: Optional[str] = None,
    provider: Optional[ATSProvider] = None,
    board_token: Optional[str] = None,
    config_path: Optional[Path] = None,
) -> Optional[CompanyConfig]:
    """Register a new hiring company into config.COMPANIES and persist to config.py.

    Args:
        company_name: Clean display name of the company.
        career_url: Optional known careers portal URL.
        provider: Optional known ATSProvider.
        board_token: Optional known ATS board token.
        config_path: Optional path to config.py.

    Returns:
        CompanyConfig if registered or existing, None if company is invalid.
    """
    name_clean = company_name.strip()
    if is_generic_or_invalid_company(name_clean):
        return None

    existing = get_existing_company_config(name_clean)
    if existing:
        return existing

    cfg_path = config_path or CONFIG_PATH

    resolved_provider = provider
    resolved_token = board_token or ""
    resolved_career_url = career_url or ""

    # 1. If provider and token not specified, attempt live ATS probe
    if not resolved_provider:
        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                # Running inside existing event loop
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    probe_res = pool.submit(asyncio.run, probe_ats_for_company(name_clean)).result()
            else:
                probe_res = asyncio.run(probe_ats_for_company(name_clean))

            if probe_res:
                resolved_provider, resolved_token = probe_res
        except Exception as exc:
            logger.debug("Failed probe for %s: %s", name_clean, exc)

    # 2. If still no ATS, resolve career portal
    if not resolved_provider:
        if not resolved_career_url:
            portal = resolve_company_career_portal(name_clean)
            if portal:
                resolved_career_url = portal

        if resolved_career_url and "myworkdayjobs.com" in resolved_career_url.lower():
            resolved_provider = ATSProvider.WORKDAY
            # Extract tenant from URL e.g. https://hpe.wd5.myworkdayjobs.com/Jobs_at_HPE
            m_wd = re.search(r"https?://([^/]+)/([^/?#]+)", resolved_career_url)
            if m_wd:
                resolved_token = f"{m_wd.group(1)}/{m_wd.group(2)}"
            else:
                resolved_token = name_clean.lower().replace(" ", "")
        else:
            resolved_provider = ATSProvider.CUSTOM
            slug = name_clean.lower().replace(" ", "").replace(".", "").replace("-", "")
            resolved_token = slug[:30]
            if not resolved_career_url:
                resolved_career_url = build_direct_careers_search_url(name_clean, "software engineer")

    cfg = CompanyConfig(
        name=name_clean,
        provider=resolved_provider,
        board_token=resolved_token,
        career_url=resolved_career_url,
    )

    # Append to file
    try:
        append_company_config_to_file(cfg, config_path=cfg_path)
    except Exception as exc:
        logger.warning("Failed appending %s to %s: %s", name_clean, cfg_path, exc)

    # Add to in-memory list
    COMPANIES.append(cfg)
    logger.info("Auto-registered new hiring company: %s (%s)", name_clean, resolved_provider.value)

    return cfg
