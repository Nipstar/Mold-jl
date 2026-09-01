"""data_confidence (0.0-1.0) + priority_rank for `maps_companies` rows.

data_confidence weighting: simple count/5 of 5 equally-weighted binary
signals. Each present signal contributes 0.2. Equal weighting chosen as the
simplest defensible default -- no evidence one signal predicts data
reliability better than another, so no signal is weighted higher. Signals:

  - has_street_address (Stage B out-of-area/SAB detection column)
  - has website (non-null/non-empty)
  - has a usable email: email is not null AND email_source in
    ('found', 'found_offdomain') -- i.e. NOT rejected/guessed-unverified
  - license_verified = 1
  - review_count >= 10

priority_rank = pain_score * data_confidence. pain_score itself is
untouched (see src/stage4_score_maps.py) -- this is purely a derived
reliability-weighted ranking field.
"""
from __future__ import annotations

USABLE_EMAIL_SOURCES = {"found", "found_offdomain"}


def compute_data_confidence(row) -> float:
    signals = 0

    if row["has_street_address"]:
        signals += 1

    website = row["website"]
    if website:
        signals += 1

    email = row["email"]
    email_source = row["email_source"]
    if email and email_source in USABLE_EMAIL_SOURCES:
        signals += 1

    if row["license_verified"]:
        signals += 1

    review_count = row["review_count"]
    if review_count is not None and review_count >= 10:
        signals += 1

    return round(signals / 5, 4)


def compute_priority_rank(pain_score, data_confidence) -> float:
    return pain_score * data_confidence
