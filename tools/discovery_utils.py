"""Shared discovery utilities for company name normalization and ATS slug candidates."""

from __future__ import annotations

import re
import unicodedata
from typing import List, Dict

KNOWN_ABBREVIATIONS: Dict[str, List[str]] = {
    "texas instruments": ["ti"],
    "western digital": ["wd"],
    "hewlett packard": ["hp"],
    "general electric": ["ge"],
    "analog devices": ["adi"],
    "applied materials": ["amat"],
    "taiwan semiconductor": ["tsmc"],
    "international business machines": ["ibm"],
    "american express": ["amex"],
    "standard chartered": ["scb", "stan-chart"],
    "broadcom": ["broadcom"],
    "micron": ["micron"],
}

CORPORATE_SUFFIXES: List[str] = [
    "inc",
    "inc.",
    "corporation",
    "corp",
    "corp.",
    "llc",
    "ltd",
    "ltd.",
    "technologies",
    "technology",
    "solutions",
    "group",
    "global",
    "systems",
    "software",
    "holdings",
    "international",
    "services",
    "enterprises",
    "pvt",
    "pvt.",
    "private",
    "limited",
    "co",
    "co.",
]


def clean_company_name(name: str) -> str:
    """Strip common corporate suffixes (Inc, Corp, Ltd, Technologies, etc.)."""
    cleaned = name.strip()
    words = cleaned.split()
    while words and words[-1].lower().rstrip(".,") in CORPORATE_SUFFIXES:
        words.pop()
    return " ".join(words) if words else cleaned


def normalize_company_name(name: str) -> str:
    """Normalize company name to lowercase ASCII alphanumeric string."""
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("utf-8")
    return re.sub(r"[^a-z0-9]", "", n.lower())


def generate_slug_candidates(company_name: str) -> List[str]:
    """Generate standardized slug candidates pruned to top 2-3 most probable variations."""
    candidates: List[str] = []
    cleaned = clean_company_name(company_name)
    if not cleaned:
        return []

    # 1. Exact cleaned lowercase alphanumeric
    raw_alphanumeric = re.sub(r"[^a-zA-Z0-9]", "", cleaned).lower()
    if raw_alphanumeric:
        candidates.append(raw_alphanumeric)

    # 2. Hyphenated lowercase
    hyphenated = re.sub(r"[^a-zA-Z0-9]+", "-", cleaned.strip()).strip("-").lower()
    if hyphenated and hyphenated != raw_alphanumeric:
        candidates.append(hyphenated)

    # 3. Known acronym / abbreviation or initials
    cleaned_lower = cleaned.lower()
    orig_lower = company_name.strip().lower()

    known_abbrs: List[str] = []
    for key, abbr_list in KNOWN_ABBREVIATIONS.items():
        if key in (cleaned_lower, orig_lower):
            known_abbrs.extend(abbr_list)
            break

    for abbr in known_abbrs:
        if abbr not in candidates and len(candidates) < 3:
            candidates.append(abbr)

    if len(candidates) < 3:
        words = re.findall(r"[a-zA-Z0-9]+", cleaned)
        if len(words) >= 2:
            initials = "".join(w[0] for w in words).lower()
            if 2 <= len(initials) <= 4 and initials not in candidates:
                candidates.append(initials)

    # 4. If space permits (< 3), include uncleaned alphanumeric (e.g. 'alphacorp' for 'Alpha Corp')
    if len(candidates) < 3:
        raw_full = re.sub(r"[^a-zA-Z0-9]", "", company_name).lower()
        if raw_full and raw_full not in candidates:
            candidates.append(raw_full)

    # Deduplicate while preserving order, cap at top 3
    return list(dict.fromkeys(candidates))[:3]


def candidate_slugs(raw_name: str) -> List[str]:
    """Comprehensive candidate slugs generator for batch probes."""
    raw_name = raw_name.strip()
    slugs = set()
    cleaned = (
        raw_name.lower()
        .replace("&", "and")
        .replace("+", "plus")
        .replace(".ai", "ai")
        .replace(".cloud", "cloud")
        .replace(".com", "")
        .replace(".ag", "ag")
        .replace(".farm", "farm")
        .replace("°", "")
    )
    cleaned = unicodedata.normalize("NFKD", cleaned).encode("ascii", "ignore").decode("utf-8")

    alphanumeric = re.sub(r"[^a-z0-9]+", "", cleaned)
    if alphanumeric:
        slugs.add(alphanumeric)
        slugs.add(f"{alphanumeric}hq")
        slugs.add(f"{alphanumeric}tech")
        slugs.add(f"{alphanumeric}careers")
        slugs.add(f"{alphanumeric}jobs")

    hyphenated = re.sub(r"[^a-z0-9]+", "-", cleaned).strip("-")
    if hyphenated and hyphenated != alphanumeric:
        slugs.add(hyphenated)
        slugs.add(f"{hyphenated}-hq")
        slugs.add(f"{hyphenated}-careers")

    for suffix in ["labs", "technologies", "technology", "robotics", "aerospace", "digital", "systems", "software"]:
        if suffix in raw_name.lower():
            no_suf = re.sub(rf"\b{suffix}\b", "", raw_name, flags=re.I).strip()
            no_suf_clean = re.sub(r"[^a-z0-9]+", "", no_suf.lower())
            if no_suf_clean:
                slugs.add(no_suf_clean)
                slugs.add(re.sub(r"[^a-z0-9]+", "-", no_suf.lower()).strip("-"))

    return [s for s in slugs if s and len(s) >= 2]
