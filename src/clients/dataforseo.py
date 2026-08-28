"""DataForSEO Business Data / Google Maps search client.

Ported from geo-slab's scripts/dataforseo_provider.py pattern (Basic Auth,
.env.local loader) but pointed at the Google Maps SERP endpoint instead of
organic SERP -- this is the direct DataForSEO analogue of SerpAPI's
google_maps engine used in stage2_places.py.

Endpoint: v3/serp/google/maps/live/advanced
Docs: https://docs.dataforseo.com/v3/serp/google/maps/live/advanced/
Cost: ~$0.002-0.003 per call (live advanced, standard priority) -- billed
per task regardless of result count.

Auth: DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD, loaded from process env or
from the first of these .env.local files found: this repo's own .env,
geo-slab/.env.local (sibling checkout), or $HOME/.env.local.

Caching: every raw response is cached in api_cache (source=
'dataforseo_maps'), keyed by a hash of the query string. Callers must check
the cache before calling -- this module never re-calls for a cached key.
"""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

import requests

DATAFORSEO_MAPS_URL = "https://api.dataforseo.com/v3/serp/google/maps/live/advanced"

# 2840 = United States. Query text already carries city + "FL" so a broad
# US-level location code is sufficient (matches stage2_places.py's approach
# of folding city into the query string rather than the API's location arg).
US_LOCATION_CODE = 2840


class DataForSEOError(RuntimeError):
    """Raised on a non-20000 status_code at either the top level or task level."""


def _load_dotenv():
    if os.environ.get("DATAFORSEO_LOGIN") and os.environ.get("DATAFORSEO_PASSWORD"):
        return
    candidates = [
        Path(__file__).resolve().parent.parent.parent / ".env",
        Path(__file__).resolve().parent.parent.parent.parent / "geo-slab" / ".env.local",
        Path.home() / ".env.local",
    ]
    for p in candidates:
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))
            if os.environ.get("DATAFORSEO_LOGIN") and os.environ.get("DATAFORSEO_PASSWORD"):
                break


class DataForSEOClient:
    """HTTP Basic Auth client for DataForSEO's Maps SERP API."""

    def __init__(self, login: str | None = None, password: str | None = None):
        _load_dotenv()
        self.login = login or os.environ.get("DATAFORSEO_LOGIN", "").strip()
        self.password = password or os.environ.get("DATAFORSEO_PASSWORD", "").strip()
        if not self.login or not self.password:
            print(
                "ERROR: DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD not set in env or .env.local",
                file=sys.stderr,
            )

    @property
    def configured(self) -> bool:
        return bool(self.login and self.password)

    def _auth_header(self) -> str:
        token = base64.b64encode(f"{self.login}:{self.password}".encode()).decode()
        return f"Basic {token}"

    def _post(self, payload: list[dict]) -> dict:
        resp = requests.post(
            DATAFORSEO_MAPS_URL,
            data=json.dumps(payload),
            headers={
                "Authorization": self._auth_header(),
                "Content-Type": "application/json",
                "User-Agent": "jl-mold-fl-dataforseo/1.0",
            },
            timeout=30,
        )
        data = resp.json()
        if data.get("status_code") != 20000:
            raise DataForSEOError(
                f"DataForSEO top-level error {data.get('status_code')}: "
                f"{data.get('status_message')}"
            )
        return data


def get_maps_results(
    client: DataForSEOClient,
    keyword: str,
    location_code: int = US_LOCATION_CODE,
    language_code: str = "en",
    device: str = "desktop",
    depth: int = 20,
) -> dict:
    """Raw Maps search response for `keyword`, normalised to the same shape
    stage2's serpapi_maps_search returns ({"local_results": [...]}) so the
    matcher's downstream field-mapping code stays a single code path.

    Each normalised item: title, address, rating, reviews, phone, website,
    place_id, open_state, hours (periods, if DataForSEO returns them),
    types.
    """
    payload = [{
        "keyword": keyword,
        "location_code": location_code,
        "language_code": language_code,
        "device": device,
        "depth": depth,
    }]
    data = client._post(payload)

    tasks = data.get("tasks") or []
    if not tasks:
        raise DataForSEOError("DataForSEO response had no tasks")
    task = tasks[0]
    if task.get("status_code") != 20000:
        raise DataForSEOError(
            f"DataForSEO task error {task.get('status_code')}: {task.get('status_message')}"
        )

    results = task.get("result") or []
    items = (results[0].get("items") if results else None) or []

    local_results = []
    for item in items:
        if item.get("type") not in ("maps_search", "local_pack"):
            continue
        rating = item.get("rating") or {}
        local_results.append({
            "title": item.get("title"),
            "address": item.get("address"),
            "rating": rating.get("value"),
            "reviews": rating.get("votes_count"),
            "phone": item.get("phone"),
            "website": item.get("url") or item.get("domain"),
            "place_id": item.get("place_id"),
            "open_state": (item.get("work_time") or {}).get("current_status")
            or ("Permanently closed" if item.get("is_permanently_closed") else None),
            "hours": item.get("work_time"),
            "types": [item.get("category")] + (item.get("additional_categories") or [])
            if item.get("category") else (item.get("additional_categories") or []),
        })

    return {"local_results": local_results, "raw": data}
