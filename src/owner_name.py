"""Owner-name validation and source classification (config/nav_stopwords.yml).

Stage3 owner_name_found extraction was picking up nav/menu/header/footer text
("Tenant Landlord", "Should Know", "Resources More") instead of real person
names. is_valid_person_name() is a pure filter applied to extracted values;
classify_owner_name_source() defines the owner_name_source column mechanics
('license' | 'about_page' | 'none')."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
NAV_STOPWORDS_PATH = ROOT / "config" / "nav_stopwords.yml"

ABOUT_SECTION_HINTS = ("about", "team", "contact")
NAV_SECTION_HINTS = ("nav", "header", "footer", "menu")

_NAME_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z'-]*$")


@lru_cache(maxsize=1)
def _load_config() -> dict:
    data = yaml.safe_load(NAV_STOPWORDS_PATH.read_text()) or {}
    data["nav_words"] = {w.lower() for w in data.get("nav_words", [])}
    data["nav_phrases"] = {p.lower() for p in data.get("nav_phrases", [])}
    return data


def is_valid_person_name(text: str | None) -> bool:
    """True if text plausibly a real person's name, not a nav/menu/heading
    fragment. Rejects: explicit nav stop-list phrases/words, all-caps
    "names" (likely headings), digits/special chars beyond hyphen/apostrophe,
    wrong token count, and generic nav-word title-case phrases even if not
    explicitly listed."""
    if not text:
        return False
    text = text.strip()
    if not text:
        return False

    cfg = _load_config()

    if text.lower() in cfg["nav_phrases"]:
        return False

    if text.isupper():
        return False

    tokens = [t for t in re.split(r"\s+", text) if t]
    if not (2 <= len(tokens) <= 3):
        return False

    for tok in tokens:
        if not _NAME_TOKEN_RE.match(tok):
            return False
        if not tok[0].isupper():
            return False

    # Reject if every token matches the nav-word list (general rule, catches
    # unlisted nav phrases built from common nav vocabulary).
    if all(tok.lower() in cfg["nav_words"] for tok in tokens):
        return False

    return True


def classify_owner_name_source(has_license_match: bool, extracted_from_section: str | None) -> str:
    """Return 'license' | 'about_page' | 'none' for the owner_name_source
    column. License match always wins. Otherwise 'about_page' only when the
    text is known to come from an about/team/contact section (never
    header/footer/nav); else 'none'."""
    if has_license_match:
        return "license"
    if extracted_from_section and extracted_from_section.lower() in ABOUT_SECTION_HINTS:
        return "about_page"
    return "none"
