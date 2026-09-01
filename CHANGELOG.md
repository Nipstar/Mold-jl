# CHANGELOG — jl-mold-fl pipeline hardening (Stages A–I)

Following a manual audit of ~640 output rows that found systematic data
quality defects, the pipeline was hardened in 9 stages, each shipped as
tests-first, config-driven, deterministic code fixes against the real
785-row North Florida dataset (not CSV patches).

## Stage A — franchise_brand + category denylist
`franchise_flag` triggered on the word "Restoration" generically (false
positives on independents like Honest Restoration) and missed real chains
(RestoPros, Rytech, 1-800 Water Damage, OneRestore). Replaced with
`franchise_brand` (config/brands.yml, name+domain match) and
`multi_location_domain`. Added `config/category_denylist.yml` plus a
positive-match requirement so off-category businesses (auto repair, HVAC,
roofing, etc.) are excluded. Result: 422/785 rows correctly flagged
category-irrelevant, 96 rows matched to 24 real franchise brands.

## Stage B — out-of-area / SAB detection
Rows with no street address were getting the search-grid-point's
city/county silently assigned (e.g. "Water Mold Fire Restoration of
Miami" tagged to a north-FL grid city). Added `has_street_address`,
`location_source`, `out_of_area` (name/website/phone signals for
genuinely out-of-state listings), and `config/region.yml` for target
area codes. 269 rows have no street address, 97 flagged out_of_area, 17
fake city/county values corrected.

## Stage C — lead-mill scoring
`lead_mill_suspect` existed but was never computed, so keyword-stuffed
directory listings ("Water Damage Restoration Jacksonville") and
review-farm shapes (1-5 reviews, no address, no website) were scoring
highest on pain_score — exactly backwards. Added
`compute_lead_mill_score()` (0-100, config/lead_mill.yml threshold),
excluded lead_mill_suspect and out_of_area rows from scoring entirely.
22 rows flagged.

## Stage D — email quality
Placeholders (sample@gmail.com), developer/agency footer addresses
(confirmed real: eben@eyebytes.com on 3+ unrelated sites), and useless
franchise corporate domains (info@servpro.com guessed for every
location) were marked "found"/"guessed". Added denylist, cross-row
shared-email/domain rejection (≥3 rows), domain-agreement downgrade,
never-guess rules (no MX / franchise domain / builder domain /
lead-mill row), and MX-only `email_verified` (no live SMTP, standing
decision). 49 shared-address rejections, 44 no-guess rejections, 114
final usable emails.

## Stage E — owner-name validation
`owner_name_found` picked up nav fragments: confirmed "Tenant Landlord",
"Should Know", "Resources More" in the live data. Added
`is_valid_person_name()` (config/nav_stopwords.yml) and
`owner_name_source` (license|about_page|none). 2 nav fragments rejected;
1 row with a real license match had its garbage text replaced with the
trusted licensee name instead of just tagging the source.

## Stage F — dedup expansion
Missed obvious pairs (Tallahassee Mold Specialists, DRT Restoration,
Joe Taylor Restoration/jtrestoration.com, Florida Water and Fire).
Extended dedup to name/phone/domain/address matching with union-find
clustering. Caught a real bug during testing: domain-match alone flagged
two different real SERVPRO locations as duplicates — fixed by excluding
known franchise corporate domains from the domain signal. 71 rows
correctly flagged (down from a buggy 107 before the fix).

## Stage G — data_confidence + priority_rank
Added `data_confidence` (0-1, 5-signal average: address, website, usable
email, license match, review_count≥10) and `priority_rank = pain_score
× data_confidence`, so ranking reflects both pain and data reliability.
`pain_score` itself is unchanged, per standing instruction.

## Stage H — license-driven enrichment
DBPR license matches existed but weren't fully leveraged. Added
`license_class` (MRSR|MRSA|both|none), license-class-driven
`primary_service` override, and a DBPR address fallback for listings
with no street address. 48 license-verified rows (20 MRSR, 22 MRSA, 6
both), 24 primary_service corrections, 1 address fallback filled.

## Stage I — export contract + QA report
Added `include_in_outreach` (category_relevant AND NOT franchise_brand
AND NOT multi_location_domain AND NOT lead_mill_suspect AND NOT
out_of_area AND NOT is_duplicate) and the full 4-file export contract:

- `jl-mold-fl-full.csv` — all 785 rows, every column.
- `jl-mold-fl-outreach.csv` — 176 include_in_outreach rows, sorted
  priority_rank desc then pain_score desc, GoHighLevel-ready columns
  including contact_bucket and tags.
- `jl-mold-fl-excluded.csv` — 609 rows with exclusion_reasons.
- `jl-mold-fl-qa.md` — run summary, exclusion breakdown, email yield.

Acceptance tests pass: outreach row count matches include_in_outreach
count in full.csv, no row appears in both outreach and excluded,
re-running the export is deterministic, and the specific named
examples from the original audit (Honest Restoration → included,
SERVPRO → excluded with franchise_brand reason, etc.) resolve correctly
against the real data.

## Net effect

785 raw discovered companies → **176 genuinely outreach-ready
prospects** after removing category-irrelevant filler (422), franchise
locations (95), duplicates (71), out-of-area listings (97), lead mills
(22), and multi-location-domain networks (114) — note these overlap
(a row can hit multiple exclusion reasons).
