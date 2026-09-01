"""Stage A: category_relevant requires a POSITIVE mold/restoration match on
google_category, with the denylist applied as a hard override on top (see
config/category_denylist.yml). Not being on the denylist is NOT sufficient
on its own -- that was the Stage A regression this test file guards against."""
from __future__ import annotations

from src.relevance import is_category_relevant


def test_roofing_contractor_is_not_relevant():
    assert is_category_relevant("Roofing contractor") is False


def test_computer_repair_is_not_relevant():
    assert is_category_relevant("Computer repair service") is False


def test_auto_restoration_service_is_not_relevant():
    # 'restoration' in the category string, but auto restoration is off-scope
    # -- denylist override wins even though it would otherwise positively match.
    assert is_category_relevant("Auto restoration service") is False


def test_denylist_wins_even_when_restoration_category_also_present():
    # Real listing category strings are semicolon-joined lists. A denylisted
    # component must force category_relevant=0 even if a legit restoration
    # category is also present in the same string.
    assert is_category_relevant(
        "Roofing contractor; Water damage restoration service"
    ) is False


def test_handyman_variant_is_not_relevant():
    # Real DB value is 'Handyman/Handywoman/Handyperson', not the bare
    # denylist string -- must still match. (No positive match either way.)
    assert is_category_relevant("Handyman/Handywoman/Handyperson") is False


def test_water_damage_restoration_is_relevant():
    assert is_category_relevant("Water damage restoration service") is True


def test_mold_remediation_service_is_relevant():
    assert is_category_relevant("Mold remediation service") is True


def test_none_category_defaults_not_relevant_without_fallback_signal():
    # No blind True default -- null google_category with no categories
    # fallback available means we can't confirm relevance.
    assert is_category_relevant(None) is False


def test_none_category_falls_back_to_categories_sweep_term():
    # google_category missing, but the row's sweep-term signal (categories
    # column) shows it was found under a genuinely mold-related search term.
    assert is_category_relevant(None, "mold remediation,mold testing") is True


def test_none_category_falls_back_to_categories_non_mold_term():
    assert is_category_relevant(None, "hvac contractor") is False


# Regression cases from the Stage A denylist-only bug: these categories have
# no positive mold/restoration/environmental/disaster match and must NOT be
# marked relevant just because they aren't denylisted.
def test_auto_repair_shop_is_not_relevant():
    assert is_category_relevant("Auto repair shop") is False


def test_hvac_contractor_is_not_relevant():
    assert is_category_relevant("HVAC contractor") is False


def test_carpet_cleaning_service_is_not_relevant():
    assert is_category_relevant("Carpet cleaning service") is False


def test_general_contractor_is_not_relevant():
    assert is_category_relevant("General contractor") is False


def test_home_inspector_is_not_relevant():
    # No positive match on its own.
    assert is_category_relevant("Home inspector") is False


def test_home_inspector_with_mold_sweep_term_is_relevant():
    # If the same row was swept in under a mold-specific search term, that's
    # a real signal even with a generic google_category -- but google_category
    # here has no positive match itself, so this stays False (positive match
    # is checked on google_category first; categories is fallback only for
    # null/empty google_category, per spec).
    assert is_category_relevant("Home inspector", "mold assessment") is False


def test_concrete_contractor_is_not_relevant():
    assert is_category_relevant("Concrete contractor") is False


def test_asphalt_contractor_is_not_relevant():
    assert is_category_relevant("Asphalt contractor") is False


def test_bathroom_remodeler_is_not_relevant():
    assert is_category_relevant("Bathroom remodeler") is False
