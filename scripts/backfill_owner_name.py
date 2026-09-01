"""One-off backfill: owner-name validation for all maps_companies rows
(Stage E pipeline hardening).

For every row with owner_name_found set:
- If matched_principal_name is also set (license match exists), the name
  came from Stage 9's future license-preference logic conceptually, but
  for now: set owner_name_source='license', leave owner_name_found as-is
  (license names are trusted, not nav fragments).
- Else, validate owner_name_found with is_valid_person_name(). If it
  fails, null owner_name_found and owner_confirmed, owner_name_source=
  'none'. If it passes, owner_name_source='about_page'.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402
from src.owner_name import is_valid_person_name  # noqa: E402


def main() -> None:
    conn = db.get_connection()
    db.run_owner_name_source_migration(conn)

    rows = conn.execute(
        "SELECT id, owner_name_found, matched_principal_name, owner_confirmed "
        "FROM maps_companies WHERE owner_name_found IS NOT NULL AND TRIM(owner_name_found) != ''"
    ).fetchall()

    n_rejected = 0
    n_license = 0
    n_about_page = 0
    rejected_samples = []

    for r in rows:
        if r["matched_principal_name"]:
            # A license match is always trusted over whatever text was
            # scraped for owner_name_found -- overwrite garbage (e.g.
            # 'Resources More') with the real licensee name.
            conn.execute(
                "UPDATE maps_companies SET owner_name_found=?, owner_name_source=? WHERE id=?",
                (r["matched_principal_name"], "license", r["id"]),
            )
            n_license += 1
            continue

        if is_valid_person_name(r["owner_name_found"]):
            conn.execute(
                "UPDATE maps_companies SET owner_name_source=? WHERE id=?",
                ("about_page", r["id"]),
            )
            n_about_page += 1
        else:
            conn.execute(
                "UPDATE maps_companies SET owner_name_found=NULL, owner_confirmed=0, "
                "owner_name_source=? WHERE id=?",
                ("none", r["id"]),
            )
            n_rejected += 1
            rejected_samples.append(r["owner_name_found"])

    conn.commit()

    print(f"Total rows with owner_name_found: {len(rows)}")
    print(f"Rejected (nav fragment): {n_rejected} -> {rejected_samples}")
    print(f"License-sourced (kept): {n_license}")
    print(f"About-page (validated, kept): {n_about_page}")


if __name__ == "__main__":
    main()
