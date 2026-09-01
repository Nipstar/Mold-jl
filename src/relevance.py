"""Stage A relevance helpers: category denylist filtering + franchise brand
matching. Both config-driven (config/category_denylist.yml,
config/brands.yml) -- not hardcoded -- so the lists can be edited without a
code change."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parent.parent
CATEGORY_DENYLIST_PATH = ROOT / "config" / "category_denylist.yml"
BRANDS_PATH = ROOT / "config" / "brands.yml"


@lru_cache(maxsize=1)
def _load_denylist() -> set[str]:
    data = yaml.safe_load(CATEGORY_DENYLIST_PATH.read_text()) or {}
    return {v.strip().lower() for v in data.get("denylist", [])}


@lru_cache(maxsize=1)
def _load_brands() -> list[dict]:
    data = yaml.safe_load(BRANDS_PATH.read_text()) or {}
    return data.get("brands", [])


# Positive relevance signal: google_category must actually BE mold/
# restoration-related, not merely "not on the denylist". Substring match,
# case-insensitive, against the whole category string.
POSITIVE_TERMS = [
    "mold",
    "restoration",
    "water damage",
    "fire damage",
    "water extraction",
    "flood",
    "environmental consultant",
    "environmental health",
    "environmental testing",
    "asbestos testing",
    "disaster recovery",
    "remediation",
]


def _has_positive_match(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in POSITIVE_TERMS)


def _is_denylisted(google_category: str) -> bool:
    denylist = _load_denylist()
    components = [c.strip().lower() for c in google_category.split(";")]
    for c in components:
        for term in denylist:
            if c == term or term in c.split("/"):
                return True
    return False


def is_category_relevant(google_category: str | None, categories: str | None = None) -> bool:
    """Two-stage check:

    1. POSITIVE match -- google_category must contain a mold/restoration
       related term (POSITIVE_TERMS). Not being on the denylist is NOT
       enough on its own -- e.g. 'Auto repair shop' / 'HVAC contractor'
       have no positive match and are excluded even though neither is
       denylisted.
    2. Denylist as a hard override on top of a positive match -- e.g.
       'Auto restoration service' contains 'restoration' but is
       denylisted, so the denylist wins and it stays excluded.

    If google_category is null/empty, fall back to `categories` (the
    search-sweep terms the row was found under): relevant only if one of
    those terms is itself mold/restoration-related. Never default to True
    blindly."""
    if google_category:
        if not _has_positive_match(google_category):
            return False
        if _is_denylisted(google_category):
            return False
        return True

    if categories and _has_positive_match(categories):
        return True
    return False


def _root_domain(website: str) -> str | None:
    website = website.strip()
    if not website:
        return None
    if "://" not in website:
        website = "//" + website
    host = urlparse(website).netloc or urlparse(website).path
    host = host.split("/")[0].split(":")[0].lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None


def match_franchise_brand(name: str, website: str | None) -> str | None:
    """Return canonical franchise brand name if `name` or `website` matches
    a known brand from config/brands.yml, else None. Name matching is
    case-insensitive and word-boundary-safe (substrings must match as whole
    words/phrases, not as part of an unrelated word) so generic terms like
    "Restoration" never match. Website matching is by root domain."""
    if not name and not website:
        return None
    name_upper = (name or "").upper()
    root_domain = _root_domain(website) if website else None

    for brand in _load_brands():
        for sub in brand.get("name_substrings", []):
            pattern = r"\b" + re.escape(sub.upper()) + r"\b"
            if re.search(pattern, name_upper):
                return brand["name"]
        if root_domain:
            for domain in brand.get("domains", []):
                if root_domain == domain.lower() or root_domain.endswith("." + domain.lower()):
                    return brand["name"]
    return None
