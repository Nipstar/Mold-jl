"""SerpAPI google_maps engine client -- fallback provider for the geo/trade
sweep in stage2_places.py, used only if the DataForSEO Maps endpoint proves
awkward/unavailable for a given sweep. Normalizes to the same shape as
dataforseo.get_maps_results() ({"local_results": [...], "raw": data}) so the
sweep code has a single downstream field-mapping path regardless of provider.

Auth: SERPAPI_KEY, loaded from process env or the same .env.local search path
as the DataForSEO client.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests

SERPAPI_URL = "https://serpapi.com/search"


class SerpAPIError(RuntimeError):
    pass


def _load_dotenv():
    if os.environ.get("SERPAPI_KEY"):
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
            if os.environ.get("SERPAPI_KEY"):
                break


class SerpAPIClient:
    """Minimal client for SerpAPI's google_maps engine."""

    def __init__(self, api_key: str | None = None):
        _load_dotenv()
        self.api_key = api_key or os.environ.get("SERPAPI_KEY", "").strip()
        if not self.api_key:
            print("ERROR: SERPAPI_KEY not set in env or .env.local", file=sys.stderr)

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _get(self, params: dict) -> dict:
        resp = requests.get(
            SERPAPI_URL,
            params={**params, "engine": "google_maps", "api_key": self.api_key},
            timeout=30,
        )
        data = resp.json()
        if data.get("error"):
            raise SerpAPIError(f"SerpAPI error: {data['error']}")
        return data


def get_maps_results(client: SerpAPIClient, keyword: str, location: str = "Florida, United States") -> dict:
    """Raw google_maps search response for `keyword`, normalized to the same
    shape as dataforseo.get_maps_results()."""
    data = client._get({"q": keyword, "location": location, "type": "search"})

    items = data.get("local_results") or []
    local_results = []
    for item in items:
        local_results.append({
            "title": item.get("title"),
            "address": item.get("address"),
            "rating": item.get("rating"),
            "reviews": item.get("reviews"),
            "phone": item.get("phone"),
            "website": item.get("website"),
            "place_id": item.get("place_id") or item.get("data_id"),
            "open_state": (item.get("hours") or None),
            "hours": item.get("operating_hours"),
            "types": item.get("type") and [item.get("type")] or [],
        })

    return {"local_results": local_results, "raw": data}
