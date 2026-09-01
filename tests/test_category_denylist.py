"""Stage A: category_relevant must be forced to False for denylisted
google_category values, regardless of any other signal (name, other
categories present, etc). See config/category_denylist.yml."""
from __future__ import annotations

from src.relevance import is_category_relevant


def test_roofing_contractor_is_not_relevant():
    assert is_category_relevant("Roofing contractor") is False


def test_computer_repair_is_not_relevant():
    assert is_category_relevant("Computer repair service") is False


def test_auto_restoration_service_is_not_relevant():
    # 'restoration' in the category string, but auto restoration is off-scope.
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
    # denylist string -- must still match.
    assert is_category_relevant("Handyman/Handywoman/Handyperson") is False


def test_water_damage_restoration_is_relevant():
    assert is_category_relevant("Water damage restoration service") is True


def test_none_category_defaults_relevant():
    assert is_category_relevant(None) is True
