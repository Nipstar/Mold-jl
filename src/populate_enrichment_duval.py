"""One-off: populate enrichment table for Duval county from cached SerpAPI data
and the companies columns stage2_places_legacy.py already wrote (this is the
old per-company matcher's one-off report script; kept pointed at the legacy
module since it reconstructs the legacy cache key). Read-only against
api_cache (no new API calls) -- reuses stage2_places_legacy.enrich_company's
cache-hit path since all 112 Duval companies are now cached.
"""
from __future__ import annotations

import json

from src import db
from src.stage2_places_legacy import enrich_company, _name_for_match


def main():
    conn = db.get_connection()
    db.run_migrations(conn)  # ensures enrichment table exists
    rows = conn.execute("SELECT * FROM companies WHERE county='Duval' ORDER BY id").fetchall()
    conn.execute("DELETE FROM enrichment WHERE company_id IN (SELECT id FROM companies WHERE county='Duval')")

    n_written = 0
    for row in rows:
        res = enrich_company(conn, row)  # cache-only now, no new calls (all cached)
        name = _name_for_match(row)
        city = (row["city"] or "").strip()
        query = f"{name} {city} FL"
        import hashlib
        key = hashlib.sha256(f"serpapi_maps|{query}".encode()).hexdigest()[:24]
        raw = db.cache_get(conn, key)

        place_found = 1 if res["place_id"] else 0
        conn.execute(
            """INSERT INTO enrichment
               (company_id, place_found, rating, review_count, most_recent_review_date,
                categories, website, phone, business_status, hours_listed, in_local_pack,
                match_confidence, source, raw_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (row["id"], place_found, res["places_rating"], res["places_review_count"],
             None,  # SerpAPI google_maps engine has no per-review timestamps -- see stage2_places docstring
             res["places_types"], res["places_website"], res["places_phone"],
             res["business_status"], res["places_hours_json"], res["in_local_pack"],
             res["match_confidence"], res["match_source"] or "serpapi_maps", raw),
        )
        n_written += 1
    conn.commit()

    total = conn.execute("SELECT COUNT(*) c FROM enrichment WHERE company_id IN (SELECT id FROM companies WHERE county='Duval')").fetchone()["c"]
    matched = conn.execute(
        "SELECT COUNT(*) c FROM enrichment e JOIN companies c2 ON e.company_id=c2.id WHERE c2.county='Duval' AND e.place_found=1"
    ).fetchone()["c"]
    with_website = conn.execute(
        "SELECT COUNT(*) c FROM enrichment e JOIN companies c2 ON e.company_id=c2.id WHERE c2.county='Duval' AND e.website IS NOT NULL"
    ).fetchone()["c"]
    with_hours = conn.execute(
        "SELECT COUNT(*) c FROM enrichment e JOIN companies c2 ON e.company_id=c2.id WHERE c2.county='Duval' AND e.hours_listed IS NOT NULL"
    ).fetchone()["c"]
    with_review_date = conn.execute(
        "SELECT COUNT(*) c FROM enrichment e JOIN companies c2 ON e.company_id=c2.id WHERE c2.county='Duval' AND e.most_recent_review_date IS NOT NULL"
    ).fetchone()["c"]

    print(json.dumps({
        "rows_written": n_written,
        "total_in_table": total,
        "place_found": matched,
        "match_rate": round(matched / total, 4) if total else 0,
        "with_website": with_website,
        "with_hours": with_hours,
        "with_review_date": with_review_date,
    }, indent=2))
    conn.close()


if __name__ == "__main__":
    main()
