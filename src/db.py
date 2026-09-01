"""SQLite data layer for jl-mold-fl. Thin wrapper, no ORM."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    state TEXT NOT NULL,
    license_number TEXT UNIQUE,
    license_type TEXT,
    licensee_name TEXT,
    dba_name TEXT,
    address TEXT,
    city TEXT,
    county TEXT,
    zip TEXT,
    status TEXT,
    expiration_date TEXT,
    source TEXT,
    franchise_flag INTEGER DEFAULT 0,
    principal_name TEXT,
    licensee_count INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS api_cache (
    cache_key TEXT PRIMARY KEY,
    source TEXT,
    response TEXT,
    fetched_at TEXT,
    expires_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_companies_state ON companies(state);
CREATE INDEX IF NOT EXISTS idx_companies_status ON companies(status);
CREATE INDEX IF NOT EXISTS idx_companies_franchise ON companies(franchise_flag);

CREATE TABLE IF NOT EXISTS enrichment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    place_found INTEGER DEFAULT 0,
    rating REAL,
    review_count INTEGER,
    most_recent_review_date TEXT,
    categories TEXT,
    website TEXT,
    phone TEXT,
    business_status TEXT,
    hours_listed TEXT,
    in_local_pack INTEGER DEFAULT 0,
    match_confidence TEXT,
    source TEXT,
    raw_json TEXT,
    fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_enrichment_company ON enrichment(company_id);
"""

# Stage 2-5 enrichment columns, added via ALTER since companies table already
# exists from Stage 1. Idempotent (checks pragma first).
ENRICH_COLUMNS = {
    "place_id": "TEXT",
    "place_found": "INTEGER DEFAULT 0",
    "match_confidence": "TEXT",
    "match_source": "TEXT",
    "places_rating": "REAL",
    "places_review_count": "INTEGER",
    "places_website": "TEXT",
    "places_phone": "TEXT",
    "places_hours_json": "TEXT",
    "places_types": "TEXT",
    "business_status": "TEXT",
    "in_local_pack": "INTEGER DEFAULT 0",
    "latest_review_age_months": "INTEGER",
    "website": "TEXT",
    "owner_name_found": "TEXT",
    "owner_confirmed": "INTEGER DEFAULT 0",
    "email": "TEXT",
    "email_source": "TEXT",
    "booking_widget": "INTEGER DEFAULT 0",
    "chat_widget": "INTEGER DEFAULT 0",
    "emergency_247": "INTEGER DEFAULT 0",
    "form_only_contact": "INTEGER DEFAULT 0",
    "pain_score": "INTEGER",
    "segment": "TEXT",
    "score_notes": "TEXT",
    "enrich_stage": "INTEGER DEFAULT 1",
}


def run_enrich_migrations(conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    conn = conn or get_connection()
    try:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(companies)")}
        for col, coltype in ENRICH_COLUMNS.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE companies ADD COLUMN {col} {coltype}")
        conn.commit()
    finally:
        if own:
            conn.close()


MAPS_COMPANIES_SCHEMA = """
CREATE TABLE IF NOT EXISTS maps_companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id TEXT UNIQUE,
    name TEXT,
    address TEXT,
    city TEXT,
    county TEXT,
    zip TEXT,
    phone TEXT,
    website TEXT,
    rating REAL,
    review_count INTEGER,
    categories TEXT,
    business_status TEXT,
    hours_json TEXT,
    franchise_flag INTEGER DEFAULT 0,
    license_verified INTEGER DEFAULT 0,
    matched_license_number TEXT,
    matched_principal_name TEXT,
    match_confidence TEXT,
    source_sweeps TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_maps_companies_city ON maps_companies(city);
CREATE INDEX IF NOT EXISTS idx_maps_companies_county ON maps_companies(county);
CREATE INDEX IF NOT EXISTS idx_maps_companies_license_verified ON maps_companies(license_verified);
"""


def run_maps_companies_migration(conn: sqlite3.Connection | None = None) -> None:
    """Additive migration for the maps-first pivot. Does not touch companies/
    enrichment tables. Idempotent (CREATE TABLE/INDEX IF NOT EXISTS)."""
    own = conn is None
    conn = conn or get_connection()
    try:
        conn.executescript(MAPS_COMPANIES_SCHEMA)
        conn.commit()
    finally:
        if own:
            conn.close()


DEDUP_COLUMNS = {
    "dup_group_id": "INTEGER",
    "is_duplicate": "INTEGER DEFAULT 0",
    "lead_mill_suspect": "INTEGER DEFAULT 0",
}


def run_dedup_migration(conn: sqlite3.Connection | None = None) -> None:
    """Additive migration: adds dup_group_id/is_duplicate/lead_mill_suspect
    columns to maps_companies for the Stage-2.5 data-quality pass. Does not
    delete or overwrite any existing row data. Idempotent."""
    own = conn is None
    conn = conn or get_connection()
    try:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(maps_companies)")}
        for col, coltype in DEDUP_COLUMNS.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE maps_companies ADD COLUMN {col} {coltype}")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_maps_companies_dup_group_id ON maps_companies(dup_group_id)"
        )
        conn.commit()
    finally:
        if own:
            conn.close()


MAPS_ENRICH_COLUMNS = {
    "owner_name_found": "TEXT",
    "owner_confirmed": "INTEGER DEFAULT 0",
    "email": "TEXT",
    "email_source": "TEXT",
    "booking_widget": "INTEGER DEFAULT 0",
    "chat_widget": "INTEGER DEFAULT 0",
    "emergency_247": "INTEGER DEFAULT 0",
    "form_only_contact": "INTEGER DEFAULT 0",
    "pain_score": "INTEGER",
    "segment": "TEXT",
    "score_notes": "TEXT",
    "stage3_processed_at": "TEXT",
    "stage4_processed_at": "TEXT",
}


def run_maps_enrich_migrations(conn: sqlite3.Connection | None = None) -> None:
    """Additive migration for the Stage 3/4 pivot (see
    docs/superpowers/specs/2026-08-28-maps-first-north-florida-design.md):
    website/email enrichment + pain_score now run against maps_companies
    instead of companies. Adds only columns not already present. Idempotent."""
    own = conn is None
    conn = conn or get_connection()
    try:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(maps_companies)")}
        for col, coltype in MAPS_ENRICH_COLUMNS.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE maps_companies ADD COLUMN {col} {coltype}")
        conn.commit()
    finally:
        if own:
            conn.close()


CATEGORY_RELEVANCE_COLUMNS = {
    "category_relevant": "INTEGER DEFAULT 1",
    "google_category": "TEXT",
}


def run_category_relevance_migration(conn: sqlite3.Connection | None = None) -> None:
    """Additive migration: adds category_relevant/google_category to
    maps_companies. category_relevant flags rows whose real Google Maps
    category (from cached dataforseo_maps_sweep local_results) is NOT
    mold/restoration-related -- e.g. home inspectors, general contractors
    that were swept in as loosely-related filler for a niche/low-density
    query. Same exclusion convention as is_duplicate/lead_mill_suspect.
    Idempotent."""
    own = conn is None
    conn = conn or get_connection()
    try:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(maps_companies)")}
        for col, coltype in CATEGORY_RELEVANCE_COLUMNS.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE maps_companies ADD COLUMN {col} {coltype}")
        conn.commit()
    finally:
        if own:
            conn.close()


PRIMARY_SERVICE_COLUMNS = {
    "primary_service": "TEXT",
}


def run_primary_service_migration(conn: sqlite3.Connection | None = None) -> None:
    """Additive migration: adds primary_service to maps_companies.
    Distinguishes mold remediation companies (main JobsLocked pitch) from
    assessment/inspection-only companies (referral-partner pitch), based on
    categories/google_category signal. Values: 'remediation' | 'assessment_only'.
    Idempotent."""
    own = conn is None
    conn = conn or get_connection()
    try:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(maps_companies)")}
        for col, coltype in PRIMARY_SERVICE_COLUMNS.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE maps_companies ADD COLUMN {col} {coltype}")
        conn.commit()
    finally:
        if own:
            conn.close()


FRANCHISE_BRAND_COLUMNS = {
    "franchise_brand": "TEXT",
    "multi_location_domain": "INTEGER DEFAULT 0",
}


def run_franchise_brand_migration(conn: sqlite3.Connection | None = None) -> None:
    """Additive migration: adds franchise_brand/multi_location_domain to
    maps_companies (Stage A pipeline hardening, config-driven brand
    matching -- see src/relevance.py, config/brands.yml). franchise_brand
    is the canonical matched brand name or NULL; franchise_flag is derived
    from it (franchise_flag = franchise_brand IS NOT NULL). Idempotent."""
    own = conn is None
    conn = conn or get_connection()
    try:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(maps_companies)")}
        for col, coltype in FRANCHISE_BRAND_COLUMNS.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE maps_companies ADD COLUMN {col} {coltype}")
        conn.commit()
    finally:
        if own:
            conn.close()


LOCATION_SOURCE_COLUMNS = {
    "has_street_address": "INTEGER",
    "location_source": "TEXT",
    "out_of_area": "INTEGER DEFAULT 0",
}


def run_location_source_migration(conn: sqlite3.Connection | None = None) -> None:
    """Additive migration: adds has_street_address/location_source/out_of_area
    to maps_companies (Stage B: fixes rows with no street address getting
    the search-grid-point's city/county assigned -- see src/location.py).
    Idempotent."""
    own = conn is None
    conn = conn or get_connection()
    try:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(maps_companies)")}
        for col, coltype in LOCATION_SOURCE_COLUMNS.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE maps_companies ADD COLUMN {col} {coltype}")
        conn.commit()
    finally:
        if own:
            conn.close()


LEAD_MILL_COLUMNS = {
    "lead_mill_score": "INTEGER",
    "lead_mill_reasons": "TEXT",
}


def run_lead_mill_migration(conn: sqlite3.Connection | None = None) -> None:
    """Additive migration: adds lead_mill_score/lead_mill_reasons to
    maps_companies (Stage 2.5 part 2 -- see src/lead_mill.py).
    lead_mill_suspect already exists from DEDUP_COLUMNS/run_dedup_migration;
    this only adds the score/reasons detail columns feeding it. Idempotent."""
    own = conn is None
    conn = conn or get_connection()
    try:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(maps_companies)")}
        for col, coltype in LEAD_MILL_COLUMNS.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE maps_companies ADD COLUMN {col} {coltype}")
        conn.commit()
    finally:
        if own:
            conn.close()


EMAIL_QUALITY_COLUMNS = {
    "email_verified": "TEXT",
    "email_verified_at": "TEXT",
    "email_evidence_url": "TEXT",
}


def run_email_quality_migration(conn: sqlite3.Connection | None = None) -> None:
    """Additive migration: adds email_verified/email_verified_at/
    email_evidence_url to maps_companies (email enrichment cleanup, see
    src/email_quality.py, scripts/backfill_email_quality.py). MX-only
    verification -- email_verified is 'invalid'/'unknown' only, never
    'valid'/'catch_all' (those require a live SMTP handshake, deliberately
    not done). Idempotent."""
    own = conn is None
    conn = conn or get_connection()
    try:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(maps_companies)")}
        for col, coltype in EMAIL_QUALITY_COLUMNS.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE maps_companies ADD COLUMN {col} {coltype}")
        conn.commit()
    finally:
        if own:
            conn.close()


OWNER_NAME_SOURCE_COLUMNS = {
    "owner_name_source": "TEXT",
}


def run_owner_name_source_migration(conn: sqlite3.Connection | None = None) -> None:
    """Additive migration: adds owner_name_source to maps_companies (see
    src/owner_name.py, scripts/backfill_owner_name.py). Values:
    'license' | 'about_page' | 'none'. Idempotent."""
    own = conn is None
    conn = conn or get_connection()
    try:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(maps_companies)")}
        for col, coltype in OWNER_NAME_SOURCE_COLUMNS.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE maps_companies ADD COLUMN {col} {coltype}")
        conn.commit()
    finally:
        if own:
            conn.close()


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def run_migrations(conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    conn = conn or get_connection()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        if own:
            conn.close()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --- api_cache ---------------------------------------------------------
def cache_get(conn: sqlite3.Connection, cache_key: str) -> Optional[str]:
    row = conn.execute(
        "SELECT response FROM api_cache WHERE cache_key = ?", (cache_key,)
    ).fetchone()
    return row["response"] if row else None


def cache_put(conn: sqlite3.Connection, cache_key: str, source: str, response: str,
              expires_at: str | None = None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO api_cache (cache_key, source, response, fetched_at, expires_at) "
        "VALUES (?,?,?,?,?)",
        (cache_key, source, response, _now(), expires_at),
    )
    conn.commit()


# --- companies -----------------------------------------------------------
def insert_company(conn: sqlite3.Connection, **fields: Any) -> int:
    cols = ", ".join(fields.keys())
    marks = ", ".join(["?"] * len(fields))
    cur = conn.execute(
        f"INSERT OR IGNORE INTO companies ({cols}) VALUES ({marks})",
        tuple(fields.values()),
    )
    conn.commit()
    return int(cur.lastrowid)


def find_company_by_license(conn: sqlite3.Connection, license_number: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM companies WHERE license_number = ?", (license_number,)
    ).fetchone()


def count_companies(conn: sqlite3.Connection, state: str | None = None) -> int:
    if state:
        return conn.execute(
            "SELECT COUNT(*) c FROM companies WHERE state = ?", (state,)
        ).fetchone()["c"]
    return conn.execute("SELECT COUNT(*) c FROM companies").fetchone()["c"]


def sample_companies(conn: sqlite3.Connection, n: int = 10) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM companies ORDER BY id LIMIT ?", (n,)).fetchall()


# --- maps_companies -------------------------------------------------------
def upsert_maps_company(conn: sqlite3.Connection, place_id: str, **fields: Any) -> int:
    """Insert a new maps_companies row for `place_id`, or update the mutable
    fields (categories/source_sweeps merge is the caller's job -- pass the
    already-merged value in) on an existing one. Returns the row id."""
    existing = conn.execute(
        "SELECT id FROM maps_companies WHERE place_id = ?", (place_id,)
    ).fetchone()
    if existing:
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(
                f"UPDATE maps_companies SET {sets} WHERE place_id = ?",
                (*fields.values(), place_id),
            )
            conn.commit()
        return int(existing["id"])
    cols = ["place_id", *fields.keys()]
    marks = ", ".join(["?"] * len(cols))
    cur = conn.execute(
        f"INSERT INTO maps_companies ({', '.join(cols)}) VALUES ({marks})",
        (place_id, *fields.values()),
    )
    conn.commit()
    return int(cur.lastrowid)


def find_maps_company_by_place_id(conn: sqlite3.Connection, place_id: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM maps_companies WHERE place_id = ?", (place_id,)
    ).fetchone()


def count_maps_companies(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) c FROM maps_companies").fetchone()["c"]


if __name__ == "__main__":
    run_migrations()
    run_maps_companies_migration()
    print(f"Migrations applied. DB at {config.DB_PATH}")
