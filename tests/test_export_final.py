"""Tests for src/reports/export_final.py -- the 4-file export contract
(Stage I: full / outreach / excluded / qa.md)."""
from __future__ import annotations

import csv
import sqlite3

import pytest

from src import db, finalize
from src.reports import export_final


def _make_row(conn, **fields):
    defaults = dict(
        place_id=None, name="Co", address="1 Main St", city="Tallahassee",
        county="Leon", zip="32301", phone="8505551212", website=None,
        rating=None, review_count=None, categories=None, business_status=None,
        franchise_flag=0, license_verified=0, matched_license_number=None,
        matched_principal_name=None, match_confidence=None,
        dup_group_id=None, is_duplicate=0, lead_mill_suspect=0,
        owner_name_found=None, owner_confirmed=0, email=None, email_source=None,
        email_verified=None, pain_score=None, segment=None, priority_rank=None,
        data_confidence=None, category_relevant=1, google_category=None,
        primary_service=None, franchise_brand=None, multi_location_domain=0,
        out_of_area=0, lead_mill_score=None, lead_mill_reasons=None,
        license_class=None, emergency_247=0, form_only_contact=0,
        include_in_outreach=None,
    )
    defaults.update(fields)
    cols = list(defaults.keys())
    marks = ", ".join(["?"] * len(cols))
    cur = conn.execute(
        f"INSERT INTO maps_companies ({', '.join(cols)}) VALUES ({marks})",
        [defaults[c] for c in cols],
    )
    conn.commit()
    return cur.lastrowid


@pytest.fixture()
def conn(tmp_path):
    c = db.get_connection(tmp_path / "pipeline.db")
    db.run_maps_companies_migration(c)
    db.run_dedup_migration(c)
    db.run_maps_enrich_migrations(c)
    db.run_category_relevance_migration(c)
    db.run_primary_service_migration(c)
    db.run_license_class_migration(c)
    db.run_franchise_brand_migration(c)
    db.run_location_source_migration(c)
    db.run_lead_mill_migration(c)
    db.run_email_quality_migration(c)
    db.run_owner_name_source_migration(c)
    db.run_data_confidence_migration(c)
    db.run_include_in_outreach_migration(c)
    yield c
    c.close()


class TestContactBucket:
    def test_verified_email_found(self):
        row = {"email": "a@b.com", "email_verified": "unknown", "email_source": "found"}
        assert export_final.compute_contact_bucket(row) == "verified_email"

    def test_verified_email_offdomain_valid(self):
        row = {"email": "a@b.com", "email_verified": "valid", "email_source": "found_offdomain"}
        assert export_final.compute_contact_bucket(row) == "verified_email"

    def test_guessed_email(self):
        row = {"email": "a@b.com", "email_verified": "unknown", "email_source": "guessed"}
        assert export_final.compute_contact_bucket(row) == "guessed_email"

    def test_invalid_recheck(self):
        row = {"email": "a@b.com", "email_verified": "invalid", "email_source": "found"}
        assert export_final.compute_contact_bucket(row) == "invalid_recheck"

    def test_form_only_has_website(self):
        row = {"email": None, "email_verified": None, "email_source": None, "website": "http://x.com"}
        assert export_final.compute_contact_bucket(row) == "form_only_has_website"

    def test_no_website_research(self):
        row = {"email": None, "email_verified": None, "email_source": None, "website": None}
        assert export_final.compute_contact_bucket(row) == "no_website_research"


class TestSplitOwnerName:
    def test_first_last_format(self):
        assert export_final.split_owner_name("John Smith") == ("John", "Smith")

    def test_last_comma_first_format(self):
        assert export_final.split_owner_name("SMITH, JOHN ROBERT") == ("John Robert", "Smith")

    def test_none_is_blank(self):
        assert export_final.split_owner_name(None) == ("", "")

    def test_single_token(self):
        assert export_final.split_owner_name("Cher") == ("Cher", "")


class TestTags:
    def test_basic_segment_tag(self):
        row = {"segment": "tier1", "license_verified": 0, "emergency_247": 0, "form_only_contact": 0}
        assert export_final.compute_tags(row) == "mold-fl,tier1"

    def test_all_flags(self):
        row = {"segment": "tier2", "license_verified": 1, "emergency_247": 1, "form_only_contact": 1}
        assert export_final.compute_tags(row) == "mold-fl,tier2,license-verified,emergency-247,form-only"

    def test_omits_unset_flags(self):
        row = {"segment": "tier3", "license_verified": 0, "emergency_247": 1, "form_only_contact": 0}
        assert export_final.compute_tags(row) == "mold-fl,tier3,emergency-247"


class TestExportContract:
    def test_outreach_row_count_matches_include_flag(self, conn, tmp_path):
        _make_row(conn, place_id="p1", name="Independent Co", category_relevant=1,
                  email="a@indep.com", email_source="found", email_verified="unknown",
                  pain_score=80, priority_rank=70)
        _make_row(conn, place_id="p2", name="SERVPRO of Leon", category_relevant=1,
                  franchise_brand="SERVPRO", pain_score=50, priority_rank=40)
        finalize.run(conn=conn)

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        export_final.export_all(conn, out_dir, vertical_region="mold-fl")

        with (out_dir / "jl-mold-fl-full.csv").open() as f:
            full_rows = list(csv.DictReader(f))
        with (out_dir / "jl-mold-fl-outreach.csv").open() as f:
            outreach_rows = list(csv.DictReader(f))

        assert len(full_rows) == 2
        n_included = sum(1 for r in full_rows if r["include_in_outreach"] in ("1", "true", "True"))
        assert len(outreach_rows) == n_included == 1
        assert outreach_rows[0]["company_name"] == "Independent Co"

    def test_no_overlap_between_outreach_and_excluded(self, conn, tmp_path):
        _make_row(conn, place_id="p1", name="Independent Co", category_relevant=1,
                  pain_score=10, priority_rank=5)
        _make_row(conn, place_id="p2", name="Franchise Co", category_relevant=1,
                  franchise_brand="SERVPRO", pain_score=10, priority_rank=5)
        finalize.run(conn=conn)

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        export_final.export_all(conn, out_dir, vertical_region="mold-fl")

        with (out_dir / "jl-mold-fl-outreach.csv").open() as f:
            outreach_ids = {r["place_id"] for r in csv.DictReader(f)}
        with (out_dir / "jl-mold-fl-excluded.csv").open() as f:
            excluded_ids = {r["place_id"] for r in csv.DictReader(f)}

        assert outreach_ids.isdisjoint(excluded_ids)
        assert outreach_ids == {"p1"}
        assert excluded_ids == {"p2"}

    def test_outreach_sorted_priority_then_pain(self, conn, tmp_path):
        _make_row(conn, place_id="p1", name="Low", category_relevant=1,
                  pain_score=10, priority_rank=5)
        _make_row(conn, place_id="p2", name="High", category_relevant=1,
                  pain_score=90, priority_rank=90)
        _make_row(conn, place_id="p3", name="Mid", category_relevant=1,
                  pain_score=50, priority_rank=50)
        finalize.run(conn=conn)

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        export_final.export_all(conn, out_dir, vertical_region="mold-fl")

        with (out_dir / "jl-mold-fl-outreach.csv").open() as f:
            names = [r["company_name"] for r in csv.DictReader(f)]
        assert names == ["High", "Mid", "Low"]

    def test_outreach_exact_column_order(self, conn, tmp_path):
        _make_row(conn, place_id="p1", name="Co", category_relevant=1, pain_score=1, priority_rank=1)
        finalize.run(conn=conn)
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        export_final.export_all(conn, out_dir, vertical_region="mold-fl")
        with (out_dir / "jl-mold-fl-outreach.csv").open() as f:
            header = next(csv.reader(f))
        assert header == [
            "company_name", "first_name", "last_name", "email", "email_verified",
            "email_source", "phone", "website", "address", "city", "state", "county",
            "zip", "contact_bucket", "segment", "pain_score", "priority_rank",
            "data_confidence", "license_class", "matched_principal_name",
            "primary_service", "rating", "review_count", "tags", "place_id",
        ]

    def test_excluded_reasons_franchise(self, conn, tmp_path):
        _make_row(conn, place_id="p1", name="SERVPRO of Leon", category_relevant=1,
                  franchise_brand="SERVPRO")
        finalize.run(conn=conn)
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        export_final.export_all(conn, out_dir, vertical_region="mold-fl")
        with (out_dir / "jl-mold-fl-excluded.csv").open() as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["exclusion_reasons"] == "franchise_brand:SERVPRO"

    def test_excluded_reasons_multiple(self, conn, tmp_path):
        _make_row(conn, place_id="p1", name="Bad Co", category_relevant=0, out_of_area=1)
        finalize.run(conn=conn)
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        export_final.export_all(conn, out_dir, vertical_region="mold-fl")
        with (out_dir / "jl-mold-fl-excluded.csv").open() as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["exclusion_reasons"] == "off_category,out_of_area"

    def test_excluded_duplicate_of_references_canonical_place_id(self, conn, tmp_path):
        _make_row(conn, place_id="canon", name="Canon Co", category_relevant=1,
                  dup_group_id=1, is_duplicate=0)
        _make_row(conn, place_id="dupe", name="Dupe Co", category_relevant=1,
                  dup_group_id=1, is_duplicate=1)
        finalize.run(conn=conn)
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        export_final.export_all(conn, out_dir, vertical_region="mold-fl")
        with (out_dir / "jl-mold-fl-excluded.csv").open() as f:
            rows = {r["place_id"]: r for r in csv.DictReader(f)}
        assert rows["dupe"]["exclusion_reasons"] == "duplicate_of:canon"

    def test_full_csv_has_every_row_and_all_columns(self, conn, tmp_path):
        _make_row(conn, place_id="p1", name="A", category_relevant=1)
        _make_row(conn, place_id="p2", name="B", category_relevant=0)
        finalize.run(conn=conn)
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        export_final.export_all(conn, out_dir, vertical_region="mold-fl")
        with (out_dir / "jl-mold-fl-full.csv").open() as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames
            rows = list(reader)
        assert len(rows) == 2
        assert "include_in_outreach" in header
        assert "franchise_brand" in header

    def test_qa_md_written(self, conn, tmp_path):
        _make_row(conn, place_id="p1", name="A", category_relevant=1, pain_score=1, priority_rank=1)
        finalize.run(conn=conn)
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        export_final.export_all(conn, out_dir, vertical_region="mold-fl")
        content = (out_dir / "jl-mold-fl-qa.md").read_text()
        assert "total rows" in content.lower() or "Total rows" in content

    def test_determinism_byte_identical_on_rerun(self, conn, tmp_path):
        _make_row(conn, place_id="p1", name="A", category_relevant=1, pain_score=10, priority_rank=10)
        _make_row(conn, place_id="p2", name="B", category_relevant=1,
                  franchise_brand="SERVPRO", pain_score=5, priority_rank=5)
        finalize.run(conn=conn)

        out1 = tmp_path / "out1"
        out1.mkdir()
        export_final.export_all(conn, out1, vertical_region="mold-fl")
        out2 = tmp_path / "out2"
        out2.mkdir()
        export_final.export_all(conn, out2, vertical_region="mold-fl")

        for fname in ("jl-mold-fl-full.csv", "jl-mold-fl-outreach.csv",
                      "jl-mold-fl-excluded.csv", "jl-mold-fl-qa.md"):
            assert (out1 / fname).read_bytes() == (out2 / fname).read_bytes()
