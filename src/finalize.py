"""Stage I: include_in_outreach final pass.

Computes the single boolean that gates the export contract
(jl-mold-fl-outreach.csv vs jl-mold-fl-excluded.csv):

    include_in_outreach = category_relevant
                           AND NOT (franchise_brand IS NOT NULL)
                           AND NOT multi_location_domain
                           AND NOT lead_mill_suspect
                           AND NOT out_of_area
                           AND NOT is_duplicate

Runs over EVERY maps_companies row -- not just rows that made it through
stage4 scoring -- because excluded rows (out-of-category, franchise, lead
mill, etc.) still need include_in_outreach=false recorded so the excluded
CSV export can find them.
"""
from __future__ import annotations

import json

from . import db


def compute_include_in_outreach(row) -> bool:
    """row supports both sqlite3.Row and dict access (row["col"])."""
    category_relevant = bool(row["category_relevant"])
    has_franchise_brand = row["franchise_brand"] is not None
    multi_location_domain = bool(row["multi_location_domain"])
    lead_mill_suspect = bool(row["lead_mill_suspect"])
    out_of_area = bool(row["out_of_area"])
    is_duplicate = bool(row["is_duplicate"])

    return (
        category_relevant
        and not has_franchise_brand
        and not multi_location_domain
        and not lead_mill_suspect
        and not out_of_area
        and not is_duplicate
    )


def run(conn=None) -> dict:
    own = conn is None
    conn = conn or db.get_connection()
    try:
        db.run_maps_companies_migration(conn)
        db.run_dedup_migration(conn)
        db.run_maps_enrich_migrations(conn)
        db.run_category_relevance_migration(conn)
        db.run_franchise_brand_migration(conn)
        db.run_location_source_migration(conn)
        db.run_include_in_outreach_migration(conn)

        rows = conn.execute("SELECT * FROM maps_companies ORDER BY id").fetchall()
        included = 0
        for row in rows:
            value = compute_include_in_outreach(row)
            if value:
                included += 1
            conn.execute(
                "UPDATE maps_companies SET include_in_outreach=? WHERE id=?",
                (1 if value else 0, row["id"]),
            )
        conn.commit()

        summary = {"total": len(rows), "included": included, "excluded": len(rows) - included}
        print(json.dumps(summary, indent=2))
        return summary
    finally:
        if own:
            conn.close()


if __name__ == "__main__":
    run()
