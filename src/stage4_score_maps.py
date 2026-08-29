"""Stage 4 (maps-first pivot) -- pain_score (0-100) + segment for
`maps_companies` rows.

Repointed from the old DBPR-first src/stage4_score.py at maps_companies
instead of companies (see
docs/superpowers/specs/2026-08-28-maps-first-north-florida-design.md,
"Stage 3/4 pivot" section). segment tier thresholds/logic are reused
unchanged from the old score_row(). Weight adjustments below are the ones
required because maps_companies carries different source signals than the
old DBPR `companies` table.

Missed-call likelihood (40 total):
  - small-team/low-visibility proxy: review_count < 10             +15
    (maps_companies has no `licensee_count` -- that was a DBPR-only signal,
    a count of individuals licensed under one business. The nearest proxy
    available here is review_count: a very small review count on Google
    correlates with a small/low-visibility operation the same way a 1-2
    person license count did in the old model. Trade-off: review_count is
    noisier -- a new but well-staffed company can have few reviews, and an
    old small shop can have accumulated many -- so this is a weaker signal
    than licensee_count was, but it's the best available proxy without
    re-adding a DBPR dependency.)
  - no hours listed                                                +10
    (maps_companies.hours_json is populated straight from the Places
    sweep, same shape as companies.places_hours_json, so the "no hours
    listed" half of the old proxy carries over directly. The old code's
    "close time before 18:00" / "no Sat-Sun coverage" refinement also
    still applies since the JSON shape is identical.)
  - no booking widget AND no chat widget                           +10
  - phone-only contact (no website)                                +5
    (spec explicitly calls this "phone-only contact (no website)" for the
    maps pivot, simpler than the old "phone + no live email + no
    booking/chat" combo since website presence is now the primary
    contactability signal being scored elsewhere in this same subscore.)
  Sub-weights: 15 + 10 + 10 + 5 = 40.

Review dependence (30 total, 25 of 30 currently scorable):
  - review_count in [3, 40]                                        +15
  - rating in [3.5, 4.5]                                           +10
  - review recency (remaining 5 pts): SKIPPED. The old stage4_score.py
    scored this off `latest_review_age_months`, which was populated from
    the Places "reviews" relative-time field during the old DBPR-first
    Stage 2 enrichment. The maps-first Stage 2 sweep (stage2_places.py)
    does not fetch individual review timestamps for maps_companies rows,
    so there is no equivalent field to score against here -- this isn't a
    deliberate business-logic exclusion, just missing input data. Per the
    task instructions this is a known, documented gap: review_dep subscore
    tops out at 25/30 rather than 30/30 until/unless a future Stage 2 pass
    adds per-review recency data to maps_companies. NOT silently folded
    into another sub-signal.

Deal viability (30 total):
  - license_verified = 1                                           +10
    (closest equivalent to the old model's MRSR/license-type signal. The
    old table assumed every row *was* a DBPR license already filtered to
    MRSR; here license_verified means a maps_companies row was
    successfully cross-matched to a real FL DBPR license record at all --
    i.e. "confirmed real, currently licensed business" rather than
    "confirmed remediator vs. assessor-only". Per the design doc this is
    a positive signal (regulatory-clean, easier sell), not a gate.)
  - franchise_flag = 0 (independent)                                +10
  - has website                                                     +5
  - owner_confirmed (matched_principal_name found on site)           +5

segment: tier1/tier2/tier3 -- thresholds/logic reused exactly from the old
stage4_score.py score_row(): tier1 = has web presence AND missed_call
subscore >= 20; tier2 = has web presence AND review_dep subscore >= 15 (and
not tier1); tier3 = everything else. "Web presence" here is website set, OR
license_verified=1 (maps-first equivalent of the old "real DBPR match" check
-- license_verified is exactly the boolean the old code derived match
confidence from, just materialized as a column instead of computed inline).
"""
from __future__ import annotations

import json

from . import db


def _no_hours_listed(hours_json: str | None) -> bool:
    if not hours_json:
        return True
    try:
        obj = json.loads(hours_json)
    except (TypeError, ValueError):
        return True
    periods = obj.get("periods") or []
    if not periods:
        return True
    for p in periods:
        close = p.get("close") or {}
        hour = close.get("hour")
        if hour is not None and hour < 18:
            return True
    days = {p.get("open", {}).get("day") for p in periods}
    if not ({5, 6} & days):  # no Sat/Sun coverage (Google: 0=Sun..6=Sat)
        return True
    return False


def score_row(row) -> tuple[int, str, dict]:
    sub = {"missed_call": 0, "review_dep": 0, "deal_viability": 0}

    # Missed-call likelihood (40)
    rc = row["review_count"]
    if rc is not None and rc < 10:
        sub["missed_call"] += 15
    if _no_hours_listed(row["hours_json"]):
        sub["missed_call"] += 10
    if not row["booking_widget"] and not row["chat_widget"]:
        sub["missed_call"] += 10
    if not row["website"]:
        sub["missed_call"] += 5

    # Review dependence (30, 25 currently scorable -- see module docstring
    # for the documented recency-data gap)
    if rc is not None and 3 <= rc <= 40:
        sub["review_dep"] += 15
    rating = row["rating"]
    if rating is not None and 3.5 <= rating <= 4.5:
        sub["review_dep"] += 10

    # Deal viability (30)
    if row["license_verified"]:
        sub["deal_viability"] += 10
    if not row["franchise_flag"]:
        sub["deal_viability"] += 10
    if row["website"]:
        sub["deal_viability"] += 5
    if row["owner_confirmed"]:
        sub["deal_viability"] += 5

    total = sub["missed_call"] + sub["review_dep"] + sub["deal_viability"]

    has_presence = bool(row["website"]) or bool(row["license_verified"])
    if has_presence and sub["missed_call"] >= 20:
        segment = "tier1"
    elif has_presence and sub["review_dep"] >= 15:
        segment = "tier2"
    else:
        segment = "tier3"

    return total, segment, sub


def run(limit: int | None = None, ids: list[int] | None = None) -> dict:
    conn = db.get_connection()
    db.run_maps_companies_migration(conn)
    db.run_dedup_migration(conn)
    db.run_maps_enrich_migrations(conn)

    query = (
        "SELECT * FROM maps_companies "
        "WHERE COALESCE(is_duplicate, 0) != 1 AND COALESCE(lead_mill_suspect, 0) != 1 "
        "AND stage3_processed_at IS NOT NULL "
    )
    if ids:
        query += f"AND id IN ({','.join('?' * len(ids))}) "
    query += "ORDER BY id"
    rows = conn.execute(query, tuple(ids) if ids else ()).fetchall()
    if limit:
        rows = rows[:limit]

    dist = {"tier1": 0, "tier2": 0, "tier3": 0}
    for row in rows:
        total, segment, sub = score_row(row)
        conn.execute(
            "UPDATE maps_companies SET pain_score=?, segment=?, score_notes=?, stage4_processed_at=? WHERE id=?",
            (total, segment, json.dumps(sub), db._now(), row["id"]),
        )
        dist[segment] += 1
    conn.commit()
    conn.close()

    summary = {"scored": len(rows), "segment_distribution": dist}
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--ids", type=str, default=None, help="comma-separated maps_companies ids, for smoke testing")
    a = p.parse_args()
    run(limit=a.limit, ids=[int(x) for x in a.ids.split(",")] if a.ids else None)
