"""One-off backfill: lead_mill_score/lead_mill_reasons/lead_mill_suspect for
all maps_companies rows (Stage C pipeline hardening). Pure local
computation, no API spend. Then re-runs Stage 4 scoring so pain_score
reflects the corrected lead_mill_suspect exclusion (and the new
out_of_area exclusion added alongside it).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402
from src.lead_mill import compute_lead_mill_score, is_lead_mill_suspect, threshold  # noqa: E402


def main() -> None:
    conn = db.get_connection()
    db.run_lead_mill_migration(conn)

    rows = [dict(r) for r in conn.execute("SELECT * FROM maps_companies").fetchall()]

    n_suspect = 0
    samples = []

    for row in rows:
        score, reasons = compute_lead_mill_score(row, rows)
        suspect = int(is_lead_mill_suspect(score))
        if suspect:
            n_suspect += 1
            if len(samples) < 15:
                samples.append((row["name"], score, ",".join(reasons)))

        conn.execute(
            "UPDATE maps_companies SET lead_mill_score=?, lead_mill_reasons=?, "
            "lead_mill_suspect=? WHERE id=?",
            (score, ",".join(reasons), suspect, row["id"]),
        )

    conn.commit()

    print(f"Total rows: {len(rows)}")
    print(f"lead_mill_suspect=True (threshold={threshold()}): {n_suspect}")
    print("\nSample flagged rows:")
    for name, score, reasons in samples:
        print(f"  {name} | score={score} | {reasons}")


if __name__ == "__main__":
    main()
