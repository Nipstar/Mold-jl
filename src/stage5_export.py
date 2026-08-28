"""Stage 5 -- export prospects_scored.csv, top_100.csv, README.md."""
from __future__ import annotations

import csv
import json

from . import config, db

ALL_COLS = [
    "id", "license_number", "license_type", "licensee_name", "dba_name", "principal_name",
    "address", "city", "county", "zip", "status", "expiration_date", "franchise_flag",
    "licensee_count", "match_confidence", "match_source", "places_rating",
    "places_review_count", "places_website", "places_phone", "business_status",
    "in_local_pack", "latest_review_age_months", "website", "owner_name_found",
    "owner_confirmed", "email", "email_source", "booking_widget", "chat_widget",
    "emergency_247", "form_only_contact", "pain_score", "segment", "score_notes",
]

TOP100_COLS = ["company", "principal_name", "email", "email_source", "phone", "city",
               "county", "website", "rating", "review_count", "pain_score", "segment", "notes"]


def run() -> dict:
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM companies WHERE enrich_stage >= 4 ORDER BY pain_score DESC"
    ).fetchall()

    config.EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    scored_path = config.EXPORT_DIR / "prospects_scored.csv"
    with scored_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ALL_COLS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r[c] for c in ALL_COLS})

    top100 = []
    for r in rows:
        if r["franchise_flag"]:
            continue
        if not (r["principal_name"] or r["owner_name_found"]):
            continue
        if not r["email"]:
            continue
        top100.append(r)
        if len(top100) >= 100:
            break

    top100_path = config.EXPORT_DIR / "top_100.csv"
    with top100_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TOP100_COLS)
        w.writeheader()
        for r in top100:
            company = r["dba_name"] or r["licensee_name"]
            notes = []
            if r["match_confidence"]:
                notes.append(f"match:{r['match_confidence']}")
            if r["email_source"]:
                notes.append(f"email:{r['email_source']}")
            if r["emergency_247"]:
                notes.append("claims 24/7")
            w.writerow({
                "company": company,
                "principal_name": r["owner_name_found"] or r["principal_name"],
                "email": r["email"],
                "email_source": r["email_source"],
                "phone": r["places_phone"],
                "city": r["city"],
                "county": r["county"],
                "website": r["website"],
                "rating": r["places_rating"],
                "review_count": r["places_review_count"],
                "pain_score": r["pain_score"],
                "segment": r["segment"],
                "notes": "; ".join(notes),
            })

    # Stats for README
    total_licensees = conn.execute("SELECT COUNT(*) c FROM companies").fetchone()["c"]
    dedup = total_licensees  # Stage 1 already dedups at ingest (one row per license)
    franchise_ct = conn.execute("SELECT COUNT(*) c FROM companies WHERE franchise_flag=1").fetchone()["c"]
    matched = conn.execute("SELECT COUNT(*) c FROM companies WHERE match_confidence IS NOT NULL").fetchone()["c"]
    with_site = conn.execute("SELECT COUNT(*) c FROM companies WHERE website IS NOT NULL AND website != 'none'").fetchone()["c"]
    emails = conn.execute("SELECT COUNT(*) c FROM companies WHERE email IS NOT NULL").fetchone()["c"]
    emails_found = conn.execute("SELECT COUNT(*) c FROM companies WHERE email_source='found'").fetchone()["c"]
    emails_guessed = conn.execute("SELECT COUNT(*) c FROM companies WHERE email_source='guessed'").fetchone()["c"]
    seg_dist = dict(conn.execute("SELECT segment, COUNT(*) c FROM companies WHERE segment IS NOT NULL GROUP BY segment").fetchall())
    score_buckets = conn.execute(
        "SELECT CASE WHEN pain_score>=70 THEN '70-100' WHEN pain_score>=40 THEN '40-69' ELSE '0-39' END bucket, "
        "COUNT(*) c FROM companies WHERE pain_score IS NOT NULL GROUP BY bucket"
    ).fetchall()
    score_dist = {r["bucket"]: r["c"] for r in score_buckets}

    api_spend_rows = conn.execute("SELECT source, COUNT(*) c FROM api_cache GROUP BY source").fetchall()
    conn.close()

    readme = f"""# jl-mold-fl -- JobsLocked Prospecting: FL Mold Remediation/Assessment Licensees

## Pipeline summary

- Total DBPR licensees ingested (Stage 1): {total_licensees}
- Companies after dedupe: {dedup} (Stage 1 ingest dedupes on license_number; one row per license)
- Franchise-flagged (excluded from top_100, kept in full CSV): {franchise_ct}
- Google Places / SerpAPI match rate: {matched}/{total_licensees} ({round(100*matched/total_licensees,1)}%)
- Companies with a resolved website: {with_site}
- Email hit rate: {emails}/{with_site if with_site else 1} ({round(100*emails/with_site,1) if with_site else 0}%) -- {emails_found} found on-page, {emails_guessed} guessed (info@domain, no SMTP verification)

## Segment distribution
{json.dumps(seg_dist, indent=2)}

## Pain-score distribution
{json.dumps(score_dist, indent=2)}

## API call volume (from api_cache, cache-deduped -- reruns spend $0 extra)
{json.dumps({r['source']: r['c'] for r in api_spend_rows}, indent=2)}

## Files
- `prospects_scored.csv` -- all {len(rows)} enriched companies, all columns, sorted pain_score desc.
- `top_100.csv` -- top 100 independent (non-franchise) prospects with an owner name AND email/strong guess.

## Scoring methodology
See `src/stage4_score.py` module docstring for exact weights and the two
tuning proxies used where the spec described a qualitative rule (hours
gap evenings/weekends -> checked against Places regularOpeningHours
periods; missing entirely counts as a gap). All other weights implemented
literally as specified.

## Notes
- Individual MRSA licensees (assessors, often no business storefront) show
  a lower Places/website match rate than MRSR company licensees -- expected,
  not a data quality issue.
- Tier 3 (no/poor presence) companies are retained in prospects_scored.csv,
  never dropped, per spec.
"""
    readme_path = config.EXPORT_DIR / "README.md"
    readme_path.write_text(readme)

    summary = {
        "total_licensees": total_licensees, "franchise_ct": franchise_ct,
        "matched": matched, "with_site": with_site, "emails": emails,
        "top100_count": len(top100), "seg_dist": seg_dist, "score_dist": score_dist,
    }
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    run()
