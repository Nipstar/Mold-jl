"""One-off backfill: has_street_address / location_source / out_of_area for
all maps_companies rows (Stage B pipeline hardening). Pure local
computation, no API spend.

Also retroactively corrects city/county: rows with no real street address
had the search-grid-point's city/county silently assigned by the old
maps_first.py logic. This backfill nulls city/county for any row where
has_street_address is false and location_source is grid_centroid, since
that city/county value is not trustworthy.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402
from src.location import classify_location_source, has_street_address, is_out_of_area  # noqa: E402


def main() -> None:
    conn = db.get_connection()
    db.run_location_source_migration(conn)

    rows = conn.execute(
        "SELECT id, name, website, phone, address, city, county FROM maps_companies"
    ).fetchall()

    n_no_street = 0
    n_out_of_area = 0
    n_city_nulled = 0
    out_of_area_samples = []

    for r in rows:
        has_addr = has_street_address(r["address"])
        loc_src = classify_location_source(r["address"], has_addr)
        ooa = is_out_of_area(r["name"], r["website"], r["phone"], r["address"])

        if not has_addr:
            n_no_street += 1
        if ooa:
            n_out_of_area += 1
            if len(out_of_area_samples) < 15:
                out_of_area_samples.append((r["name"], r["city"], r["county"], r["phone"]))

        # Retroactive fix: a grid_centroid-sourced city/county is not
        # trustworthy -- null it out rather than leave fake data in place.
        new_city, new_county = r["city"], r["county"]
        if loc_src == "grid_centroid" and (r["city"] or r["county"]):
            new_city, new_county = None, None
            n_city_nulled += 1

        conn.execute(
            "UPDATE maps_companies SET has_street_address=?, location_source=?, "
            "out_of_area=?, city=?, county=? WHERE id=?",
            (int(has_addr), loc_src, int(ooa), new_city, new_county, r["id"]),
        )

    conn.commit()

    print(f"Total rows: {len(rows)}")
    print(f"has_street_address=False: {n_no_street}")
    print(f"out_of_area=True: {n_out_of_area}")
    print(f"city/county nulled (grid_centroid correction): {n_city_nulled}")
    print("\nSample out_of_area rows:")
    for name, city, county, phone in out_of_area_samples:
        print(f"  {name} | city={city} county={county} phone={phone}")


if __name__ == "__main__":
    main()
