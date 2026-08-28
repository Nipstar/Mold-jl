"""Stage 4 -- pain_score (0-100) + segment.

Weights per spec (tuning notes below where a literal spec rule needed a
concrete proxy):

Missed-call likelihood (40):
  - small team, 1-2 licensees                                   +15
  - no hours listed OR claims 24/7 but hours show gaps evenings/
    weekends (proxy: no regularOpeningHours captured at all counts
    as "no hours listed"; if hours exist we check for any day with
    a close time before 18:00 as a proxy for "not truly evening-
    covered")                                                    +10
  - no booking widget AND no chat widget                         +10
  - phone-only contact (has phone, no live email found, no
    booking/chat)                                                 +5
Review dependence (30):
  - review count between 3 and 40 inclusive                      +15
  - rating between 3.5 and 4.5 inclusive                         +10
  - most recent review >6 months old (proxy: latest_review_age_
    months > 6, from Places "reviews" relative-time field)        +5
Deal viability (30):
  - primary license type MRSR (mold remediator, does the work,
    not just assessment)                                         +10
  - independent (franchise_flag = 0)                             +10
  - has a website                                                 +5
  - owner_confirmed (DBPR principal name found on site)           +5

segment:
  - tier1: has web presence (website != none) AND misses-calls
    subscore (out of 40) >= 20
  - tier2: has web presence AND review-dependence subscore
    (out of 30) >= 15 but not tier1
  - tier3: no website, or website but neither tier1 nor tier2
    condition met (weak/poor presence overall)
"""
from __future__ import annotations

import json

from . import db


def _hours_gap_evening_weekend(hours_json: str | None) -> bool:
    if not hours_json:
        return True  # no hours listed at all
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
    notes = []

    # Missed-call likelihood (40)
    if (row["licensee_count"] or 1) <= 2:
        sub["missed_call"] += 15
    if _hours_gap_evening_weekend(row["places_hours_json"]):
        sub["missed_call"] += 10
    if not row["booking_widget"] and not row["chat_widget"]:
        sub["missed_call"] += 10
    has_live_email = row["email_source"] == "found"
    if row["places_phone"] and not has_live_email and not row["booking_widget"] and not row["chat_widget"]:
        sub["missed_call"] += 5

    # Review dependence (30)
    rc = row["places_review_count"]
    if rc is not None and 3 <= rc <= 40:
        sub["review_dep"] += 15
    rating = row["places_rating"]
    if rating is not None and 3.5 <= rating <= 4.5:
        sub["review_dep"] += 10
    age = row["latest_review_age_months"]
    if age is not None and age > 6:
        sub["review_dep"] += 5

    # Deal viability (30)
    if row["license_type"] == "MRSR":
        sub["deal_viability"] += 10
    if not row["franchise_flag"]:
        sub["deal_viability"] += 10
    if row["website"] and row["website"] != "none":
        sub["deal_viability"] += 5
    if row["owner_confirmed"]:
        sub["deal_viability"] += 5

    total = sub["missed_call"] + sub["review_dep"] + sub["deal_viability"]

    # 'no_dba_skipped' is not a real match -- it's the explicit no-DBA skip
    # path (individual licensees, never searched). Must not count as presence.
    real_match = row["match_confidence"] in ("high", "medium", "low")
    has_presence = bool(row["website"] and row["website"] != "none") or real_match
    if has_presence and sub["missed_call"] >= 20:
        segment = "tier1"
    elif has_presence and sub["review_dep"] >= 15:
        segment = "tier2"
    else:
        segment = "tier3"

    return total, segment, sub


def run() -> dict:
    conn = db.get_connection()
    db.run_enrich_migrations(conn)
    rows = conn.execute("SELECT * FROM companies WHERE enrich_stage >= 2 ORDER BY id").fetchall()

    dist = {"tier1": 0, "tier2": 0, "tier3": 0}
    for row in rows:
        total, segment, sub = score_row(row)
        conn.execute(
            "UPDATE companies SET pain_score=?, segment=?, score_notes=?, enrich_stage=4 WHERE id=?",
            (total, segment, json.dumps(sub), row["id"]),
        )
        dist[segment] += 1
    conn.commit()
    conn.close()

    summary = {"scored": len(rows), "segment_distribution": dist}
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    run()
