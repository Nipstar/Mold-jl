"""Stage I: final export contract -- jl-mold-fl-{full,outreach,excluded,qa}.

Andy's exact spec (see docs/superpowers/specs -- final hardening pass):
  - jl-mold-fl-full.csv: every row, all columns, nothing removed.
  - jl-mold-fl-outreach.csv: include_in_outreach=true rows only, sorted
    priority_rank DESC, pain_score DESC, exact column list (see
    OUTREACH_COLUMNS).
  - jl-mold-fl-excluded.csv: include_in_outreach=false rows, plus an
    exclusion_reasons column.
  - jl-mold-fl-qa.md: run summary for spot-checking the export.

Deterministic: every query is ORDER BY id (or an explicit tiebreak), so two
runs against the same DB state produce byte-identical output.
"""
from __future__ import annotations

import csv
import sqlite3
from collections import Counter
from pathlib import Path

STATE = "FL"

OUTREACH_COLUMNS = [
    "company_name", "first_name", "last_name", "email", "email_verified",
    "email_source", "phone", "website", "address", "city", "state", "county",
    "zip", "contact_bucket", "segment", "pain_score", "priority_rank",
    "data_confidence", "license_class", "matched_principal_name",
    "primary_service", "rating", "review_count", "tags", "place_id",
]


# --- field derivations -----------------------------------------------------

def split_owner_name(owner_name_found: str | None) -> tuple[str, str]:
    """('First Last', 'First', 'Last') or 'LAST, FIRST MIDDLE' (common DBPR
    license format) -> (first, last), both naturally title-cased. Blank
    tuple if owner_name_found is null/empty."""
    if not owner_name_found:
        return "", ""
    text = owner_name_found.strip()
    if not text:
        return "", ""

    if "," in text:
        last, _, first = text.partition(",")
        last = last.strip().title()
        first = first.strip().title()
        return first, last

    parts = text.split(None, 1)
    if len(parts) == 1:
        return parts[0], ""
    first, last = parts[0], parts[1]
    return first, last


def compute_contact_bucket(row) -> str:
    """Exact if/elif chain per spec:
      1. verified_email:   email present AND email_verified in (unknown, valid)
                            AND email_source in (found, found_offdomain)
      2. guessed_email:     email present AND email_source == guessed
      3. invalid_recheck:   email present AND email_verified == invalid
      4. form_only_has_website: no usable email but website exists
      5. no_website_research: no usable email, no website
    """
    def get(key):
        return row[key] if hasattr(row, "keys") else row.get(key)

    email = get("email")
    email_verified = get("email_verified")
    email_source = get("email_source")

    has_email = bool(email)
    if has_email and email_verified in ("unknown", "valid") and email_source in ("found", "found_offdomain"):
        return "verified_email"
    if has_email and email_source == "guessed":
        return "guessed_email"
    if has_email and email_verified == "invalid":
        return "invalid_recheck"
    if get("website"):
        return "form_only_has_website"
    return "no_website_research"


def compute_tags(row, vertical_region: str = "mold-fl") -> str:
    def get(key):
        return row[key] if hasattr(row, "keys") else row.get(key)

    tags = [vertical_region]
    if get("segment"):
        tags.append(get("segment"))
    if get("license_verified"):
        tags.append("license-verified")
    if get("emergency_247"):
        tags.append("emergency-247")
    if get("form_only_contact"):
        tags.append("form-only")
    return ",".join(tags)


def _canonical_place_id_lookup(rows: list[sqlite3.Row]) -> dict:
    """dup_group_id -> place_id of the canonical (is_duplicate=0) row."""
    lookup = {}
    for r in rows:
        if r["dup_group_id"] is not None and not r["is_duplicate"]:
            lookup[r["dup_group_id"]] = r["place_id"]
    return lookup


def compute_exclusion_reasons(row, canonical_lookup: dict) -> str:
    reasons = []
    if not row["category_relevant"]:
        reasons.append("off_category")
    if row["franchise_brand"]:
        reasons.append(f"franchise_brand:{row['franchise_brand']}")
    if row["multi_location_domain"]:
        reasons.append("multi_location_domain")
    if row["lead_mill_suspect"]:
        reasons.append(f"lead_mill:{row['lead_mill_reasons'] or ''}")
    if row["out_of_area"]:
        reasons.append("out_of_area")
    if row["is_duplicate"]:
        canonical = canonical_lookup.get(row["dup_group_id"])
        reasons.append(f"duplicate_of:{canonical or ''}")
    return ",".join(reasons)


# --- writers -----------------------------------------------------------

def write_full_csv(rows: list[sqlite3.Row], columns: list[str], path: Path) -> int:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(columns)
        for r in rows:
            w.writerow([r[c] for c in columns])
    return len(rows)


def write_outreach_csv(rows: list[sqlite3.Row], path: Path, vertical_region: str) -> int:
    included = [r for r in rows if r["include_in_outreach"]]
    included.sort(
        key=lambda r: ((r["priority_rank"] or 0), (r["pain_score"] or 0)),
        reverse=True,
    )
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(OUTREACH_COLUMNS)
        for r in included:
            first, last = split_owner_name(r["owner_name_found"])
            w.writerow([
                r["name"],
                first,
                last,
                r["email"],
                r["email_verified"],
                r["email_source"],
                r["phone"],
                r["website"],
                r["address"],
                r["city"],
                STATE,
                r["county"],
                r["zip"],
                compute_contact_bucket(r),
                r["segment"],
                r["pain_score"],
                r["priority_rank"],
                r["data_confidence"],
                r["license_class"],
                r["matched_principal_name"],
                r["primary_service"],
                r["rating"],
                r["review_count"],
                compute_tags(r, vertical_region),
                r["place_id"],
            ])
    return len(included)


def write_excluded_csv(rows: list[sqlite3.Row], columns: list[str], path: Path) -> int:
    excluded = [r for r in rows if not r["include_in_outreach"]]
    canonical_lookup = _canonical_place_id_lookup(rows)
    out_columns = [*columns, "exclusion_reasons"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(out_columns)
        for r in excluded:
            values = [r[c] for c in columns]
            values.append(compute_exclusion_reasons(r, canonical_lookup))
            w.writerow(values)
    return len(excluded)


def _exclusion_reason_counts(rows: list[sqlite3.Row], canonical_lookup: dict) -> Counter:
    counts: Counter = Counter()
    for r in rows:
        if r["include_in_outreach"]:
            continue
        reasons = compute_exclusion_reasons(r, canonical_lookup)
        for reason in reasons.split(","):
            if not reason:
                continue
            key = reason.split(":", 1)[0]
            counts[key] += 1
    return counts


def write_qa_md(rows: list[sqlite3.Row], path: Path) -> None:
    total = len(rows)
    canonical_lookup = _canonical_place_id_lookup(rows)
    reason_counts = _exclusion_reason_counts(rows, canonical_lookup)

    yield_counts: Counter = Counter()
    for r in rows:
        yield_counts[(r["email_source"] or "", r["email_verified"] or "")] += 1

    reject_counter: Counter = Counter()
    for r in rows:
        if r["lead_mill_suspect"]:
            key = (r["email"] or r["website"] or r["name"] or "").lower()
            if key:
                reject_counter[key] += 1
    top_rejected = reject_counter.most_common(20)

    name_counts: Counter = Counter()
    for r in rows:
        if r["name"]:
            name_counts[r["name"]] += 1
    no_brand_multi = sorted(
        name for name, c in name_counts.items()
        if c >= 2 and not any(r["name"] == name and r["franchise_brand"] for r in rows)
    )

    lines = []
    lines.append("# jl-mold-fl export QA report\n")
    lines.append(f"Total rows: {total}\n")

    lines.append("## Exclusion reason counts\n")
    lines.append("(a row can have multiple reasons; each is counted independently)\n")
    lines.append("| reason | count |")
    lines.append("|---|---|")
    for reason in sorted(reason_counts):
        lines.append(f"| {reason} | {reason_counts[reason]} |")
    lines.append("")

    lines.append("## Email yield (email_source x email_verified)\n")
    lines.append("| email_source | email_verified | count |")
    lines.append("|---|---|---|")
    for (source, verified), count in sorted(yield_counts.items()):
        lines.append(f"| {source or '(none)'} | {verified or '(none)'} | {count} |")
    lines.append("")

    lines.append("## Top 20 rejected_shared emails/domains (lead-mill suspects)\n")
    lines.append("| value | count |")
    lines.append("|---|---|")
    for value, count in top_rejected:
        lines.append(f"| {value} | {count} |")
    lines.append("")

    lines.append("## Multi-location independents (name on >=2 rows, no franchise_brand match)\n")
    lines.append("Candidates to review for config/brands.yml.\n")
    for name in no_brand_multi:
        lines.append(f"- {name}")
    lines.append("")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def export_all(conn: sqlite3.Connection, out_dir: Path, vertical_region: str = "mold-fl") -> dict:
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM maps_companies ORDER BY id").fetchall()
    columns = [d[0] for d in conn.execute("SELECT * FROM maps_companies LIMIT 0").description]

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    n_full = write_full_csv(rows, columns, out_dir / "jl-mold-fl-full.csv")
    n_outreach = write_outreach_csv(rows, out_dir / "jl-mold-fl-outreach.csv", vertical_region)
    n_excluded = write_excluded_csv(rows, columns, out_dir / "jl-mold-fl-excluded.csv")
    write_qa_md(rows, out_dir / "jl-mold-fl-qa.md")

    return {"total": n_full, "outreach": n_outreach, "excluded": n_excluded}


if __name__ == "__main__":
    from .. import db

    conn = db.get_connection()
    summary = export_all(conn, Path("dist_export"))
    conn.close()
    print(summary)
