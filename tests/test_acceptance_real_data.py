"""Andy's literal acceptance criteria, run against a snapshot of the real
data/pipeline.db (785 maps_companies rows as of Stage H). Copies the DB so
this test suite never mutates the production pipeline.db."""
from __future__ import annotations

import csv
import shutil
import sqlite3
from pathlib import Path

import pytest

from src import db, finalize
from src.reports import export_final

ROOT = Path(__file__).resolve().parent.parent
REAL_DB = ROOT / "data" / "pipeline.db"

pytestmark = pytest.mark.skipif(not REAL_DB.exists(), reason="real pipeline.db not present")


@pytest.fixture(scope="module")
def real_export(tmp_path_factory):
    tmp_dir = tmp_path_factory.mktemp("acceptance")
    db_copy = tmp_dir / "pipeline.db"
    shutil.copy(REAL_DB, db_copy)
    conn = db.get_connection(db_copy)
    finalize.run(conn=conn)
    out_dir = tmp_dir / "out"
    export_all_summary = export_final.export_all(conn, out_dir, vertical_region="mold-fl")
    conn.row_factory = sqlite3.Row
    yield conn, out_dir, export_all_summary
    conn.close()


def _read_csv(path):
    with path.open() as f:
        return list(csv.DictReader(f))


class TestRowCounts:
    def test_outreach_count_equals_include_flag_count_in_full(self, real_export):
        conn, out_dir, summary = real_export
        full_rows = _read_csv(out_dir / "jl-mold-fl-full.csv")
        outreach_rows = _read_csv(out_dir / "jl-mold-fl-outreach.csv")
        n_included = sum(1 for r in full_rows if r["include_in_outreach"] == "1")
        assert len(outreach_rows) == n_included
        assert len(outreach_rows) > 0

    def test_no_overlap_outreach_excluded(self, real_export):
        conn, out_dir, summary = real_export
        outreach_ids = {r["place_id"] for r in _read_csv(out_dir / "jl-mold-fl-outreach.csv")}
        excluded_ids = {r["place_id"] for r in _read_csv(out_dir / "jl-mold-fl-excluded.csv")}
        assert outreach_ids.isdisjoint(excluded_ids)

    def test_full_plus_nothing_missing(self, real_export):
        conn, out_dir, summary = real_export
        full_rows = _read_csv(out_dir / "jl-mold-fl-full.csv")
        outreach_ids = {r["place_id"] for r in _read_csv(out_dir / "jl-mold-fl-outreach.csv")}
        excluded_ids = {r["place_id"] for r in _read_csv(out_dir / "jl-mold-fl-excluded.csv")}
        assert len(outreach_ids) + len(excluded_ids) == len(full_rows)


class TestDeterminism:
    def test_rerun_produces_byte_identical_csvs(self, real_export, tmp_path):
        conn, out_dir, summary = real_export
        out_dir2 = tmp_path / "rerun"
        out_dir2.mkdir()
        export_final.export_all(conn, out_dir2, vertical_region="mold-fl")
        for fname in ("jl-mold-fl-full.csv", "jl-mold-fl-outreach.csv",
                      "jl-mold-fl-excluded.csv", "jl-mold-fl-qa.md"):
            assert (out_dir / fname).read_bytes() == (out_dir2 / fname).read_bytes()


class TestSpotChecks:
    def test_known_independent_honest_restoration(self, real_export):
        conn, out_dir, summary = real_export
        full_rows = _read_csv(out_dir / "jl-mold-fl-full.csv")
        matches = [r for r in full_rows if "Honest Restoration" in r["name"]]
        assert matches, "expected 'Honest Restoration' in full CSV"
        row = matches[0]
        if row["category_relevant"] == "1" and row["franchise_brand"] == "" \
                and row["multi_location_domain"] == "0" and row["lead_mill_suspect"] == "0" \
                and row["out_of_area"] == "0" and row["is_duplicate"] == "0":
            assert row["include_in_outreach"] == "1"

    def test_known_franchise_servpro_excluded(self, real_export):
        conn, out_dir, summary = real_export
        excluded_rows = _read_csv(out_dir / "jl-mold-fl-excluded.csv")
        servpro = [r for r in excluded_rows if "SERVPRO" in r["name"]]
        assert servpro, "expected at least one SERVPRO row in excluded CSV"
        assert any("franchise_brand:SERVPRO" in r["exclusion_reasons"] for r in servpro)
