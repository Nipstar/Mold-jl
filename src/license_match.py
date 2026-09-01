"""Derive maps_companies enrichment from a DBPR license match (spec item 9).

When license_verified=1 (a matched_license_number exists), the matched DBPR
`companies` row is authoritative for three things Google-derived signals
can't reliably give us:

  - primary_service: license CLASS (MRSR -> remediation, MRSA ->
    assessment_only) beats Google category text.
  - license_class: MRSR | MRSA | both | none, exposed on maps_companies so
    downstream reporting doesn't need to re-join `companies`. 'both' when
    the matched licensee (by principal_name) holds licenses of both types.
  - address fallback: DBPR business address, used only when the Maps
    listing itself has no usable street address (has_street_address=0).
"""
from __future__ import annotations

CLASS_TO_SERVICE = {
    "MRSR": "remediation",
    "MRSA": "assessment_only",
}


def primary_service_from_license_type(license_type: str | None) -> str | None:
    """MRSR -> remediation, MRSA -> assessment_only. Unknown/None -> None."""
    if not license_type:
        return None
    return CLASS_TO_SERVICE.get(license_type)


def compute_license_class(conn, matched_license_number: str | None) -> str:
    """'MRSR' | 'MRSA' | 'both' | 'none'. 'both' when the matched licensee
    (matched by principal_name on the matched company row) holds license
    records of both types -- e.g. one person licensed as both remediator and
    assessor."""
    if not matched_license_number:
        return "none"
    row = conn.execute(
        "SELECT license_type, principal_name FROM companies WHERE license_number = ?",
        (matched_license_number,),
    ).fetchone()
    if not row:
        return "none"
    principal_name = row["principal_name"]
    if not principal_name:
        return row["license_type"] or "none"
    types = {
        r["license_type"]
        for r in conn.execute(
            "SELECT DISTINCT license_type FROM companies WHERE principal_name = ?",
            (principal_name,),
        ).fetchall()
        if r["license_type"]
    }
    if len(types) > 1:
        return "both"
    return row["license_type"] or "none"


def get_license_address(conn, matched_license_number: str | None) -> dict | None:
    """DBPR business address for the matched license, for use as a fallback
    when the Maps listing has no usable street address. None if there's no
    match or the DBPR row itself has no address on file."""
    if not matched_license_number:
        return None
    row = conn.execute(
        "SELECT address, city, county, zip FROM companies WHERE license_number = ?",
        (matched_license_number,),
    ).fetchone()
    if not row or not (row["address"] and row["address"].strip()):
        return None
    return {
        "address": row["address"],
        "city": row["city"],
        "county": row["county"],
        "zip": row["zip"],
    }
