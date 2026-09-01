"""Location/address heuristics -- Stage B fix.

BUG (fixed by this module): rows with no real street address were getting the
search-grid-point's swept city/county assigned by src/ingest/maps_first.py,
producing nonsense like 'Water Mold Fire Restoration of Miami' tagged
McDavid/Escambia. This module provides:

- has_street_address(): does an address string contain a real street portion
  (number + street name), vs. just a city/state/zip fragment or nothing.
- classify_location_source(): gbp_address | grid_centroid | unknown.
- is_out_of_area(): flags listings whose name/website/phone signal a
  different STATE than the target (not a different FL region -- south-FL
  companies swept into a north-FL run are a separate out-of-target-region
  concern, not handled here).
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
_REGION_CONFIG_PATH = ROOT / "config" / "region.yml"

# A real street address starts with a house number followed by a street
# name/type -- e.g. "1514 Roberts Dr Ste 2". A bare "City, ST 12345" or
# "City, ST" fragment has no leading number-led street segment.
_STREET_ADDRESS_RE = re.compile(r"^\s*\d+[\w]*\s+\S+")

# US state abbreviations and full names, minus FL, used by is_out_of_area's
# name-based heuristic (e.g. "Baldwin County" AL, "of Houston" implies TX
# via the city map below). Keep small/targeted -- full 50-state name lists
# create false positives against generic English words, so we pair
# abbreviations with a handful of well-known non-FL city/region names that
# show up in restoration-franchise "of <City>" naming.
_NON_FL_STATE_ABBREVS = {
    "AL", "GA", "MS", "LA", "TX", "TN", "SC", "NC", "VA", "OH", "NY",
    "AZ", "CA", "IL", "MN", "NJ", "NV", "OK",
    "WA", "WI",
}
# Deliberately excluded: CO/IN/OR/PA/MI/MO/ME -- collide with common English
# words/abbreviations ("Co" = Company, "In"/"Or" = prepositions, "Pa"/"Mi"/"Mo"
# = informal words) and would false-positive on ordinary business names.

# Known non-FL city/region names that appear in "<Franchise> of <Place>"
# naming and would otherwise look like a plain place name. Deliberately
# small and specific -- extend as new false negatives are found.
_NON_FL_PLACE_NAMES = {
    "MAHONING VALLEY",  # Ohio (Youngstown area)
    "HOUSTON",
    "BALDWIN COUNTY",  # Alabama
}

# FL city/region names that could be mistaken for out-of-state names if a
# naive substring check were used (e.g. "Miami" alone is fine -- it's FL).
_FL_PLACE_NAMES = {
    "MIAMI", "SANTA ROSA BEACH", "ORLANDO", "TAMPA", "JACKSONVILLE",
    "PENSACOLA", "TALLAHASSEE",
}


def _load_region_config() -> dict:
    with open(_REGION_CONFIG_PATH) as f:
        return yaml.safe_load(f)


_REGION_CONFIG = None


def _region_config() -> dict:
    global _REGION_CONFIG
    if _REGION_CONFIG is None:
        _REGION_CONFIG = _load_region_config()
    return _REGION_CONFIG


def target_area_codes() -> list[str]:
    return list(_region_config().get("target_area_codes", []))


def has_street_address(address: str | None) -> bool:
    """True if `address` looks like a real street address (number + street
    name), not just a city/state/zip fragment or empty/None."""
    if not address or not address.strip():
        return False
    first_segment = address.split(",")[0].strip()
    return bool(_STREET_ADDRESS_RE.match(first_segment))


def classify_location_source(address: str | None, has_addr: bool) -> str:
    """'gbp_address' if has_addr; 'grid_centroid' if address exists but is
    just city-level (no street number); 'unknown' if no address data at
    all."""
    if has_addr:
        return "gbp_address"
    if address and address.strip():
        return "grid_centroid"
    return "unknown"


def _extract_area_code(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) >= 10:
        return digits[:3]
    return None


def _name_signals_other_state(name: str | None) -> bool:
    if not name:
        return False
    upper = name.upper()
    for place in _NON_FL_PLACE_NAMES:
        if place in upper:
            return True
    # State abbreviation as a whole word (avoid matching inside other words).
    for abbrev in _NON_FL_STATE_ABBREVS:
        if re.search(rf"\b{abbrev}\b", upper):
            return True
    return False


def _website_signals_other_state(website: str | None) -> bool:
    if not website:
        return False
    lower = website.lower()
    for abbrev in _NON_FL_STATE_ABBREVS:
        # URL path segment tell, e.g. '-al/' or '/al-' or trailing '-al'.
        if re.search(rf"[-/]{abbrev.lower()}(?:[-/]|$)", lower):
            return True
    return False


def is_out_of_area(
    name: str | None,
    website: str | None,
    phone: str | None,
    address: str | None,
    target_state: str = "FL",
) -> bool:
    """True if any signal indicates this listing is genuinely out of
    `target_state` (not merely out of the north/central FL sweep sub-region
    -- that's a separate out_of_target_region concern):

    (a) business name contains a recognizable non-target-state city/region
        or state abbreviation/name,
    (b) website URL path/slug contains another state's abbreviation,
    (c) phone area code is not in the configured target list AND the row
        has no real street address (an out-of-area area code paired with a
        real in-area street address is fine -- people keep old numbers).
    """
    if _name_signals_other_state(name):
        return True
    if _website_signals_other_state(website):
        return True

    addr_present = has_street_address(address)
    area_code = _extract_area_code(phone)
    if area_code and area_code not in target_area_codes() and not addr_present:
        return True

    return False
