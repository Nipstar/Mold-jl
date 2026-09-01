"""data_confidence / priority_rank -- see src/data_confidence.py.

data_confidence = simple count/5 of 5 equally-weighted binary signals:
  has_street_address, has website, has usable email (email set AND
  email_source in ('found','found_offdomain')), license_verified=1,
  review_count >= 10. Each signal contributes 0.2. No signal is judged more
  predictive of reliability than another without evidence, so equal
  weighting is the simplest defensible default (documented per task
  instructions).
"""
from __future__ import annotations

from src.data_confidence import compute_data_confidence, compute_priority_rank


def _row(**overrides):
    base = {
        "has_street_address": 0,
        "website": None,
        "email": None,
        "email_source": None,
        "license_verified": 0,
        "review_count": 0,
    }
    base.update(overrides)
    return base


def test_all_five_signals_present_gives_full_confidence():
    row = _row(
        has_street_address=1,
        website="https://example.com",
        email="owner@example.com",
        email_source="found",
        license_verified=1,
        review_count=25,
    )
    assert compute_data_confidence(row) == 1.0


def test_no_signals_present_gives_zero_confidence():
    row = _row()
    assert compute_data_confidence(row) == 0.0


def test_two_of_five_signals_gives_point_four():
    row = _row(has_street_address=1, license_verified=1)
    assert compute_data_confidence(row) == 0.4


def test_offdomain_found_email_counts_as_usable():
    row = _row(email="owner@othersite.com", email_source="found_offdomain")
    assert compute_data_confidence(row) == 0.2


def test_rejected_email_source_not_usable():
    row = _row(email="owner@example.com", email_source="rejected")
    assert compute_data_confidence(row) == 0.0


def test_null_email_not_usable_even_with_found_source():
    row = _row(email=None, email_source="found")
    assert compute_data_confidence(row) == 0.0


def test_review_count_below_ten_not_counted():
    row = _row(review_count=9)
    assert compute_data_confidence(row) == 0.0


def test_review_count_at_ten_counted():
    row = _row(review_count=10)
    assert compute_data_confidence(row) == 0.2


def test_priority_rank_multiplies_pain_score_by_confidence():
    assert compute_priority_rank(50, 0.5) == 25.0
