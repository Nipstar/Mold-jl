"""Tests for src/dedup.py -- duplicate detection/grouping extending the
existing dup_group_id/is_duplicate columns in maps_companies."""
from __future__ import annotations

from src.dedup import (
    normalize_name,
    is_likely_duplicate_pair,
    assign_duplicate_groups,
)


def row(**kw):
    base = {
        "id": None, "name": None, "city": None, "phone": None,
        "website": None, "address": None, "rating": None,
        "review_count": None,
    }
    base.update(kw)
    return base


def test_normalize_name_strips_punctuation_and_suffix():
    assert normalize_name("DRT Restoration, LLC") == "drt restoration"
    assert normalize_name("DRT Restoration") == "drt restoration"


def test_normalize_name_strips_own_city_suffix():
    r = row(name="ABC Restoration Jacksonville", city="Jacksonville")
    assert normalize_name(r["name"], city=r["city"]) == "abc restoration"


def test_normalize_name_keeps_city_when_not_own_city():
    # "Tallahassee Mold Pros" city is Sneads -- Tallahassee is a real part
    # of the brand name here, must not be stripped.
    r = row(name="Tallahassee Mold Pros", city="Sneads")
    assert normalize_name(r["name"], city=r["city"]) == "tallahassee mold pros"


def test_tallahassee_mold_specialists_pair_flagged():
    a = row(name="Tallahassee Mold Specialists", city="Tallahassee",
            phone="+1239-474-3462", address="1335 E 6th Ave, Tallahassee, FL 32303")
    b = row(name="Tallahassee Mold Specialists", city="Tallahassee",
            phone="+1239-474-3479", address="3817 N Monroe St, Tallahassee, FL 32303")
    assert is_likely_duplicate_pair(a, b) is True


def test_drt_restoration_llc_variant_flagged():
    a = row(name="DRT Restoration, LLC", city="Green Cove Springs", phone="+1904-894-6057")
    b = row(name="DRT Restoration", city="St. Augustine", phone="+1386-282-8857",
            address="8211 Forest Ct, St. Augustine, FL 32092")
    assert is_likely_duplicate_pair(a, b) is True


def test_joe_taylor_restoration_same_domain_flagged():
    a = row(name="Joe Taylor Restoration- Tallahassee", city="Havana",
            website="https://www.jtrestoration.com/")
    b = row(name="Joe Taylor Restoration", city="Baker",
            website="https://www.jtrestoration.com/")
    assert is_likely_duplicate_pair(a, b) is True


def test_florida_water_and_fire_pair_flagged():
    a = row(name="Florida Water and Fire", city="Holt", phone="+1904-206-7895")
    b = row(name="Florida Water and Fire", city="Baker", phone="+1850-270-8719")
    assert is_likely_duplicate_pair(a, b) is True


def test_shared_address_different_names_flagged():
    a = row(name="AAA Mold Co", city="Jacksonville",
            address="100 Main St, Jacksonville, FL 32202")
    b = row(name="BBB Restoration LLC", city="Jacksonville",
            address="100 Main St, Jacksonville, FL 32202")
    assert is_likely_duplicate_pair(a, b) is True


def test_franchise_locations_same_domain_not_flagged_by_domain_alone():
    # Two different SERVPRO franchise locations legitimately share the
    # servpro.com root domain but are separate independently-owned
    # businesses -- must NOT be flagged as duplicates on domain alone.
    a = row(name="SERVPRO of North Leon County", city="Tallahassee",
            phone="+1850-446-6920",
            website="https://www.servpro.com/locations/fl/servpro-of-north-leon-county",
            address="3841 Killearn Ct STE B, Tallahassee, FL 32309")
    b = row(name="SERVPRO of Ocala", city="Ocala",
            phone="+1352-745-3049",
            website="https://www.servpro.com/locations/fl/servpro-of-ocala",
            address="3407 SW 7th St, Ocala, FL 34474")
    assert is_likely_duplicate_pair(a, b) is False


def test_genuinely_different_businesses_not_flagged():
    a = row(name="Coastal Mold Solutions", city="Miami",
            phone="+1305-111-2222", website="https://coastalmold.com/",
            address="500 Ocean Dr, Miami, FL 33139")
    b = row(name="Peninsula Restoration Group", city="Orlando",
            phone="+1407-333-4444", website="https://peninsularestore.com/",
            address="900 Lake Ave, Orlando, FL 32801")
    assert is_likely_duplicate_pair(a, b) is False


def test_assign_duplicate_groups_clusters_and_picks_canonical():
    rows = [
        row(id=1, name="Tallahassee Mold Specialists", city="Tallahassee",
            phone="+1239-474-3462", address="1335 E 6th Ave, Tallahassee, FL 32303",
            rating=None, review_count=None),
        row(id=2, name="Tallahassee Mold Specialists", city="Tallahassee",
            phone="+1239-474-3479", website="https://tallahasseemold.com",
            address="3817 N Monroe St, Tallahassee, FL 32303",
            rating=4.5, review_count=20),
        row(id=3, name="Unrelated Biz", city="Ocala", phone="+1352-000-0000"),
    ]
    result = assign_duplicate_groups(rows)
    r1, r2, r3 = (result[i] for i in range(3))
    assert r1["dup_group_id"] == r2["dup_group_id"]
    assert r3["dup_group_id"] != r1["dup_group_id"]
    # canonical = more complete row (id=2 has website+rating+review_count)
    assert r2["is_duplicate"] == 0
    assert r1["is_duplicate"] == 1
    assert r3["is_duplicate"] == 0
