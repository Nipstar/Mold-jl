"""One-off backfill: dup_group_id / is_duplicate re-clustering for all
maps_companies rows (Stage F pipeline hardening), using the extended
src.dedup module (name/phone/domain/address matching, with franchise
corporate domains excluded from the domain-match signal).

Re-running this OVERWRITES any prior is_duplicate/dup_group_id values --
it is the single source of truth per src/dedup.py's docstring.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402
from src.dedup import assign_duplicate_groups  # noqa: E402


def main() -> None:
    conn = db.get_connection()
    db.run_dedup_migration(conn)

    rows = [dict(r) for r in conn.execute("SELECT * FROM maps_companies").fetchall()]
    before_dup = sum(1 for r in rows if r.get("is_duplicate"))

    clustered = assign_duplicate_groups(rows)

    after_dup = 0
    samples = []
    for row in clustered:
        conn.execute(
            "UPDATE maps_companies SET dup_group_id=?, is_duplicate=? WHERE id=?",
            (row["dup_group_id"], row["is_duplicate"], row["id"]),
        )
        if row["is_duplicate"]:
            after_dup += 1
            if len(samples) < 15:
                samples.append((row["name"], row["website"], row["dup_group_id"]))
    conn.commit()

    print(f"Total rows: {len(rows)}")
    print(f"is_duplicate=1 before this run: {before_dup}")
    print(f"is_duplicate=1 after this run: {after_dup}")
    print("\nSample non-canonical (excluded) rows:")
    for name, website, group in samples:
        print(f"  {name} | {website} | group={group}")


if __name__ == "__main__":
    main()
