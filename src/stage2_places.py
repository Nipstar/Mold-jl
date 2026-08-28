"""Stage 2 -- geo/trade sweep + address match (replaces the old per-company
name-search matcher, now kept at stage2_places_legacy.py for comparison).

Why: searching per-company ("{dba_name} {city} FL") means one paid API call
per row and is prone to false positives on common/short business names (see
the Duval pilot notes in stage2_places_legacy.py). Instead, sweep each
(city, trade) pair ONCE -- 'mold remediation' and 'mold assessment' -- via
DataForSEO Business Data / Google Maps search, cache the full listing set for
that city, and match every DBPR company in that city against the cached
listings by ADDRESS first (high confidence), falling back to fuzzy DBA-name
match against the same listing set (medium confidence) only when no address
match is found. This is both cheaper (N cities x 2 trades, not N companies)
and more reliable (address match doesn't care what the listing is titled).

Provider: DataForSEO Maps SERP (src/clients/dataforseo.py) is primary.
src/clients/serpapi.py (google_maps engine) is available as a fallback if a
DataForSEO sweep call fails outright and SERPAPI_KEY is configured -- flip
SWEEP_PROVIDER to 'serpapi' or pass provider='serpapi' to force it.

Caching: every raw sweep response is cached in api_cache, keyed by a hash of
(provider, city, trade_query) -- source='dataforseo_maps_sweep' or
'serpapi_maps_sweep'. A given city+trade is swept AT MOST ONCE ever; every
company in that city reuses the same cached listing set.

Matching (src/matching.py):
  1. Normalize the company's address (street number + name, suite/unit
     stripped, suffix words collapsed -- 'St' == 'Street'). Compare against
     every listing's normalized address from that city's sweep. Match (exact
     after normalization, or fuzzy ratio >= 90) => match_confidence='high',
     match_source='address_sweep'.
  2. No address match: if the row's dba_name is present and is NOT a
     person-name-in-dba-field (DBPR quirk -- 'LASTNAME, FIRSTNAME' with no
     business-suffix word, see matching.looks_like_person_name), fuzzy-match
     it (token_set_ratio) against listing titles in the same city's sweep.
     Score >= 85 => match_confidence='medium', match_source='name_fuzzy_sweep'.
  3. Neither => place_found=false, match_confidence='none'.

Person-name dba_name rows never get a name-fallback search of their own --
they rely solely on address match, same as any other row, since their
"business name" is actually a person's name.

Writes results onto the same `companies` enrichment columns the old matcher
used (see db.ENRICH_COLUMNS) -- schema unchanged, only how it's populated.

NOT executed by this change -- see stage2_places_legacy.py's TOP_COUNTIES
comment for county ordering; a separate task runs this against real data.
"""
from __future__ import annotations

import hashlib
import json
import time

from . import db
from .clients.dataforseo import DataForSEOClient, DataForSEOError, get_maps_results as dfs_get_maps_results
from .matching import address_match, looks_like_person_name, name_fuzzy_score

TRADE_QUERIES = [
    "mold remediation", "mold assessment", "mold inspection", "mold testing",
    "water damage restoration", "restoration company",
    "fire and water damage restoration", "disaster restoration company",
]

TOP_COUNTIES = ["Dade", "Miami-Dade", "Broward", "Palm Beach", "Hillsborough", "Orange", "Duval"]

NAME_MATCH_THRESHOLD = 85
ADDRESS_MATCH_THRESHOLD = 90

# Sweeps are per (city, trade), not per company -- expect this to stay tiny
# even statewide (a few hundred cities x 2 trades) vs. tens of thousands of
# per-company calls under the old approach.
SWEEP_STATS = {"sweeps_called": 0, "sweeps_cached": 0, "sweeps_failed": 0}
PATH_COUNTS = {"address_match": 0, "name_fuzzy_match": 0, "no_match": 0, "no_dba_available": 0}
COST = {"dataforseo_maps_sweep": 0.003, "serpapi_maps_sweep": 0.015}

_DFS_CLIENT: DataForSEOClient | None = None
_SERPAPI_CLIENT = None  # lazy import, only if fallback is actually used


def _dfs_client() -> DataForSEOClient:
    global _DFS_CLIENT
    if _DFS_CLIENT is None:
        _DFS_CLIENT = DataForSEOClient()
    return _DFS_CLIENT


def _serpapi_client():
    global _SERPAPI_CLIENT
    if _SERPAPI_CLIENT is None:
        from .clients.serpapi import SerpAPIClient
        _SERPAPI_CLIENT = SerpAPIClient()
    return _SERPAPI_CLIENT


def _hash_key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def _cache_get_json(conn, key):
    raw = db.cache_get(conn, key)
    return json.loads(raw) if raw else None


def _cache_put_json(conn, key, source, obj):
    db.cache_put(conn, key, source, json.dumps(obj))


def _sweep_cache_key(provider: str, city: str, trade_query: str) -> str:
    return _hash_key(provider, (city or "").strip().lower(), (trade_query or "").strip().lower())


def sweep_city_trade(conn, city: str, trade_query: str, provider: str = "dataforseo") -> dict:
    """One sweep of `trade_query` in `city`, cached forever under
    (city, trade_query, provider). Never re-sweeps a cached key."""
    source = f"{provider}_maps_sweep"
    key = _sweep_cache_key(provider, city, trade_query)
    cached = _cache_get_json(conn, key)
    if cached is not None:
        SWEEP_STATS["sweeps_cached"] += 1
        return cached

    query = f"{trade_query} {city} FL"
    try:
        if provider == "dataforseo":
            client = _dfs_client()
            if not client.configured:
                data = {"local_results": [], "error": "not_configured"}
            else:
                data = dfs_get_maps_results(client, query)
                SWEEP_STATS["sweeps_called"] += 1
        elif provider == "serpapi":
            from .clients.serpapi import get_maps_results as serpapi_get_maps_results
            client = _serpapi_client()
            if not client.configured:
                data = {"local_results": [], "error": "not_configured"}
            else:
                data = serpapi_get_maps_results(client, query)
                SWEEP_STATS["sweeps_called"] += 1
        else:
            raise ValueError(f"unknown provider: {provider}")
    except DataForSEOError as e:
        SWEEP_STATS["sweeps_failed"] += 1
        data = {"error": str(e), "local_results": []}
    except Exception as e:  # noqa: BLE001 -- any provider error still gets cached as a miss
        SWEEP_STATS["sweeps_failed"] += 1
        data = {"error": str(e), "local_results": []}

    _cache_put_json(conn, key, source, data)
    return data


def get_city_listings(conn, city: str, trade_queries: list[str] | None = None,
                       provider: str = "dataforseo") -> list[dict]:
    """All listings for `city` across every trade query, deduped by place_id
    (falling back to title+address when place_id is missing)."""
    trade_queries = trade_queries or TRADE_QUERIES
    merged: dict[str, dict] = {}
    for trade_query in trade_queries:
        data = sweep_city_trade(conn, city, trade_query, provider=provider)
        for item in data.get("local_results") or []:
            dedupe_key = item.get("place_id") or f"{item.get('title')}|{item.get('address')}"
            if dedupe_key not in merged:
                merged[dedupe_key] = item
    return list(merged.values())


def cities_for_counties(conn, counties: list[str] | None = None) -> list[str]:
    """Distinct, non-empty cities among companies in `counties` (or all
    companies if counties is falsy)."""
    rows = conn.execute("SELECT DISTINCT city, county FROM companies").fetchall()
    wanted = {c.lower() for c in counties} if counties else None
    cities = set()
    for r in rows:
        if wanted is not None and (r["county"] or "").lower() not in wanted:
            continue
        city = (r["city"] or "").strip()
        if city:
            cities.add(city)
    return sorted(cities)


def _empty_result() -> dict:
    return {
        "match_confidence": "none", "match_source": None, "place_id": None,
        "places_rating": None, "places_review_count": None, "places_website": None,
        "places_phone": None, "places_hours_json": None, "places_types": None,
        "business_status": None, "in_local_pack": 0, "latest_review_age_months": None,
        "place_found": 0,
    }


def _result_from_listing(listing: dict, confidence: str, source: str) -> dict:
    result = _empty_result()
    result["match_confidence"] = confidence
    result["match_source"] = source
    result["place_id"] = listing.get("place_id")
    result["places_rating"] = listing.get("rating")
    result["places_review_count"] = listing.get("reviews")
    result["places_website"] = listing.get("website")
    result["places_phone"] = listing.get("phone")
    hours = listing.get("hours")
    result["places_hours_json"] = json.dumps(hours) if hours else None
    result["places_types"] = ",".join(listing.get("types") or [])
    result["place_found"] = 1
    open_state = (listing.get("open_state") or "")
    open_state_l = open_state.lower() if isinstance(open_state, str) else ""
    if "permanently closed" in open_state_l:
        result["business_status"] = "CLOSED_PERMANENTLY"
    elif "temporarily closed" in open_state_l:
        result["business_status"] = "CLOSED_TEMPORARILY"
    elif open_state or listing.get("hours"):
        result["business_status"] = "OPERATIONAL"
    return result


def match_company(row, listings: list[dict]) -> dict:
    """Address-match first, DBA fuzzy-name fallback second, else no match.
    Pure function over a pre-fetched listing set -- no API/DB calls here."""
    address = row["address"]
    dba = (row["dba_name"] or "").strip()

    for listing in listings:
        if address_match(address, listing.get("address"), threshold=ADDRESS_MATCH_THRESHOLD):
            PATH_COUNTS["address_match"] += 1
            return _result_from_listing(listing, "high", "address_sweep")

    if not dba or looks_like_person_name(dba):
        # DBPR quirk: dba_name is actually a person's name ("LASTNAME,
        # FIRSTNAME") or missing entirely -- never name-search this, address
        # match above is the only signal available.
        PATH_COUNTS["no_dba_available"] += 1
        return _empty_result()

    best_listing = None
    best_score = 0
    for listing in listings:
        score = name_fuzzy_score(dba, listing.get("title"))
        if score > best_score:
            best_score, best_listing = score, listing

    if best_listing and best_score >= NAME_MATCH_THRESHOLD:
        PATH_COUNTS["name_fuzzy_match"] += 1
        return _result_from_listing(best_listing, "medium", "name_fuzzy_sweep")

    PATH_COUNTS["no_match"] += 1
    return _empty_result()


def run(limit: int | None = None, sleep: float = 0.15, counties: list[str] | None = None,
        provider: str = "dataforseo") -> dict:
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

    # Sweep every distinct city up front so the matching loop below never
    # triggers a sweep mid-company -- keeps the per-company work pure/cheap.
    cities = sorted({(r["city"] or "").strip() for r in rows if (r["city"] or "").strip()})
    city_listings: dict[str, list[dict]] = {}
    for city in cities:
        city_listings[city] = get_city_listings(conn, city, provider=provider)
        time.sleep(sleep)

    matched = 0
    processed = 0
    for row in rows:
        city = (row["city"] or "").strip()
        listings = city_listings.get(city, [])
        res = match_company(row, listings)
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
                  f"paths={PATH_COUNTS}, sweeps={SWEEP_STATS}")

    conn.close()
    total_spend = SWEEP_STATS["sweeps_called"] * COST.get(f"{provider}_maps_sweep", 0)
    summary = {
        "processed": processed, "matched": matched,
        "match_rate": round(matched / processed, 4) if processed else 0,
        "cities_swept": len(cities),
        "path_counts": dict(PATH_COUNTS),
        "sweep_stats": dict(SWEEP_STATS),
        "spend_usd": round(total_spend, 2),
    }
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--sleep", type=float, default=0.15, help="delay between city sweeps, seconds")
    p.add_argument("--counties", type=str, default=None, help="comma-separated county filter")
    p.add_argument("--provider", type=str, default="dataforseo", choices=["dataforseo", "serpapi"])
    a = p.parse_args()
    run(limit=a.limit, sleep=a.sleep,
        counties=a.counties.split(",") if a.counties else None,
        provider=a.provider)
