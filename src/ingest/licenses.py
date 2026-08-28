"""Parse DBPR mold-services extract -> filter active -> dedupe individuals to
companies -> flag franchises -> write companies table.

Stage 1 pipeline: raw actor records -> filtered records -> grouped companies.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from .. import config, db

MOLD_TYPE_CODES = set(config.STATES["FL"]["occupation_codes"].keys())  # MRSR, MRSA


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().upper())


def filter_active(records: list[dict]) -> list[dict]:
    """Keep only MRSR/MRSA records with an acceptable status."""
    out = []
    for r in records:
        if r.get("licenseType") not in MOLD_TYPE_CODES:
            continue
        primary = _norm(r.get("primaryStatus")).lower()
        secondary = _norm(r.get("secondaryStatus")).lower()
        if primary not in config.ACTIVE_PRIMARY_STATUSES:
            continue
        if secondary in config.EXCLUDE_SECONDARY_STATUSES:
            continue
        if secondary and secondary not in config.KEEP_SECONDARY_STATUSES:
            continue
        out.append(r)
    return out


def _group_key(r: dict) -> str:
    dba = _norm(r.get("dbaName"))
    if dba:
        return f"dba:{dba}"
    addr = _norm(r.get("addressLine1"))
    city = _norm(r.get("city"))
    zipc = _norm(r.get("zip"))
    return f"addr:{addr}|{city}|{zipc}"


def _is_franchise(name: str) -> bool:
    upper = _norm(name)
    return any(f in upper for f in config.FRANCHISE_NAMES)


def dedupe_to_companies(records: list[dict]) -> list[dict]:
    """Group individual license rows into company-level rows."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        groups[_group_key(r)].append(r)

    companies = []
    for key, rows in groups.items():
        rows_sorted = sorted(rows, key=lambda r: r.get("licenseNumber") or "")
        primary = rows_sorted[0]
        dba = primary.get("dbaName")
        name_for_flag = dba or primary.get("licenseeName") or ""
        addr = ", ".join(
            filter(None, [primary.get("addressLine1"), primary.get("addressLine2")])
        )
        companies.append(
            {
                "state": "FL",
                "license_number": primary.get("licenseNumber"),
                "license_type": primary.get("licenseType"),
                "licensee_name": primary.get("licenseeName"),
                "dba_name": dba,
                "address": addr,
                "city": primary.get("city"),
                "county": primary.get("countyName"),
                "zip": primary.get("zip"),
                "status": f"{primary.get('primaryStatus')}/{primary.get('secondaryStatus')}",
                "expiration_date": primary.get("expirationDate"),
                "source": "dbpr_apify:FL:mold-related-services",
                "franchise_flag": 1 if _is_franchise(name_for_flag) else 0,
                "principal_name": primary.get("licenseeName"),
                "licensee_count": len(rows),
            }
        )
    return companies


def ingest(records: list[dict]) -> dict[str, Any]:
    """Full pipeline: filter -> dedupe -> write. Returns summary counts."""
    total_raw = len(records)
    active = filter_active(records)
    companies = dedupe_to_companies(active)

    conn = db.get_connection()
    db.run_migrations(conn)
    written = 0
    for co in companies:
        if not co["license_number"]:
            continue
        if db.find_company_by_license(conn, co["license_number"]):
            continue
        db.insert_company(conn, **co)
        written += 1
    franchise_count = sum(1 for c in companies if c["franchise_flag"])
    conn.close()

    return {
        "total_raw": total_raw,
        "active_after_filter": len(active),
        "companies_after_dedupe": len(companies),
        "companies_written": written,
        "franchise_count": franchise_count,
    }
