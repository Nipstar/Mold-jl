"""Location/address heuristics -- fixes the bug where rows with no street
address got the search-grid-point's city/county assigned, producing nonsense
like 'Water Mold Fire Restoration of Miami' tagged McDavid/Escambia. See
docs/superpowers/specs (Stage B: location source + out-of-area detection)."""
from __future__ import annotations

from src.location import (
    classify_location_source,
    has_street_address,
    is_out_of_area,
)


def test_has_street_address_true_for_real_street():
    assert has_street_address("1514 Roberts Dr Ste 2") is True


def test_has_street_address_false_for_none():
    assert has_street_address(None) is False


def test_has_street_address_false_for_empty():
    assert has_street_address("") is False


def test_has_street_address_false_for_city_state_zip_only():
    assert has_street_address("Miami, FL 33101") is False


def test_classify_location_source_gbp_address():
    assert classify_location_source("1514 Roberts Dr Ste 2, Miami, FL 33101", True) == "gbp_address"


def test_classify_location_source_grid_centroid():
    assert classify_location_source("Miami, FL 33101", False) == "grid_centroid"


def test_classify_location_source_unknown():
    assert classify_location_source(None, False) == "unknown"


def test_mahoning_valley_is_out_of_area():
    assert is_out_of_area(
        "911 Restoration of Mahoning Valley", None, None, None
    ) is True


def test_5_star_restoration_of_houston_is_out_of_area():
    assert is_out_of_area(
        "5 Star Restoration of Houston", None, None, None
    ) is True


def test_water_mold_fire_restoration_of_miami_is_not_out_of_area():
    # Miami IS in Florida -- just outside the north/central FL sweep region.
    # That's an out-of-target-region concern, not out-of-STATE.
    assert is_out_of_area(
        "Water Mold Fire Restoration of Miami", None, None, None
    ) is False


def test_baldwin_county_alabama_is_out_of_area():
    assert is_out_of_area(
        "Best Option Restoration of Baldwin County", None, "251-555-0100", None
    ) is True


def test_url_slug_al_suffix_is_out_of_area_despite_fl_city_name():
    # Santa Rosa Beach is a real FL city, but the URL slug says '-al'.
    assert is_out_of_area(
        "PuroClean of Santa Rosa Beach",
        "https://www.puroclean.com/locations/santa-rosa-beach-al/",
        None,
        None,
    ) is True


def test_in_area_phone_with_no_street_address_is_not_flagged_by_phone_alone():
    # Out-of-list area code + a real in-area street address should NOT be
    # flagged -- people keep old phone numbers. Only flag on area code when
    # has_street_address is False.
    assert is_out_of_area(
        "Acme Mold Co",
        None,
        "212-555-0100",
        "1514 Roberts Dr Ste 2, Pensacola, FL 32503",
    ) is False


def test_out_of_list_area_code_with_no_address_is_out_of_area():
    assert is_out_of_area(
        "Acme Mold Co",
        None,
        "212-555-0100",
        None,
    ) is True


def test_in_target_area_code_is_not_out_of_area():
    assert is_out_of_area(
        "Acme Mold Co",
        None,
        "850-555-0100",
        None,
    ) is False
