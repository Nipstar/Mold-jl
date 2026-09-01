"""Migration adding email_verified/email_verified_at/email_evidence_url to
maps_companies. See src/db.run_email_quality_migration."""
from __future__ import annotations

import sqlite3

from src import db


def _fresh_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA)
    db.run_maps_companies_migration(conn)
    return conn


def test_migration_adds_email_verified_columns():
    conn = _fresh_conn()
    db.run_email_quality_migration(conn)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(maps_companies)")}
    assert "email_verified" in cols
    assert "email_verified_at" in cols
    assert "email_evidence_url" in cols


def test_migration_is_idempotent():
    conn = _fresh_conn()
    db.run_email_quality_migration(conn)
    db.run_email_quality_migration(conn)  # must not raise
    cols = [row["name"] for row in conn.execute("PRAGMA table_info(maps_companies)")]
    assert cols.count("email_verified") == 1
