"""lead_mill_suspect scoring (Stage 2.5 data-quality pass, part 2).

BUG fixed by this module: lead_mill_suspect was 0 on nearly every row
despite many top pain_score rows being obvious lead mills -- the column
existed (src/db.py DEDUP_COLUMNS) but nothing ever computed it. This module
adds the actual scoring so the column is meaningful.

compute_lead_mill_score() returns a 0-100 weighted score plus the list of
signal reasons that fired. Weights (see config/lead_mill.yml for the
threshold, default 40):

  - keyword_stuffed_name   +20  strongest single tell: a generic
    "<Trade word> <Trade word> <City>" name (optionally with a repeated
    trade word, e.g. "Water Damage Restoration Jacksonville") or a bare
    letter-code agency prefix (e.g. "ZND-") is very rarely how a real
    independent names itself -- these are template names auto-generated
    for SEO-farm listings.
  - throwaway_tld_or_builder_only   +15  a throwaway TLD (.online/.space/
    etc) is a strong tell by itself; a builder/social domain (wixsite.com,
    Instagram, etc) as the ONLY web presence is a weaker but real tell, so
    both land in the same 15-pt bucket rather than throwaway TLD getting
    its own higher weight -- either way the business has no owned web
    presence.
  - out_of_area   +15  reuses Stage B's is_out_of_area signal (already
    computed independently) -- an out-of-area business posing as local is
    exactly the lead-mill pattern (or at minimum not a real local
    prospect), so it's scored here too, not just used as a separate filter.
  - shared_address_or_phone   +25  highest weight after name -- two
    different-named "businesses" at the same street address/phone is
    almost never legitimate (shared office at most), and is the hardest
    signal to fake around, so it counts the most.
  - thin_review_farm_shape   +25  1-5 reviews + 5.0 rating + no real
    address + no real website, all four at once, is a distinctive
    review-farm/directory-listing shape distinct from "new small business"
    (which usually has at least one of address or website). Weighted equal
    to shared_address since both are strong compound signals, unlike the
    single-fact signals above.

  Max: 20 + 15 + 15 + 25 + 25 = 100.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from .location import has_street_address, is_out_of_area

ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = ROOT / "config" / "lead_mill.yml"

_TRADE_WORDS = [
    "water damage", "mold remediation", "mold removal", "mold inspection",
    "fire damage", "restoration", "remediation", "removal", "cleanup",
    "damage restoration",
]

_config_cache = None


def _load_config() -> dict:
    global _config_cache
    if _config_cache is None:
        with open(_CONFIG_PATH) as f:
            _config_cache = yaml.safe_load(f)
    return _config_cache


def threshold() -> int:
    return int(_load_config().get("threshold", 40))


def _normalize_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    return digits or None


def _normalize_address(address: str | None) -> str | None:
    if not address:
        return None
    return re.sub(r"\s+", " ", address.strip().lower())


def _is_keyword_stuffed_name(name: str | None) -> bool:
    if not name:
        return False
    lower = name.lower()

    cfg = _load_config()
    for prefix in cfg.get("generic_prefixes", []):
        if name.upper().startswith(prefix.upper()):
            return True

    # Count distinct trade phrases, dropping any phrase that is a substring
    # of another matched phrase (e.g. "remediation" inside "mold
    # remediation") so one trade concept doesn't double-count. A real
    # business name rarely stacks two-plus distinct generic trade concepts
    # plus a bare city name with no distinguishing brand word.
    matched = [w for w in _TRADE_WORDS if w in lower]
    distinct = [w for w in matched if not any(w != other and w in other for other in matched)]
    return len(distinct) >= 2


def _is_throwaway_tld_or_builder_only(website: str | None) -> bool:
    if not website:
        return False
    lower = website.lower()
    cfg = _load_config()

    for tld in cfg.get("throwaway_tlds", []):
        if lower.rstrip("/").endswith(tld):
            return True

    for domain in cfg.get("builder_social_domains", []):
        if domain in lower:
            return True

    return False


def compute_lead_mill_score(row: dict, all_rows_for_dup_check: list[dict]) -> tuple[int, list[str]]:
    """Compute lead_mill_score (0-100) and the list of reason tags that
    fired, for `row` given the full candidate set (for shared address/phone
    duplicate checks)."""
    score = 0
    reasons = []

    if _is_keyword_stuffed_name(row.get("name")):
        score += 20
        reasons.append("keyword_stuffed_name")

    if _is_throwaway_tld_or_builder_only(row.get("website")):
        score += 15
        reasons.append("throwaway_tld")
    elif not row.get("website"):
        # No website at all is the worst case of "no real owned web
        # presence" -- same underlying signal as a throwaway-TLD/builder-
        # only site, so it shares the bucket (mutually exclusive with the
        # branch above since that branch requires a website string).
        score += 15
        reasons.append("no_website")

    ooa = row.get("out_of_area")
    if ooa or is_out_of_area(
        row.get("name"), row.get("website"), row.get("phone"), row.get("address")
    ):
        score += 15
        reasons.append("out_of_area")

    if _shares_address_or_phone(row, all_rows_for_dup_check):
        score += 25
        reasons.append("shared_address")

    rc = row.get("review_count")
    rating = row.get("rating")
    has_addr = has_street_address(row.get("address"))
    has_site = bool(row.get("website"))
    if (
        rc is not None
        and 1 <= rc <= 5
        and rating is not None
        and rating >= 5.0
        and not has_addr
        and not has_site
    ):
        score += 25
        reasons.append("thin_review_farm_shape")

    return score, reasons


def _shares_address_or_phone(row: dict, all_rows: list[dict]) -> bool:
    name = row.get("name")
    addr = _normalize_address(row.get("address"))
    phone = _normalize_phone(row.get("phone"))

    for other in all_rows:
        if other is row:
            continue
        if other.get("name") == name:
            continue  # same business, not a dup-under-different-name signal
        other_addr = _normalize_address(other.get("address"))
        other_phone = _normalize_phone(other.get("phone"))
        if addr and other_addr and addr == other_addr:
            return True
        if phone and other_phone and phone == other_phone:
            return True
    return False


def is_lead_mill_suspect(score: int) -> bool:
    return score >= threshold()
