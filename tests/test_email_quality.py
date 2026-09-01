"""Email quality checks -- placeholder/denylist detection, domain
agreement, free-mail recognition, MX lookup. See src/email_quality.py."""
from __future__ import annotations

from unittest.mock import patch

from src.email_quality import (
    domain_agrees,
    free_mail_domains,
    has_mx_record,
    is_denylisted_email,
)


def test_placeholder_local_part_sample_gmail_denylisted():
    assert is_denylisted_email("sample@gmail.com") is True


def test_placeholder_local_part_example_gmail_denylisted():
    assert is_denylisted_email("example@gmail.com") is True


def test_sentry_wixpress_error_tracking_address_denylisted():
    assert is_denylisted_email("8eb368c655b84e029ed79ad7a5c1718e@sentry.wixpress.com") is True


def test_g_page_domain_denylisted():
    assert is_denylisted_email("info@g.page") is True


def test_real_business_email_not_denylisted():
    assert is_denylisted_email("owner@realbusiness.com") is False


def test_yourdomain_pattern_denylisted():
    assert is_denylisted_email("info@yourdomain-example.com") is True


def test_free_mail_domains_loaded_from_config():
    domains = free_mail_domains()
    assert "gmail.com" in domains
    assert "yahoo.com" in domains
    assert "pm.me" in domains
    assert "bellsouth.net" in domains


def test_domain_agrees_same_root_domain():
    assert domain_agrees("owner@realbusiness.com", "https://realbusiness.com") is True


def test_domain_agrees_free_mail_always_agrees():
    assert domain_agrees("owner@gmail.com", "https://realbusiness.com") is True


def test_domain_agrees_different_domain_disagrees():
    assert domain_agrees("random@otherdomain.com", "https://realbusiness.com") is False


def test_domain_agrees_www_subdomain_treated_as_root():
    assert domain_agrees("owner@realbusiness.com", "https://www.realbusiness.com/") is True


def test_has_mx_record_true_for_real_domain():
    with patch("src.email_quality._resolve_mx") as mock_resolve:
        mock_resolve.return_value = True
        assert has_mx_record("gmail.com") is True
        mock_resolve.assert_called_once()


def test_has_mx_record_false_for_nonexistent_domain():
    with patch("src.email_quality._resolve_mx") as mock_resolve:
        mock_resolve.return_value = False
        assert has_mx_record("this-domain-does-not-exist-xyzabc123.com") is False


def test_has_mx_record_caches_per_domain():
    with patch("src.email_quality._resolve_mx") as mock_resolve:
        mock_resolve.return_value = True
        has_mx_record("cached-example.com")
        has_mx_record("cached-example.com")
        assert mock_resolve.call_count == 1
