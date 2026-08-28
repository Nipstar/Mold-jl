"""Config: state registry, DB path, Apify actor settings, franchise list."""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "pipeline.db"
CACHE_DIR = ROOT / "cache"
EXPORT_DIR = ROOT / "export"

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")

# Franchise / national roll-up names to flag (not exclude — flagged for
# separate handling since they're not independent local operators).
FRANCHISE_NAMES = [
    "SERVPRO",
    "PUROCLEAN",
    "SERVICEMASTER",
    "PAUL DAVIS",
    "RAINBOW",
    "911 RESTORATION",
    "BELFOR",
    "ATI",
    "ROTO-ROOTER",
    "DKI",
    "RESTORATION 1",
    "ALL DRY",
    "UNITED WATER RESTORATION",
    "STANLEY STEEMER",
]

# Primary DBPR statuses we keep. DBPR's extract already drops null&void /
# delinquent / involuntarily-inactive rows entirely for "any" status pulls in
# practice, but we filter defensively since the field is present.
ACTIVE_PRIMARY_STATUSES = {"current"}
KEEP_SECONDARY_STATUSES = {"active", "inactive", "voluntarily inactive", "voluntarily-inactive"}
EXCLUDE_SECONDARY_STATUSES = {
    "null and void", "null & void", "delinquent", "involuntarily inactive",
    "involuntarily-inactive",
}

STATES = {
    "FL": {
        "name": "Florida",
        "extract_source": {
            "apify_actor_id": "nT0apWbN1rH19DD6t",
            "apify_actor_name": "cblu/florida-license-records-scraper",
            "cost_per_record_usd": 0.004,
            "fallback_url": "https://www2.myfloridalicense.com/sto/file_download/extracts/lic07mold.csv",
            "fallback_note": (
                "Site is Cloudflare-gated; plain curl gets 403. Fallback requires "
                "a real headless browser (Playwright persistent context) or the "
                "Apify rag-web-browser actor configured to return raw content."
            ),
        },
        "occupation_codes": {
            "MRSR": {"label": "Mold Remediator", "role": "primary"},
            "MRSA": {"label": "Mold Assessor", "role": "secondary"},
        },
        "franchise_names": FRANCHISE_NAMES,
    }
}
