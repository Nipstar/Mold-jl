"""One-off backfill: classify primary_service for category_relevant=1 rows.

remediation: categories/google_category shows remediation/restoration signal.
assessment_only: only assessment/inspection/testing signal, no remediation.
Both present -> remediation (higher-value work).
Neither clearly determinable -> assessment_only (conservative default).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402

REMEDIATION_TERMS = [
    "mold remediation",
    "water damage restoration",
    "restoration company",
    "fire and water damage restoration",
    "disaster restoration company",
]
ASSESSMENT_TERMS = [
    "mold assessment",
    "mold inspection",
    "mold testing",
]


def classify(categories: str | None, google_category: str | None) -> str:
    text = " ".join(t for t in (categories, google_category) if t).lower()
    has_remediation = any(term in text for term in REMEDIATION_TERMS)
    if has_remediation:
        return "remediation"
    has_assessment = any(term in text for term in ASSESSMENT_TERMS)
    if has_assessment:
        return "assessment_only"
    return "assessment_only"


def main() -> None:
    conn = db.get_connection()
    db.run_primary_service_migration(conn)
    rows = conn.execute(
        "SELECT id, categories, google_category FROM maps_companies WHERE category_relevant = 1"
    ).fetchall()
    counts = {"remediation": 0, "assessment_only": 0}
    for r in rows:
        service = classify(r["categories"], r["google_category"])
        counts[service] += 1
        conn.execute(
            "UPDATE maps_companies SET primary_service = ? WHERE id = ?",
            (service, r["id"]),
        )
    conn.commit()
    conn.close()
    print(f"Backfilled {len(rows)} rows: {counts}")


if __name__ == "__main__":
    main()
