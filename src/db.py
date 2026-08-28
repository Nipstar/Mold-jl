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
"""


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


if __name__ == "__main__":
    run_migrations()
    print(f"Migrations applied. DB at {config.DB_PATH}")
