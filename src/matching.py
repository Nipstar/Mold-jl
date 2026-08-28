"""Shared matching helpers for the geo/trade sweep approach (stage2_places.py):

- normalize_address / address_match: street-level address comparison, tolerant
  of "St" vs "Street" style formatting diffs, ignores suite/unit and anything
  after the street segment.
- looks_like_person_name: detects the DBPR data quirk where dba_name actually
  holds "LASTNAME, FIRSTNAME" (a person's name mis-filed as a DBA) rather than
  a real business name -- these should never be searched as a business name.
- name_fuzzy_score: token-set-ratio wrapper for the DBA-name fallback match.
"""
from __future__ import annotations

import re

from rapidfuzz import fuzz

# --- person-name-in-dba-field detection -----------------------------------

_PERSON_NAME_RE = re.compile(
    r"^[A-Z][A-Za-z'\-]*(?:\s[A-Z][A-Za-z'\-]*)*,\s*[A-Z][A-Za-z'\-]*(?:\s[A-Z][A-Za-z'\-]*)*$"
)
_BUSINESS_WORDS = (
    "LLC", "INC", "CORP", "CO", "SERVICES", "SERVICE", "RESTORATION",
    "REMEDIATION", "MOLD", "GROUP", "SOLUTIONS", "ENTERPRISES",
    "CONTRACTING", "CONSTRUCTION", "INSPECTIONS", "INSPECTION",
    "ASSESSMENT", "ASSESSMENTS", "ASSOCIATES", "PARTNERS", "COMPANY",
    "RESTORATIONS", "PROPERTY", "PROPERTIES", "ENVIRONMENTAL", "TESTING",
)


def looks_like_person_name(name: str) -> bool:
    """True when `name` is DBPR's 'LASTNAME, FIRSTNAME [MIDDLE]' pattern with
    no business-suffix words -- i.e. it's a person's name, not a real DBA."""
    if not name:
        return False
    if not _PERSON_NAME_RE.match(name.strip()):
        return False
    upper = name.upper()
    return not any(w in upper for w in _BUSINESS_WORDS)


# --- address normalization / matching -------------------------------------

_UNIT_SPLIT_RE = re.compile(
    r"\b(ste|suite|unit|apt|apartment|bldg|building|fl|floor|rm|room|#)\b"
)
_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")

_SUFFIX_MAP = {
    "street": "st", "avenue": "ave", "drive": "dr", "road": "rd",
    "boulevard": "blvd", "lane": "ln", "court": "ct", "place": "pl",
    "circle": "cir", "highway": "hwy", "parkway": "pkwy", "terrace": "ter",
    "trail": "trl", "square": "sq", "crossing": "xing", "point": "pt",
    "north": "n", "south": "s", "east": "e", "west": "w",
    "northeast": "ne", "northwest": "nw", "southeast": "se", "southwest": "sw",
}


def normalize_address(address: str | None) -> str:
    """Street number + street name only, lowercase, punctuation stripped,
    common suffix words collapsed to abbreviations, suite/unit/floor and
    anything after it dropped. Empty string in -> empty string out."""
    if not address:
        return ""
    s = address.strip().lower()
    # Only the street segment matters -- drop city/state/zip if present.
    s = s.split(",")[0]
    # Drop suite/unit/floor/room markers and everything after them.
    s = _UNIT_SPLIT_RE.split(s)[0]
    s = _PUNCT_RE.sub(" ", s)
    words = [w for w in s.split() if w]
    words = [_SUFFIX_MAP.get(w, w) for w in words]
    return _WS_RE.sub(" ", " ".join(words)).strip()


def address_match(address_a: str | None, address_b: str | None, threshold: int = 90) -> bool:
    """True if two raw addresses normalize to (near-)identical street
    segments. Exact match after normalization, or fuzzy ratio >= threshold to
    tolerate minor formatting/typo differences."""
    a = normalize_address(address_a)
    b = normalize_address(address_b)
    if not a or not b:
        return False
    if a == b:
        return True
    return fuzz.ratio(a, b) >= threshold


def name_fuzzy_score(name_a: str | None, name_b: str | None) -> int:
    """Token-set-ratio between two business names (0-100)."""
    a = (name_a or "").strip().lower()
    b = (name_b or "").strip().lower()
    if not a or not b:
        return 0
    return int(fuzz.token_set_ratio(a, b))
