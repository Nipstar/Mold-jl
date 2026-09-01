"""Tests for src/owner_name.py -- nav/menu fragment rejection for
owner_name_found values, and owner_name_source classification."""
from __future__ import annotations

from src.owner_name import classify_owner_name_source, is_valid_person_name


class TestIsValidPersonName:
    def test_rejects_tenant_landlord(self):
        assert is_valid_person_name("Tenant Landlord") is False

    def test_rejects_should_know(self):
        assert is_valid_person_name("Should Know") is False

    def test_rejects_resources_more(self):
        assert is_valid_person_name("Resources More") is False

    def test_accepts_john_smith(self):
        assert is_valid_person_name("John Smith") is True

    def test_accepts_apostrophe_name(self):
        assert is_valid_person_name("Mary O'Brien") is True

    def test_rejects_all_caps(self):
        assert is_valid_person_name("JOHN SMITH") is False

    def test_rejects_contact_us_today(self):
        assert is_valid_person_name("Contact Us Today") is False

    def test_accepts_hyphenated_surname(self):
        assert is_valid_person_name("Anna Smith-Jones") is True

    def test_accepts_name_with_middle(self):
        assert is_valid_person_name("John Michael Smith") is True

    def test_rejects_single_token(self):
        assert is_valid_person_name("Cher") is False

    def test_rejects_digits(self):
        assert is_valid_person_name("John Smith3") is False

    def test_rejects_empty(self):
        assert is_valid_person_name("") is False

    def test_rejects_none(self):
        assert is_valid_person_name(None) is False

    def test_rejects_generic_nav_pattern_not_in_explicit_list(self):
        # "Learn More" is explicit, but a similar-shaped unlisted nav phrase
        # made of common nav words should also be rejected by the general
        # title-case nav-word rule.
        assert is_valid_person_name("Book Now") is False

    def test_rejects_too_many_tokens(self):
        assert is_valid_person_name("John Michael Robert Smith Jones") is False


class TestClassifyOwnerNameSource:
    def test_license_when_has_license_match(self):
        assert classify_owner_name_source(True, "footer") == "license"

    def test_about_page_when_section_is_about(self):
        assert classify_owner_name_source(False, "about") == "about_page"

    def test_about_page_when_section_is_team(self):
        assert classify_owner_name_source(False, "team") == "about_page"

    def test_about_page_when_section_is_contact(self):
        assert classify_owner_name_source(False, "contact") == "about_page"

    def test_none_when_section_is_nav(self):
        assert classify_owner_name_source(False, "nav") == "none"

    def test_none_when_section_is_header(self):
        assert classify_owner_name_source(False, "header") == "none"

    def test_none_when_section_is_footer(self):
        assert classify_owner_name_source(False, "footer") == "none"

    def test_none_when_section_is_none(self):
        assert classify_owner_name_source(False, None) == "none"

    def test_license_takes_priority_over_section(self):
        assert classify_owner_name_source(True, None) == "license"
