"""DBPR license extract client.

Primary path: Apify actor cblu/florida-license-records-scraper
(actor id nT0apWbN1rH19DD6t, PAY_PER_EVENT ~$0.004/license record).

Caching: every raw record returned by the actor is cached locally, keyed by
license_number, in both a JSON file under cache/ (for human inspection / full
run replay) and the api_cache sqlite table (for per-license reuse). A rerun
with an unchanged request signature is served entirely from the cache/ file
and spends zero Apify credits.

Fallback (stubbed): headless-browser fetch of the raw DBPR extract CSV. The
site is Cloudflare-gated (plain curl -> 403), so this needs Playwright with a
persistent context or the Apify rag-web-browser actor in raw-content mode.
Not implemented here — call fetch_fallback_csv() only if the actor path is
validated as unusable; it raises NotImplementedError until built.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import requests

from .. import config, db

APIFY_BASE = "https://api.apify.com/v2"
ACTOR_ID = config.STATES["FL"]["extract_source"]["apify_actor_id"]
COST_PER_RECORD = config.STATES["FL"]["extract_source"]["cost_per_record_usd"]


class DbprFetchError(RuntimeError):
    pass


def _cache_key(input_payload: dict[str, Any]) -> str:
    blob = json.dumps(input_payload, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _cache_path(key: str) -> Path:
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return config.CACHE_DIR / f"dbpr_fl_{key}.json"


def run_actor_extract(
    profession: str = "mold-related-services",
    status: str = "any",
    max_results: int = 20000,
    force_refresh: bool = False,
) -> tuple[list[dict], float]:
    """Run the Apify actor (or serve from cache) and return (records, spend_usd).

    spend_usd is 0.0 when served from the local cache file — that's the whole
    point of caching: reruns cost nothing.
    """
    payload = {"profession": profession, "status": status, "maxResults": max_results}
    key = _cache_key(payload)
    path = _cache_path(key)

    if path.exists() and not force_refresh:
        records = json.loads(path.read_text())
        return records, 0.0

    if not config.APIFY_TOKEN:
        raise DbprFetchError("APIFY_TOKEN not set in environment/.env")

    url = f"{APIFY_BASE}/acts/{ACTOR_ID}/run-sync-get-dataset-items"
    resp = requests.post(
        url,
        params={"token": config.APIFY_TOKEN},
        json=payload,
        timeout=600,
    )
    if resp.status_code >= 300:
        raise DbprFetchError(f"Apify actor call failed: {resp.status_code} {resp.text[:500]}")
    records = resp.json()

    path.write_text(json.dumps(records, indent=2))

    conn = db.get_connection()
    db.run_migrations(conn)
    fetched_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    for rec in records:
        lic = rec.get("licenseNumber")
        if not lic:
            continue
        db.cache_put(conn, f"dbpr:FL:{lic}", "dbpr_apify", json.dumps(rec), None)
    conn.close()

    spend = len(records) * COST_PER_RECORD
    return records, spend


def fetch_fallback_csv() -> list[dict]:
    """Fallback path: headless-browser pull of the raw extract CSV.
    Stub — only build this out if the actor path proves unusable."""
    raise NotImplementedError(
        "Fallback not implemented. Needs Playwright persistent context or "
        "Apify rag-web-browser actor (raw content mode) against: "
        f"{config.STATES['FL']['extract_source']['fallback_url']}"
    )
