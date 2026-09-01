"""Duplicate detection/grouping for maps_companies.

Extends the existing dup_group_id/is_duplicate columns (see src/db.py
MAPS_COMPANIES_MIGRATION_COLUMNS) -- this module is the single source of
truth for computing those two fields. Reuses normalize_address from
src/matching.py rather than re-implementing address normalization.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import yaml

from src.matching import normalize_address

_BRANDS_PATH = Path(__file__).resolve().parent.parent / "config" / "brands.yml"


@lru_cache(maxsize=1)
def _franchise_corporate_domains() -> set[str]:
    """Domains shared by many real, distinct franchise locations (e.g.
    servpro.com) -- a domain match alone must NOT be treated as a
    duplicate signal for these, since different real locations legitimately
    share the corporate domain. Reused from config/brands.yml rather than
    re-listing (single source of truth with Stage A's franchise matching)."""
    data = yaml.safe_load(_BRANDS_PATH.read_text()) or {}
    domains = set()
    for brand in data.get("brands", []):
        for d in brand.get("domains", []):
            domains.add(d.lower())
    return domains

_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")

_NAME_SUFFIXES = (
    "llc", "inc", "incorporated", "co", "corp", "corporation",
    "llp", "ltd",
)


def normalize_name(name: str | None, city: str | None = None) -> str:
    """Lowercase, punctuation-stripped business name with common legal
    suffixes (LLC/Inc/Co/Corp/LLP/Ltd) removed. If `city` is given and the
    row's own city appears as a trailing word (or words) of the name, that
    trailing city mention is also stripped -- e.g. 'ABC Restoration
    Jacksonville' in a row whose city is Jacksonville normalizes to 'abc
    restoration'. A city that is NOT the row's own city, or that's woven
    into a longer legitimate brand name in a non-trailing position, is left
    alone."""
    if not name:
        return ""
    s = name.strip().lower()
    s = _PUNCT_RE.sub(" ", s)
    words = [w for w in s.split() if w]

    # Strip trailing legal-suffix words (there can be more than one, e.g.
    # "... Inc Co" in noisy data -- strip repeatedly).
    while words and words[-1] in _NAME_SUFFIXES:
        words.pop()

    # Strip a trailing mention of the row's own city, word-by-word, only
    # when it matches the tail of the name exactly (avoids stripping a city
    # name that's merely a substring of a longer legitimate final word).
    if city:
        city_words = [w for w in _PUNCT_RE.sub(" ", city.strip().lower()).split() if w]
        if city_words and len(words) > len(city_words):
            if words[-len(city_words):] == city_words:
                words = words[: -len(city_words)]

    return _WS_RE.sub(" ", " ".join(words)).strip()


def normalize_phone(phone: str | None) -> str:
    """Digits only."""
    if not phone:
        return ""
    return re.sub(r"\D", "", phone)


def normalize_domain(website: str | None) -> str:
    """Root domain, lowercase, no www/scheme/path."""
    if not website:
        return ""
    w = website.strip()
    if "://" not in w:
        w = "http://" + w
    host = urlparse(w).netloc.lower()
    host = host.split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def is_likely_duplicate_pair(row_a: dict, row_b: dict) -> bool:
    """True if row_a and row_b are likely the same business: matching
    normalized name, phone, website root domain, or street address."""
    name_a = normalize_name(row_a.get("name"), city=row_a.get("city"))
    name_b = normalize_name(row_b.get("name"), city=row_b.get("city"))
    if name_a and name_b and name_a == name_b:
        return True

    phone_a = normalize_phone(row_a.get("phone"))
    phone_b = normalize_phone(row_b.get("phone"))
    if phone_a and phone_b and phone_a == phone_b:
        return True

    domain_a = normalize_domain(row_a.get("website"))
    domain_b = normalize_domain(row_b.get("website"))
    if (
        domain_a
        and domain_b
        and domain_a == domain_b
        and domain_a not in _franchise_corporate_domains()
    ):
        return True

    addr_a = normalize_address(row_a.get("address"))
    addr_b = normalize_address(row_b.get("address"))
    if addr_a and addr_b and addr_a == addr_b:
        return True

    return False


def _completeness_key(row: dict) -> tuple:
    """Sort key for picking a cluster's canonical row: more non-null
    "quality" fields wins, tie-broken on higher review_count."""
    fields = (row.get("website"), row.get("phone"), row.get("address"),
              row.get("rating"), row.get("review_count"))
    non_null_count = sum(1 for f in fields if f not in (None, ""))
    review_count = row.get("review_count") or 0
    return (non_null_count, review_count)


def assign_duplicate_groups(rows: list[dict]) -> list[dict]:
    """Union-find clustering of `rows` via is_likely_duplicate_pair.
    Returns new list of dicts (shallow copies) with dup_group_id set per
    cluster and is_duplicate=1 on every non-canonical row in a cluster of
    size > 1. Canonical = most complete row (see _completeness_key).
    Singletons get their own dup_group_id and is_duplicate=0."""
    n = len(rows)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    for i in range(n):
        for j in range(i + 1, n):
            if is_likely_duplicate_pair(rows[i], rows[j]):
                union(i, j)

    clusters: dict[int, list[int]] = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(i)

    out = [dict(r) for r in rows]
    for group_id, (root, members) in enumerate(clusters.items(), start=1):
        canonical_idx = max(members, key=lambda idx: _completeness_key(rows[idx]))
        for idx in members:
            out[idx]["dup_group_id"] = group_id
            out[idx]["is_duplicate"] = 0 if idx == canonical_idx else 1

    return out
