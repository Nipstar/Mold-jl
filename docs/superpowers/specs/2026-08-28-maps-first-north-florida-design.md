# Maps-first pivot: North Florida expansion

## Context

The original pipeline (Stages 1-5, see repo root README/CLAUDE.md) used the FL DBPR
mold-license extract as the source of truth: 4,304 licensed individuals/companies,
enriched by matching each one against a Google Maps listing via city/trade sweeps.

That approach undercounts real leads because DBPR licenses **individuals**, not
businesses (~1.65% of records have a DBA name). Match rates across the first 5
counties done this way (Duval, Orange, Hillsborough, Palm Beach, Dade) ranged
4-11% — most real mold/restoration companies visible on Google Maps were never
captured at all, because the pipeline only ever looked for a match to an existing
DBPR row.

Andy's direction: flip it. Capture every relevant business visible on Maps, and
use the DBPR register as a bonus verification/enrichment layer, not the gate.

## Scope: Phase 1 = North Florida

34 counties (panhandle + north-central): Escambia, Santa Rosa, Okaloosa, Walton,
Holmes, Washington, Bay, Jackson, Calhoun, Gulf, Gadsden, Liberty, Franklin, Leon,
Wakulla, Jefferson, Madison, Taylor, Hamilton, Suwannee, Lafayette, Dixie, Columbia,
Baker, Nassau, Duval, Union, Bradford, Clay, St. Johns, Putnam, Alachua, Gilchrist,
Levy.

- 90 distinct cities (from existing DBPR city list as a practical proxy for "every
  FL city with business activity" — not a from-scratch municipality list).
- 5 cities (Duval) already swept under the old model and reusable from cache.
- 85 new cities x 8 categories = 680 new sweeps, ~$2 estimated (DataForSEO,
  ~$0.003/sweep, confirmed rate from prior counties).
- Statewide rollout is the eventual goal; North Florida is the first outreach-ready
  batch, chosen so Andy can start using real data sooner rather than waiting for
  full-state coverage.

## Categories (unchanged)

mold remediation, mold assessment, mold inspection, mold testing, water damage
restoration, restoration company, fire and water damage restoration, disaster
restoration company.

## Architecture change

### New table: `maps_companies`

One row per unique Google Maps listing (`place_id`) surfaced by any sweep in scope.
This becomes the primary lead unit going forward, replacing `companies` (DBPR-first)
as the base for new regions.

Columns (draft): `id`, `place_id` UNIQUE, `name`, `address`, `city`, `county`,
`zip`, `phone`, `website`, `rating`, `review_count`, `categories` (raw sweep
category tags, comma-joined), `business_status`, `hours_json`, `franchise_flag`,
`license_verified` (bool), `matched_license_number`, `matched_principal_name`,
`match_confidence`, `source_sweeps` (which of the 8 category queries surfaced it),
`created_at`.

### Dedup

By `place_id` across overlapping category sweeps in the same city (a restoration
company can legitimately surface under both "water damage restoration" and "mold
remediation" — one row, both categories recorded).

### DBPR cross-reference (reversed from the old direction)

For each `maps_companies` row, attempt an address match against the existing
`companies` (DBPR) table for the same city/county. Reuse the existing fuzzy-match +
address-match logic, just swap which side initiates the lookup. On match:
`license_verified = true`, pull `principal_name`, `license_number`, `license_type`
across. No match: keep the row, `license_verified = false` — this is expected and
fine, not a Tier-3 flag like before.

Franchise exclusion list (Servpro, PuroClean, etc.) still applies, checked against
`name`.

### Stage 3/4 pivot

Website/email enrichment (Stage 3) and pain-scoring (Stage 4) now run against
`maps_companies` rows instead of DBPR `companies` rows. Scoring weights need a
small adjustment since `license_verified` is now a data point rather than assumed —
treat verified-licensed as a positive signal (regulatory-clean, easier sell) but
don't gate on it.

### What doesn't change

- Old DBPR-first data (5 counties, 4,304 companies, existing `companies` +
  `enrichment` tables) is kept as-is, not migrated or discarded. It's a separate,
  already-paid-for dataset that can still feed outreach for those 5 counties.
- Execution discipline stays the same: short per-city/county batches, foreground
  only, cost shown before any batch that isn't trivially small, CSV export +
  GitHub push after each batch.

## Out of scope for this phase

- Statewide rollout (future phase, same mechanism, just more counties).
- A from-scratch authoritative FL city/municipality list (using DBPR-derived city
  list as the practical proxy for now).
- Re-scoring or migrating the 5 already-done DBPR-first counties into the new
  model (they stay as their own dataset).
