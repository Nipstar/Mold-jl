"""Maps-first sweep + DBPR reverse cross-reference (see docs/superpowers/
specs/2026-08-28-maps-first-north-florida-design.md).

Why: the old pipeline (stage2_places.py) is DBPR-first -- it sweeps Maps only
to enrich/verify companies that already exist in the `companies` (DBPR)
table. That undercounts real businesses because DBPR licenses individuals,
not businesses. This module flips the direction: sweep Maps for every
(city, trade) pair in scope, capture every listing into `maps_companies`
(one row per unique place_id, becomes the primary lead unit), and only THEN
attempt an address match back against the existing DBPR `companies` table as
a bonus verification layer -- match or no match, the maps_companies row is
kept either way.

Sweep mechanics (city/trade caching, provider, cost) are unchanged from
stage2_places.py -- reuses sweep_city_trade() so a (provider, city,
trade_query) sweep already cached under the old DBPR-first run (e.g. the 5
Duval cities) is a pure cache hit here too, no new spend.

Dedup: by place_id, across all 8 TRADE_QUERIES category sweeps within a
city. A listing surfaced under both "mold remediation" and "water damage
restoration" gets one row with both categories recorded in
`categories`/`source_sweeps` (comma-joined).

DBPR cross-reference: reuses matching.address_match against `companies` rows
filtered to the same city/county (reversed lookup direction from
stage2_places.match_company, but the same normalize/fuzzy logic). On match:
license_verified=1, matched_license_number, matched_principal_name,
match_confidence='high'. No match: license_verified=0 -- expected, not a
failure.

NOT executed against real data by this change (build-only task) -- a
separate task runs sweep_and_ingest() against config.NORTH_FLORIDA_COUNTIES.
"""
from __future__ import annotations

import json

from .. import config, db
from ..matching import address_match
from .. import stage2_places
from ..stage2_places import TRADE_QUERIES, get_city_listings, sweep_city_trade  # noqa: F401 (re-exported for callers)


def _franchise_flag(name: str | None) -> int:
    upper = (name or "").upper()
    return int(any(f in upper for f in config.FRANCHISE_NAMES))


def _city_from_address(address: str | None, fallback: str) -> str:
    """DataForSEO Maps listings carry no separate city field -- only a full
    address string, e.g. '1514 Roberts Dr Ste 2, Jacksonville Beach, FL
    32250'. A sweep of city X routinely surfaces nearby-city listings too
    (e.g. sweeping 'Neptune Beach' returns Jacksonville Beach results), so
    the swept city is NOT a reliable city value for storage or for the DBPR
    cross-reference -- parse the real city out of the address's second
    comma-segment instead, falling back to the swept city only when the
    address doesn't parse."""
    if address and address.count(",") >= 2:
        segment = address.split(",")[1].strip()
        if segment:
            return segment.upper()
    return fallback


# --- county re-derivation --------------------------------------------------
# BUG (fixed here): the old code tagged every listing from a sweep with the
# sweep's TARGET county, not the listing's real location. For small/rural
# target cities (e.g. Fountain, Calhoun county) DataForSEO's Maps search
# often returns loosely-related statewide filler results when local density
# near the exact town is low (Tampa/Miami/Orlando/Fort Myers companies
# showing up for a "Fountain FL mold remediation" query) -- those were
# getting mislabeled county=Calhoun. Fix: derive county from the REAL parsed
# city (via _city_from_address), not the swept county, and drop listings
# that can't be reasonably placed near the swept county at all.

# Small supplementary hardcoded list for cities that show up in DataForSEO
# addresses but aren't well represented in the DBPR companies table (gaps in
# the primary lookup). Keys are upper-cased city names.
_SUPPLEMENTAL_CITY_COUNTY = {
    "FOUNTAIN": "Bay",
    "BLOUNTSTOWN": "Calhoun",
    "ALTHA": "Calhoun",
    "CLARKSVILLE": "Calhoun",
    "MARIANNA": "Jackson",
    "PORT ST JOE": "Gulf",
    "PORT ST. JOE": "Gulf",
    "WEWAHITCHKA": "Gulf",
    "APALACHICOLA": "Franklin",
    "CARRABELLE": "Franklin",
    "PANAMA CITY": "Bay",
    "PANAMA CITY BEACH": "Bay",
    "LYNN HAVEN": "Bay",
    "SPRINGFIELD": "Bay",
    "CALLAWAY": "Bay",
    "YOUNGSTOWN": "Bay",
}

# Minimal neighbor table for the North Florida counties in scope (config.
# NORTH_FLORIDA_COUNTIES) -- used by the sanity filter so a listing in an
# immediately adjacent county isn't dropped as a false positive (a sweep
# targeting a border town legitimately surfaces neighboring-county
# businesses). Not exhaustive statewide -- extend if sweeps move outside
# this list.
_COUNTY_ADJACENCY = {
    "Escambia": {"Santa Rosa"},
    "Santa Rosa": {"Escambia", "Okaloosa"},
    "Okaloosa": {"Santa Rosa", "Walton"},
    "Walton": {"Okaloosa", "Holmes", "Washington", "Bay"},
    "Holmes": {"Walton", "Washington", "Jackson"},
    "Washington": {"Holmes", "Walton", "Bay", "Jackson", "Calhoun"},
    "Bay": {"Walton", "Washington", "Calhoun", "Gulf"},
    "Jackson": {"Holmes", "Washington", "Calhoun"},
    "Calhoun": {"Jackson", "Washington", "Bay", "Gulf", "Liberty", "Gadsden"},
    "Gulf": {"Bay", "Calhoun", "Franklin"},
    "Gadsden": {"Jackson", "Calhoun", "Liberty", "Leon"},
    "Liberty": {"Calhoun", "Gadsden", "Franklin", "Wakulla", "Leon"},
    "Franklin": {"Gulf", "Liberty", "Wakulla"},
    "Leon": {"Gadsden", "Liberty", "Wakulla", "Jefferson"},
    "Wakulla": {"Liberty", "Franklin", "Leon", "Jefferson"},
    "Jefferson": {"Leon", "Wakulla", "Madison", "Taylor"},
    "Madison": {"Jefferson", "Taylor", "Hamilton", "Suwannee"},
    "Taylor": {"Jefferson", "Madison", "Lafayette", "Dixie"},
    "Hamilton": {"Madison", "Suwannee", "Columbia"},
    "Suwannee": {"Hamilton", "Madison", "Lafayette", "Columbia"},
    "Lafayette": {"Suwannee", "Taylor", "Dixie", "Gilchrist", "Columbia"},
    "Dixie": {"Taylor", "Lafayette", "Gilchrist", "Levy"},
    "Columbia": {"Hamilton", "Suwannee", "Lafayette", "Baker", "Union", "Alachua"},
    "Baker": {"Columbia", "Nassau", "Duval", "Union", "Bradford"},
    "Nassau": {"Baker", "Duval"},
    "Duval": {"Nassau", "Baker", "Clay", "St. Johns"},
    "Union": {"Baker", "Columbia", "Bradford", "Alachua"},
    "Bradford": {"Baker", "Union", "Alachua", "Clay"},
    "Clay": {"Duval", "Bradford", "Putnam", "St. Johns"},
    "St. Johns": {"Duval", "Clay", "Putnam", "Flagler"},
    "Putnam": {"Clay", "St. Johns", "Bradford", "Alachua", "Marion", "Flagler"},
    "Alachua": {"Columbia", "Union", "Bradford", "Putnam", "Gilchrist", "Levy", "Marion"},
    "Gilchrist": {"Lafayette", "Dixie", "Alachua", "Levy"},
    "Levy": {"Dixie", "Gilchrist", "Alachua", "Marion", "Citrus"},
}


def _load_city_county_lookup(conn) -> dict[str, str]:
    """Primary lookup: city -> county, built from the existing DBPR
    `companies` table (~90%+ of FL cities already mapped there). Picks the
    most frequent non-blank county per city in case of noisy duplicates."""
    rows = conn.execute(
        "SELECT upper(trim(city)) AS city, county, COUNT(*) AS n "
        "FROM companies "
        "WHERE city IS NOT NULL AND trim(city) != '' "
        "AND county IS NOT NULL AND trim(county) != '' AND county != 'Out of State' "
        "GROUP BY upper(trim(city)), county"
    ).fetchall()
    best: dict[str, tuple[str, int]] = {}
    for row in rows:
        city, county, n = row["city"], row["county"], row["n"]
        if city not in best or n > best[city][1]:
            best[city] = (county, n)
    return {city: county for city, (county, _n) in best.items()}


def _resolve_real_county(lookup: dict[str, str], real_city: str, swept_county: str | None) -> str | None:
    """Resolve the real county for `real_city`: DBPR-derived `lookup` table
    first, then the small supplemental hardcoded list, falling back to the
    swept county only if the city can't be resolved at all (better than None
    for downstream code, but the sanity filter still gets a shot at it)."""
    key = (real_city or "").strip().upper()
    return lookup.get(key) or _SUPPLEMENTAL_CITY_COUNTY.get(key) or swept_county


def _near_swept_county(real_county: str | None, swept_county: str | None) -> bool:
    """Sanity check: is `real_county` at or near `swept_county`? True if
    they're the same county or adjacent per _COUNTY_ADJACENCY. False (=drop)
    if the listing's real location can't reasonably be tied to the sweep
    target -- e.g. a Tampa (Hillsborough) listing surfaced by a Fountain
    (Calhoun) sweep."""
    if not swept_county:
        return True  # no target county to sanity-check against -- keep
    if not real_county:
        return False  # couldn't resolve the listing's real county at all
    if real_county.strip().lower() == swept_county.strip().lower():
        return True
    return real_county in _COUNTY_ADJACENCY.get(swept_county, set())


def sweep_city(conn, city: str, county: str | None = None, provider: str = "dataforseo",
               trade_queries: list[str] | None = None) -> list[dict]:
    """Sweep all trade categories for `city`, dedupe by place_id, upsert each
    unique listing into maps_companies. Returns the list of maps_companies
    row dicts touched (post-upsert)."""
    trade_queries = trade_queries or TRADE_QUERIES
    merged: dict[str, dict] = {}       # place_id -> listing
    sources: dict[str, set[str]] = {}  # place_id -> set of trade queries that surfaced it

    for trade_query in trade_queries:
        data = sweep_city_trade(conn, city, trade_query, provider=provider)
        for item in data.get("local_results") or []:
            place_id = item.get("place_id")
            if not place_id:
                continue  # maps_companies.place_id is the dedup/identity key -- skip listings without one
            if place_id not in merged:
                merged[place_id] = item
                sources[place_id] = set()
            sources[place_id].add(trade_query)

    city_county_lookup = _load_city_county_lookup(conn)
    touched = []
    dropped = 0
    for place_id, listing in merged.items():
        categories = ",".join(sorted(sources[place_id]))
        hours = listing.get("hours")
        real_city = _city_from_address(listing.get("address"), city)
        real_county = _resolve_real_county(city_county_lookup, real_city, county)

        if not _near_swept_county(real_county, county):
            dropped += 1
            continue  # e.g. a Tampa listing surfaced by a Fountain/Calhoun sweep -- mislabeled, drop

        fields = dict(
            name=listing.get("title"),
            address=listing.get("address"),
            city=real_city,
            county=real_county,
            zip=listing.get("zip") or listing.get("zip_code"),
            phone=listing.get("phone"),
            website=listing.get("website"),
            rating=listing.get("rating"),
            review_count=listing.get("reviews"),
            categories=categories,
            business_status=_business_status(listing),
            hours_json=json.dumps(hours) if hours else None,
            franchise_flag=_franchise_flag(listing.get("title")),
            source_sweeps=categories,
        )
        row_id = db.upsert_maps_company(conn, place_id, **fields)
        touched.append({"id": row_id, "place_id": place_id, **fields})

    if dropped:
        print(f"[maps_first] sweep_city({city!r}, county={county!r}): dropped {dropped} of "
              f"{len(merged)} listings as out-of-area (kept {len(touched)})")
    return touched


def _business_status(listing: dict) -> str | None:
    open_state = listing.get("open_state") or ""
    open_state_l = open_state.lower() if isinstance(open_state, str) else ""
    if "permanently closed" in open_state_l:
        return "CLOSED_PERMANENTLY"
    if "temporarily closed" in open_state_l:
        return "CLOSED_TEMPORARILY"
    if open_state or listing.get("hours"):
        return "OPERATIONAL"
    return None


def cross_reference_dbpr(conn, maps_row: dict) -> dict:
    """Reverse of stage2_places.match_company: given a maps_companies row,
    look for an address match among DBPR `companies` in the same city/county.
    Returns the fields to write back (license_verified, matched_*,
    match_confidence) -- does not write to the DB itself."""
    city = (maps_row.get("city") or "").strip()
    county = (maps_row.get("county") or "").strip()
    query = "SELECT * FROM companies WHERE 1=1"
    params: list = []
    if city:
        query += " AND lower(city) = lower(?)"
        params.append(city)
    if county:
        query += " AND lower(county) = lower(?)"
        params.append(county)
    candidates = conn.execute(query, params).fetchall()

    for cand in candidates:
        if address_match(maps_row.get("address"), cand["address"]):
            return {
                "license_verified": 1,
                "matched_license_number": cand["license_number"],
                "matched_principal_name": cand["principal_name"] or cand["licensee_name"],
                "match_confidence": "high",
            }
    return {
        "license_verified": 0,
        "matched_license_number": None,
        "matched_principal_name": None,
        "match_confidence": "none",
    }


def sweep_and_ingest(cities: list[tuple[str, str]], provider: str = "dataforseo") -> dict:
    """Full pipeline for a list of (city, county) pairs: sweep all 8 trade
    categories per city, dedupe/upsert into maps_companies, then cross-
    reference each touched row against DBPR `companies`.

    `cities` is caller-supplied (e.g. from config.NORTH_FLORIDA_COUNTIES +
    the existing DBPR city list, per the spec) -- this module doesn't decide
    which cities are in scope."""
    conn = db.get_connection()
    db.run_maps_companies_migration(conn)

    swept = 0
    verified = 0
    for city, county in cities:
        rows = sweep_city(conn, city, county=county, provider=provider)
        for row in rows:
            xref = cross_reference_dbpr(conn, row)
            db.upsert_maps_company(conn, row["place_id"], **xref)
            swept += 1
            if xref["license_verified"]:
                verified += 1

    conn.close()
    summary = {
        "cities_processed": len(cities),
        "listings_swept": swept,
        "license_verified": verified,
        "sweep_stats": dict(stage2_places.SWEEP_STATS),
    }
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Maps-first sweep + DBPR cross-reference (build-only until wired up by a follow-on task)")
    p.add_argument("--city", type=str, help="single city smoke test, e.g. 'Jacksonville'")
    p.add_argument("--county", type=str, default="Duval")
    p.add_argument("--provider", type=str, default="dataforseo", choices=["dataforseo", "serpapi"])
    a = p.parse_args()
    if a.city:
        sweep_and_ingest([(a.city, a.county)], provider=a.provider)
    else:
        print("Pass --city for a smoke test. Full North Florida run is a separate task.")
