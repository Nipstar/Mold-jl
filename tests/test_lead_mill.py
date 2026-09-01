"""lead_mill_suspect scoring -- fixes the bug where lead-mill listings
(keyword-stuffed generic names, throwaway-TLD/builder-only websites,
shared address/phone under different names, review-farm-shaped rows)
scored lead_mill_score=0 despite obvious signals. See src/lead_mill.py."""
from __future__ import annotations

from src.lead_mill import compute_lead_mill_score


def _row(**kw):
    base = {
        "name": "Acme Mold Remediation",
        "website": "https://acmemold.com",
        "address": "123 Main St, Jacksonville, FL 32202",
        "phone": "9045551234",
        "review_count": 25,
        "rating": 4.6,
        "out_of_area": 0,
    }
    base.update(kw)
    return base


def test_keyword_stuffed_name_contributes_to_score():
    row = _row(name="Certified Water Damage Restoration Jacksonville")
    score, reasons = compute_lead_mill_score(row, [row])
    assert "keyword_stuffed_name" in reasons
    assert score > 0


def test_throwaway_tld_website_contributes_to_score():
    row = _row(website="https://jaxmoldpros.online")
    score, reasons = compute_lead_mill_score(row, [row])
    assert "throwaway_tld" in reasons
    assert score > 0


def test_thin_row_no_address_no_website_is_high_score_and_suspect():
    row = _row(
        website=None,
        address=None,
        review_count=2,
        rating=5.0,
    )
    score, reasons = compute_lead_mill_score(row, [row])
    assert "thin_review_farm_shape" in reasons
    assert score >= 40


def test_normal_independent_is_not_suspect():
    row = _row()
    score, reasons = compute_lead_mill_score(row, [row])
    assert score < 40
    assert reasons == []


def test_shared_address_between_two_differently_named_rows_flags_both():
    row_a = _row(name="Alpha Restoration", address="500 Bay St, Jacksonville, FL 32202")
    row_b = _row(name="Beta Water Damage", address="500 Bay St, Jacksonville, FL 32202")
    all_rows = [row_a, row_b]

    score_a, reasons_a = compute_lead_mill_score(row_a, all_rows)
    score_b, reasons_b = compute_lead_mill_score(row_b, all_rows)

    assert "shared_address" in reasons_a
    assert "shared_address" in reasons_b
