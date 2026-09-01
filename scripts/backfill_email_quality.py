"""One-off backfill: email quality cleanup for all maps_companies rows
(Stage D pipeline hardening).

Rules applied in order:
1. Denylisted email (placeholder local-part or developer/builder domain) ->
   null the email, email_source='rejected_placeholder'.
2. Cross-row frequency: an email or its domain appearing on >=3 rows with
   DIFFERENT business website root domains is a shared dev/agency address
   -> null the email, email_source='rejected_shared' on all affected rows.
3. Domain agreement: for email_source='found' rows, if the email's domain
   doesn't agree with the business website (and isn't free-mail), downgrade
   to email_source='found_offdomain' (keep the email, just relabel).
4. Never-guess: for email_source='guessed' rows, null the email and set
   email_source='rejected_no_guess' if: no MX record for the domain, the
   domain is a known franchise corporate domain (config/brands.yml), the
   domain is a builder/social-only domain, or the row is
   lead_mill_suspect=1.
5. email_verified: MX-only check (no live SMTP, per standing decision) --
   'invalid' if the surviving email's domain has no MX record, 'unknown'
   if it does. Real DNS queries, no API cost, cached per domain.

Real network calls (MX lookups) happen here -- expect a a few seconds for
unique domains across 785 rows.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from src import db  # noqa: E402
from src.email_quality import (  # noqa: E402
    _root_domain,
    domain_agrees,
    has_mx_record,
    is_denylisted_email,
)

BRANDS_PATH = ROOT / "config" / "brands.yml"
LEAD_MILL_CONFIG_PATH = ROOT / "config" / "lead_mill.yml"


def _franchise_corporate_domains() -> set[str]:
    data = yaml.safe_load(BRANDS_PATH.read_text()) or {}
    domains = set()
    for brand in data.get("brands", []):
        for d in brand.get("domains", []):
            domains.add(d.lower())
    return domains


def _builder_domains() -> set[str]:
    data = yaml.safe_load(LEAD_MILL_CONFIG_PATH.read_text()) or {}
    return {d.lower() for d in data.get("builder_social_domains", [])}


def main() -> None:
    conn = db.get_connection()
    db.run_email_quality_migration(conn)

    rows = [dict(r) for r in conn.execute("SELECT * FROM maps_companies").fetchall()]

    n_placeholder = 0
    n_shared = 0
    n_offdomain = 0
    n_no_guess = 0
    franchise_domains = _franchise_corporate_domains()
    builder_domains = _builder_domains()

    # Pass 1: denylist + no-guess rejection, computed per-row first.
    for row in rows:
        email = row.get("email")
        source = row.get("email_source")
        if not email:
            continue

        if is_denylisted_email(email):
            row["email"] = None
            row["email_source"] = "rejected_placeholder"
            n_placeholder += 1
            continue

        if source == "guessed":
            domain = _root_domain(email)
            reject = (
                (domain and not has_mx_record(domain))
                or (domain in franchise_domains)
                or (domain in builder_domains)
                or bool(row.get("lead_mill_suspect"))
            )
            if reject:
                row["email"] = None
                row["email_source"] = "rejected_no_guess"
                n_no_guess += 1

    # Pass 2: cross-row frequency check (needs all surviving emails/domains
    # first, so it runs after pass 1's rejections).
    email_to_website_domains = defaultdict(set)
    domain_to_website_domains = defaultdict(set)
    for row in rows:
        email = row.get("email")
        if not email:
            continue
        website_domain = _root_domain(row.get("website")) if row.get("website") else None
        email_to_website_domains[email.lower()].add(website_domain)
        edomain = _root_domain(email)
        if edomain:
            domain_to_website_domains[edomain].add(website_domain)

    shared_emails = {e for e, sites in email_to_website_domains.items() if len(sites) >= 3}
    shared_domains = {d for d, sites in domain_to_website_domains.items() if len(sites) >= 3}

    for row in rows:
        email = row.get("email")
        if not email:
            continue
        edomain = _root_domain(email)
        if email.lower() in shared_emails or (edomain and edomain in shared_domains):
            row["email"] = None
            row["email_source"] = "rejected_shared"
            n_shared += 1

    # Pass 3: domain agreement downgrade for surviving 'found' emails.
    for row in rows:
        email = row.get("email")
        if not email or row.get("email_source") != "found":
            continue
        if not domain_agrees(email, row.get("website")):
            row["email_source"] = "found_offdomain"
            n_offdomain += 1

    # Pass 4: email_verified (MX-only, no live SMTP).
    for row in rows:
        email = row.get("email")
        if not email:
            row["email_verified"] = None
            row["email_verified_at"] = None
            continue
        domain = _root_domain(email)
        row["email_verified"] = "unknown" if (domain and has_mx_record(domain)) else "invalid"
        row["email_verified_at"] = "backfill"

    for row in rows:
        conn.execute(
            "UPDATE maps_companies SET email=?, email_source=?, email_verified=?, "
            "email_verified_at=? WHERE id=?",
            (row.get("email"), row.get("email_source"), row.get("email_verified"),
             row.get("email_verified_at"), row["id"]),
        )
    conn.commit()

    usable = sum(
        1 for r in rows
        if r.get("email") and r.get("email_source") in ("found", "found_offdomain")
    )

    print(f"Total rows: {len(rows)}")
    print(f"Rejected placeholder: {n_placeholder}")
    print(f"Rejected shared (dev/agency): {n_shared}, shared emails found: {shared_emails}")
    print(f"Downgraded found_offdomain: {n_offdomain}")
    print(f"Rejected no_guess (MX/franchise/builder/lead-mill): {n_no_guess}")
    print(f"Final usable emails (found + found_offdomain): {usable}")


if __name__ == "__main__":
    main()
