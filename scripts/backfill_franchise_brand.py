"""One-off backfill: config-driven franchise_brand / multi_location_domain /
category_relevant re-check for all maps_companies rows (Stage A pipeline
hardening). Pure local computation, no API spend.

- franchise_brand: matched via src.relevance.match_franchise_brand(name, website)
- franchise_flag: derived strictly as (franchise_brand IS NOT NULL)
- multi_location_domain: 1 if the row's website root domain appears on >=2
  OTHER rows (different place_id) with a non-null root domain, else 0
- category_relevant: re-run via src.relevance.is_category_relevant(google_category),
  now denylist-config-driven instead of hardcoded
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402
from src.relevance import is_category_relevant, match_franchise_brand, _root_domain  # noqa: E402


def main() -> None:
    conn = db.get_connection()
    db.run_franchise_brand_migration(conn)

    rows = conn.execute(
        "SELECT id, place_id, name, website, google_category, category_relevant FROM maps_companies"
    ).fetchall()

    # root domain -> count of distinct place_ids with that domain
    domain_counts = Counter()
    domain_by_row = {}
    for r in rows:
        rd = _root_domain(r["website"]) if r["website"] else None
        domain_by_row[r["id"]] = rd
        if rd:
            domain_counts[rd] += 1

    brand_counts = Counter()
    multi_location_ct = 0
    category_flips = 0
    franchise_flag_ct = 0

    for r in rows:
        brand = match_franchise_brand(r["name"], r["website"])
        rd = domain_by_row[r["id"]]
        is_multi = 1 if (rd and domain_counts[rd] >= 2) else 0
        new_relevant = 1 if is_category_relevant(r["google_category"]) else 0
        old_relevant = r["category_relevant"]

        if brand:
            brand_counts[brand] += 1
            franchise_flag_ct += 1
        if is_multi:
            multi_location_ct += 1
        if old_relevant is not None and int(old_relevant) != new_relevant:
            category_flips += 1

        conn.execute(
            "UPDATE maps_companies SET franchise_brand = ?, multi_location_domain = ?, "
            "franchise_flag = ?, category_relevant = ? WHERE id = ?",
            (brand, is_multi, 1 if brand else 0, new_relevant, r["id"]),
        )

    conn.commit()

    total = len(rows)
    print(f"Total rows: {total}")
    print(f"franchise_brand set: {franchise_flag_ct}")
    for brand, ct in brand_counts.most_common():
        print(f"  {brand}: {ct}")
    print(f"multi_location_domain=1: {multi_location_ct}")
    print(f"category_relevant flips: {category_flips}")

    conn.close()


if __name__ == "__main__":
    main()
