"""One-off backfill: license-driven enrichment for all maps_companies rows
(Stage H pipeline hardening). Pure local computation, no API spend --
joins against the existing `companies` (DBPR) table already in this DB.

For every license_verified=1 row:
- license_class computed and stored.
- primary_service overwritten from license class (beats Google-category
  derivation, per spec item 9a).
- address fallback: if has_street_address=0, pull the DBPR business
  address and fill city/county/address/zip, mark location_source
  accordingly.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402
from src.license_match import (  # noqa: E402
    compute_license_class,
    get_license_address,
    primary_service_from_license_type,
)


def main() -> None:
    conn = db.get_connection()
    db.run_license_class_migration(conn)

    rows = conn.execute(
        "SELECT id, matched_license_number, has_street_address, primary_service "
        "FROM maps_companies WHERE license_verified = 1"
    ).fetchall()

    n_class = {"MRSR": 0, "MRSA": 0, "both": 0, "none": 0}
    n_service_fixed = 0
    n_address_filled = 0
    address_samples = []

    for r in rows:
        license_class = compute_license_class(conn, r["matched_license_number"])
        n_class[license_class] = n_class.get(license_class, 0) + 1

        new_service = None
        if license_class in ("MRSR", "MRSA"):
            new_service = primary_service_from_license_type(license_class)
        if new_service and new_service != r["primary_service"]:
            n_service_fixed += 1

        new_address = None
        if not r["has_street_address"]:
            new_address = get_license_address(conn, r["matched_license_number"])
            if new_address:
                n_address_filled += 1
                if len(address_samples) < 10:
                    address_samples.append((r["id"], new_address))

        updates = {"license_class": license_class}
        if new_service:
            updates["primary_service"] = new_service
        if new_address:
            updates.update(
                {
                    "address": new_address["address"],
                    "city": new_address["city"],
                    "county": new_address["county"],
                    "zip": new_address["zip"],
                    "location_source": "license_address",
                    "has_street_address": 1,
                }
            )

        set_clause = ", ".join(f"{k}=?" for k in updates)
        conn.execute(
            f"UPDATE maps_companies SET {set_clause} WHERE id=?",
            (*updates.values(), r["id"]),
        )

    # license_verified=0 rows get license_class='none' too, for a complete column.
    conn.execute(
        "UPDATE maps_companies SET license_class='none' "
        "WHERE COALESCE(license_verified, 0) != 1 AND license_class IS NULL"
    )

    conn.commit()

    print(f"license_verified=1 rows: {len(rows)}")
    print(f"license_class distribution (verified rows only): {n_class}")
    print(f"primary_service corrected from license class: {n_service_fixed}")
    print(f"Address filled from DBPR fallback: {n_address_filled}")
    for row_id, addr in address_samples:
        print(f"  id={row_id} -> {addr}")


if __name__ == "__main__":
    main()
