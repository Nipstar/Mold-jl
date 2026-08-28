"""LEGACY (kept for comparison only, not run by default) -- per-company
name-search matcher. Replaced by the geo/trade sweep + address-match approach
in stage2_places.py (see that module's docstring for why: per-company name
search is expensive -- one paid call per company -- and prone to false
positives on common names; a sweep-based approach caches one call per
city/trade pair and matches by address, which is far more reliable and far
cheaper). Do not delete -- useful for side-by-side accuracy comparison.

Stage 2 -- DataForSEO Maps enrichment (switched from SerpAPI/Google Places
after the Duval pilot showed name-only fuzzy search against individual
licensee names (no DBA) produced false-positive matches -- e.g. matching a
UCLA physician's name to an unrelated business, since 111/112 Duval rows had
no dba_name and the old code fell back to principal_name).

SOLE SOURCE: DataForSEO Google Maps search (src/clients/dataforseo.py), one
search per company that actually has a DBA name ('{dba_name} {city} FL').
Google Places (New) Text Search / Place Details are NOT called.

Matcher fix (post-Duval-pilot):
  - dba_name present -> search '{dba_name} {city} FL'. Accept a candidate
    only if fuzzy match (rapidfuzz token_set_ratio >= 85) AND the row's
    city/county appears in the candidate's address. Both required -- do not
    weaken either.
  - dba_name empty (individual licensee, no business name) -> never search
    on principal_name. Default to place_found=false, match_confidence=
    'no_dba_skipped', zero API spend. These fall out as tier3 in scoring
    (stage4_score.py's has_presence check treats 'no_dba_skipped' as no
    presence) -- matches the spec's intent that no discoverable business
    presence means tier3.

Order: top counties first (Miami-Dade/Dade, Broward, Palm Beach, Hillsborough,
Orange, Duval) then remaining counties statewide. Caches every raw response in
api_cache keyed by a hash of (source, query), source='dataforseo_maps'. Never
re-spends quota on reruns -- always checks cache first.

NOTE on review recency: the Maps search listing does not return per-review
timestamps -- only aggregate rating + review count. latest_review_age_months
is therefore always null under this source; flagged in pilot report.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

from rapidfuzz import fuzz

from . import db
from .clients.dataforseo import DataForSEOClient, DataForSEOError, get_maps_results

# DBPR sometimes populates dba_name with a reformatted person name
# ("LASTNAME, FIRSTNAME MIDDLE") for sole-proprietor licenses instead of
# leaving it blank -- same unreliable-search problem as falling back to
# principal_name. Route these to the no_dba_skipped path too.
_PERSON_NAME_RE = re.compile(r"^[A-Z][A-Za-z'\-]*(?:\s[A-Z][A-Za-z'\-]*)*,\s*[A-Z][A-Za-z'\-]*(?:\s[A-Z][A-Za-z'\-]*)*$")
_BUSINESS_WORDS = ("LLC", "INC", "CORP", "CO", "SERVICES", "SERVICE", "RESTORATION",
                    "REMEDIATION", "MOLD", "GROUP", "SOLUTIONS", "ENTERPRISES",
                    "CONTRACTING", "CONSTRUCTION", "INSPECTIONS", "INSPECTION")


def _looks_like_person_name(name: str) -> bool:
    if not _PERSON_NAME_RE.match(name):
        return False
    upper = name.upper()
    return not any(w in upper for w in _BUSINESS_WORDS)

TOP_COUNTIES = ["Dade", "Miami-Dade", "Broward", "Palm Beach", "Hillsborough", "Orange", "Duval"]

# dba_search = real DataForSEO call made (dba_name present); no_dba_skipped =
# row skipped entirely, zero spend. Tracked separately per the report spec.
SPEND = {"dataforseo_maps": 0}
PATH_COUNTS = {"dba_search": 0, "no_dba_skipped": 0}
COST = {"dataforseo_maps": 0.003}  # live advanced, standard priority

_CLIENT: DataForSEOClient | None = None


def _client() -> DataForSEOClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = DataForSEOClient()
    return _CLIENT


def _hash_key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def _cache_get_json(conn, key):
    raw = db.cache_get(conn, key)
    return json.loads(raw) if raw else None


def _cache_put_json(conn, key, source, obj):
    db.cache_put(conn, key, source, json.dumps(obj))


def dataforseo_maps_search(conn, query: str) -> dict | None:
    key = _hash_key("dataforseo_maps", query)
    cached = _cache_get_json(conn, key)
    if cached is not None:
        return cached
    client = _client()
    if not client.configured:
        return None
    try:
        data = get_maps_results(client, query)
        SPEND["dataforseo_maps"] += 1
    except DataForSEOError as e:
        data = {"error": str(e), "local_results": []}
    _cache_put_json(conn, key, "dataforseo_maps", data)
    return data


def _name_for_match(row) -> str:
    """Compat shim for populate_enrichment_duval.py's cache-key rebuild --
    the name actually searched on for a given row (empty for skipped rows)."""
    dba = (row["dba_name"] or "").strip()
    if not dba or _looks_like_person_name(dba):
        return ""
    return dba


def _confidence(name_score: int, city_match: bool) -> str:
    if name_score >= 92 and city_match:
        return "high"
    if name_score >= 85 and city_match:
        return "medium"
    return "low"


def enrich_company(conn, row) -> dict:
    dba = (row["dba_name"] or "").strip()
    city = (row["city"] or "").strip()
    county = (row["county"] or "").strip()
    result = {"match_confidence": None, "match_source": None, "place_id": None,
              "places_rating": None, "places_review_count": None, "places_website": None,
              "places_phone": None, "places_hours_json": None, "places_types": None,
              "business_status": None, "in_local_pack": 0, "latest_review_age_months": None,
              "place_found": 0}

    if not dba or _looks_like_person_name(dba):
        # Individual licensee, no real business name -- do not force a
        # name-only Maps search against a person's name (unreliable,
        # false-positive prone). Skip entirely, zero spend.
        PATH_COUNTS["no_dba_skipped"] += 1
        result["match_confidence"] = "no_dba_skipped"
        result["place_found"] = 0
        return result

    PATH_COUNTS["dba_search"] += 1
    query = f"{dba} {city} FL"
    sm = dataforseo_maps_search(conn, query)
    if sm and sm.get("local_results"):
        best = None
        best_score = 0
        for p in sm["local_results"][:10]:
            pname = p.get("title", "")
            score = fuzz.token_set_ratio(dba.lower(), pname.lower())
            if score > best_score:
                best_score, best = score, p
        location_match = bool(best) and (
            (city and city.lower() in (best.get("address", "") or "").lower())
            or (county and county.lower() in (best.get("address", "") or "").lower())
        )
        if best and best_score >= 85 and location_match:
            result["match_confidence"] = _confidence(best_score, location_match)
            result["match_source"] = "dataforseo_maps"
            result["place_id"] = best.get("place_id")
            result["places_rating"] = best.get("rating")
            result["places_review_count"] = best.get("reviews")
            result["places_website"] = best.get("website")
            result["places_phone"] = best.get("phone")
            hours = best.get("hours")
            result["places_hours_json"] = json.dumps(hours) if hours else None
            result["places_types"] = ",".join(best.get("types", []) or [])
            result["place_found"] = 1
            open_state = (best.get("open_state") or "").lower()
            if "closed permanently" in open_state or "permanently closed" in open_state:
                result["business_status"] = "CLOSED_PERMANENTLY"
            elif "temporarily closed" in open_state:
                result["business_status"] = "CLOSED_TEMPORARILY"
            elif best.get("open_state") or best.get("hours"):
                result["business_status"] = "OPERATIONAL"

    return result


def run(limit: int | None = None, sleep: float = 0.15, counties: list[str] | None = None) -> dict:
    conn = db.get_connection()
    db.run_enrich_migrations(conn)

    def county_rank(county):
        c = (county or "").lower()
        for i, tc in enumerate(TOP_COUNTIES):
            if tc.lower() in c:
                return i
        return 99

    rows = conn.execute("SELECT * FROM companies ORDER BY id").fetchall()
    if counties:
        wanted = [c.lower() for c in counties]
        rows = [r for r in rows if (r["county"] or "").lower() in wanted]
    rows = sorted(rows, key=lambda r: county_rank(r["county"]))
    if limit:
        rows = rows[:limit]

    matched = 0
    processed = 0
    for row in rows:
        res = enrich_company(conn, row)
        conn.execute(
            """UPDATE companies SET place_id=?, match_confidence=?, match_source=?,
               places_rating=?, places_review_count=?, places_website=?, places_phone=?,
               places_hours_json=?, places_types=?, business_status=?, in_local_pack=?,
               latest_review_age_months=?, place_found=?, enrich_stage=2 WHERE id=?""",
            (res["place_id"], res["match_confidence"], res["match_source"], res["places_rating"],
             res["places_review_count"], res["places_website"], res["places_phone"],
             res["places_hours_json"], res["places_types"], res["business_status"],
             res["in_local_pack"], res["latest_review_age_months"], res["place_found"], row["id"]),
        )
        conn.commit()
        processed += 1
        if res["place_found"]:
            matched += 1
        if processed % 200 == 0:
            print(f"stage2: {processed}/{len(rows)} processed, {matched} matched, "
                  f"paths={PATH_COUNTS}, spend={SPEND}")
        time.sleep(sleep)

    conn.close()
    total_spend = sum(SPEND[k] * COST[k] for k in SPEND)
    summary = {"processed": processed, "matched": matched,
               "match_rate": round(matched / processed, 4) if processed else 0,
               "path_counts": dict(PATH_COUNTS),
               "spend_calls": dict(SPEND), "spend_usd": round(total_spend, 2)}
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--sleep", type=float, default=0.15)
    p.add_argument("--counties", type=str, default=None, help="comma-separated county filter")
    a = p.parse_args()
    run(limit=a.limit, sleep=a.sleep, counties=a.counties.split(",") if a.counties else None)
