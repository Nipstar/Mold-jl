"""Stage A: match_franchise_brand must catch real franchise/national-chain
listings while never falsely matching independents whose name merely
contains a generic industry word like 'Restoration'. See config/brands.yml."""
from __future__ import annotations

from src.relevance import match_franchise_brand


def test_honest_restoration_is_not_a_franchise():
    assert match_franchise_brand("Honest Restoration", None) is None


def test_barakat_restoration_is_not_a_franchise():
    assert match_franchise_brand("Barakat Restoration", None) is None


def test_red_carpet_flood_restoration_llc_is_not_a_franchise():
    assert match_franchise_brand("Red Carpet Flood Restoration LLC", None) is None


def test_restopros_of_northeast_florida_matches():
    assert match_franchise_brand("RestoPros of Northeast Florida", None) == "RestoPros"


def test_rytech_bare_name_matches():
    assert match_franchise_brand("Rytech", None) == "Rytech"


def test_1800_water_damage_of_jacksonville_east_matches():
    assert match_franchise_brand(
        "1-800 Water Damage of Jacksonville East", None
    ) == "1-800 Water Damage"


def test_onerestore_bare_name_matches():
    assert match_franchise_brand("OneRestore", None) == "OneRestore"


def test_website_domain_match():
    assert match_franchise_brand("Some Local Co", "https://www.servpro.com/locations/fl") == "SERVPRO"


def test_none_name_and_website_returns_none():
    assert match_franchise_brand("", None) is None
