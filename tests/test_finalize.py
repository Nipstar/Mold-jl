"""Tests for src/finalize.py -- include_in_outreach computation (Stage I)."""
from __future__ import annotations

import sqlite3

import pytest

from src import db, finalize


def _base_row(**overrides):
    row = {
        "category_relevant": 1,
        "franchise_brand": None,
        "multi_location_domain": 0,
        "lead_mill_suspect": 0,
        "out_of_area": 0,
        "is_duplicate": 0,
    }
    row.update(overrides)
    return row


class TestComputeIncludeInOutreach:
    def test_true_when_all_clear(self):
        assert finalize.compute_include_in_outreach(_base_row()) is True

    def test_false_when_category_not_relevant(self):
        assert finalize.compute_include_in_outreach(_base_row(category_relevant=0)) is False

    def test_false_when_franchise_brand_set(self):
        assert finalize.compute_include_in_outreach(_base_row(franchise_brand="SERVPRO")) is False

    def test_false_when_multi_location_domain(self):
        assert finalize.compute_include_in_outreach(_base_row(multi_location_domain=1)) is False

    def test_false_when_lead_mill_suspect(self):
        assert finalize.compute_include_in_outreach(_base_row(lead_mill_suspect=1)) is False

    def test_false_when_out_of_area(self):
        assert finalize.compute_include_in_outreach(_base_row(out_of_area=1)) is False

    def test_false_when_duplicate(self):
        assert finalize.compute_include_in_outreach(_base_row(is_duplicate=1)) is False

    def test_false_when_multiple_exclusions(self):
        assert finalize.compute_include_in_outreach(
            _base_row(is_duplicate=1, out_of_area=1)
        ) is False


class TestRun:
    def test_sets_include_in_outreach_for_every_row_including_unscored(self, tmp_path):
        db_path = tmp_path / "pipeline.db"
        conn = db.get_connection(db_path)
        db.run_maps_companies_migration(conn)
        db.run_dedup_migration(conn)
        db.run_maps_enrich_migrations(conn)
        db.run_category_relevance_migration(conn)
        db.run_franchise_brand_migration(conn)
        db.run_location_source_migration(conn)
        db.run_include_in_outreach_migration(conn)

        # Row that never got scored (no stage3_processed_at) -- still needs
        # include_in_outreach set (to false) for the excluded.csv export.
        conn.execute(
            "INSERT INTO maps_companies (place_id, name, category_relevant, out_of_area) "
            "VALUES ('p1', 'Unscored Co', 0, 0)"
        )
        conn.execute(
            "INSERT INTO maps_companies (place_id, name, category_relevant) "
            "VALUES ('p2', 'Clean Co', 1)"
        )
        conn.commit()

        summary = finalize.run(conn=conn)

        rows = {r["place_id"]: r for r in conn.execute("SELECT * FROM maps_companies")}
        assert rows["p1"]["include_in_outreach"] == 0
        assert rows["p2"]["include_in_outreach"] == 1
        assert summary["total"] == 2
        assert summary["included"] == 1
        conn.close()
