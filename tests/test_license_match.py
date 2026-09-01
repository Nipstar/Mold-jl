"""Tests for src/license_match.py -- spec item 9: when license_verified=1,
derive primary_service/owner_name/license_class/address fallback from the
matched DBPR license instead of (or in addition to) Google-category signals.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.license_match import (
    compute_license_class,
    get_license_address,
    primary_service_from_license_type,
)


def test_mrsr_maps_to_remediation():
    assert primary_service_from_license_type("MRSR") == "remediation"


def test_mrsa_maps_to_assessment_only():
    assert primary_service_from_license_type("MRSA") == "assessment_only"


def test_unknown_license_type_returns_none():
    assert primary_service_from_license_type("XYZ") is None
    assert primary_service_from_license_type(None) is None


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        """CREATE TABLE companies (
            id INTEGER PRIMARY KEY, license_number TEXT UNIQUE, license_type TEXT,
            licensee_name TEXT, principal_name TEXT, address TEXT, city TEXT,
            county TEXT, zip TEXT
        )"""
    )
    yield c
    c.close()


def test_compute_license_class_single_mrsr(conn):
    conn.execute(
        "INSERT INTO companies (license_number, license_type, principal_name) "
        "VALUES ('MRSR1', 'MRSR', 'SMITH, JOHN')"
    )
    conn.commit()
    assert compute_license_class(conn, "MRSR1") == "MRSR"


def test_compute_license_class_single_mrsa(conn):
    conn.execute(
        "INSERT INTO companies (license_number, license_type, principal_name) "
        "VALUES ('MRSA1', 'MRSA', 'SMITH, JOHN')"
    )
    conn.commit()
    assert compute_license_class(conn, "MRSA1") == "MRSA"


def test_compute_license_class_both_when_principal_has_both_types(conn):
    conn.execute(
        "INSERT INTO companies (license_number, license_type, principal_name) "
        "VALUES ('MRSR2', 'MRSR', 'SMITH, JOHN')"
    )
    conn.execute(
        "INSERT INTO companies (license_number, license_type, principal_name) "
        "VALUES ('MRSA2', 'MRSA', 'SMITH, JOHN')"
    )
    conn.commit()
    assert compute_license_class(conn, "MRSR2") == "both"


def test_compute_license_class_none_when_no_match(conn):
    assert compute_license_class(conn, None) == "none"
    assert compute_license_class(conn, "NOPE") == "none"


def test_get_license_address_returns_dbpr_address(conn):
    conn.execute(
        "INSERT INTO companies (license_number, address, city, county, zip) "
        "VALUES ('MRSA84', '3161 ELIZA ROAD UNIT 2', 'TALLAHASSEE', 'Leon', '32308')"
    )
    conn.commit()
    result = get_license_address(conn, "MRSA84")
    assert result == {
        "address": "3161 ELIZA ROAD UNIT 2",
        "city": "TALLAHASSEE",
        "county": "Leon",
        "zip": "32308",
    }


def test_get_license_address_none_when_no_match(conn):
    assert get_license_address(conn, None) is None
    assert get_license_address(conn, "NOPE") is None


def test_get_license_address_none_when_address_blank(conn):
    conn.execute(
        "INSERT INTO companies (license_number, address, city, county, zip) "
        "VALUES ('MRSR9', '', 'TALLAHASSEE', 'Leon', '32308')"
    )
    conn.commit()
    assert get_license_address(conn, "MRSR9") is None
