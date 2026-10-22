"""Unit layer: parsing, completeness states, normalisation rules, upsert/history."""
import random
import sqlite3

import pytest

from database import repositories as repo
from database.db import init_schema
from parser import unit_parser
from parser.common import (classify_usage, normalize_unit_label, normalize_unit_status, parse_date, unit_label_kind)
from parser.pipeline import _note_unit_reconciliation
from scraper.units import block_candidates

NOW = "2026-10-21T00:00:00+00:00"
PII = {"alloteeName", "mobileNumber", "kycId", "kycid", "typeofKYC", "unitConsideration", "receivedAmount",
       "balanceAmount", "allottee_name", "mobile"}


def parse(units_for, pid, category="residential", failed=frozenset(), drop=()):
    raw, unit_raw = units_for(pid)
    unit_raw = {k: v for k, v in unit_raw.items() if k.split("|", 1)[1] not in drop}
    return unit_parser.parse_units(pid, category, raw, unit_raw, set(failed), NOW)


# ------------------------------------------------------------------------------------------ real fixtures
def test_multiblock_residential_12_is_complete_and_matches_block_chart(units_for):
    up = parse(units_for, 12, "mixed")
    assert up.state == "complete" and len(up.units) == 220
    assert {n: s["unit_rows"] for n, s in up.block_stats.items()} == {"A": 37, "B": 36, "C": 35, "D": 56, "E": 56}
    # unit rows agree with the portal block chart for this project (31/6, 33/3, 30/5, 45/11, 47/9)
    assert (up.block_stats["A"]["unit_booked"], up.block_stats["A"]["unit_unbooked"]) == (31, 6)
    assert sum(s["unit_booked"] for s in up.block_stats.values()) == 186
    # Form 3 header of this project returns HTTP 500 on the portal: units are still complete without it
    assert up.form3 is None and up.form3_submitted_on is None


def test_source_values_are_preserved_and_area_is_not_split(units_for):
    units = parse(units_for, 12, "mixed").units
    first = next(u for u in units if u["block_name"] == "A" and u["unit_no_raw"] == "A - 101 to 904")
    assert first["status_source"] == "BOOKED" and first["status_norm"] == "booked"
    assert first["carpet_area_sqm"] == 1390.74 and first["area_excl_balcony_terrace_sqm"] == 48.1
    keys = set(first)
    assert not any("balcony_area" in k or "terrace_area" in k for k in keys)   # one combined field only
    assert not PII & keys
    # promoter-typed ranges are kept verbatim and flagged, never expanded
    assert first["unit_no_raw"] == "A - 101 to 904" and first["unit_kind"] == "range"
    assert first["agreement_date"] is None and first["agreement_date_raw"] is None   # missing stays NULL


def test_agreement_dates_parse_to_date_and_keep_raw(units_for):
    units = parse(units_for, 3797).units
    dated = [u for u in units if u["agreement_date"]]
    assert len(dated) == 218 and all(u["status_norm"] == "booked" for u in dated)
    u = next(u for u in dated if u["unit_no_raw"] == "101" and u["block_name"] == "A")
    assert u["agreement_date"] == "2019-10-26" and u["agreement_date_raw"].startswith("2019-10-26T00:00:00")


def test_combined_block_names_and_reconciliation_note(units_for):
    up = parse(units_for, 16972)
    assert up.state == "complete" and set(up.block_stats) == {"A+B", "C", "D"} and len(up.units) == 200
    assert up.form3["number_of_units"] == 200 and up.form3["submitted_on"] == "2026-10-07"
    booked = sum(u["status_norm"] == "booked" for u in up.units)
    assert (booked, 200 - booked) == (164, 36)             # the portal block chart says 149 / 51
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    _note_unit_reconciliation(conn, 16972, {"booked_units": 149, "unbooked_units": 51}, up)
    notes = [r["note"] for r in conn.execute("SELECT note FROM data_quality_notes")]
    assert any("164/36" in n and "149/51" in n for n in notes)       # recorded, not silently "fixed"


def test_empty_block_name_source_falls_back_to_chart_and_master(units_for):
    up = parse(units_for, 19269)
    assert up.state == "complete" and set(up.block_stats) == {"A+B"} and len(up.units) == 78


def test_header_unbooked_bug_never_overrides_rows(units_for):
    up = parse(units_for, 3797)
    assert up.form3["booked_declared"] == 218 and up.form3["unbooked_declared"] == 218   # source bug, stored as-is
    assert sum(u["status_norm"] == "unbooked" for u in up.units) == 6                    # rows are authoritative


def test_commercial_and_plot_projects(units_for):
    com = parse(units_for, 5162, "commercial")
    assert com.state == "complete" and len(com.units) == 241 and {u["usage_class"] for u in com.units} == {"shop"}
    plot = parse(units_for, 12921, "plotted")
    assert len(plot.units) == 20 and {u["usage_class"] for u in plot.units} == {"plot"}
    assert {u["usage_raw"] for u in plot.units} == {"Other"}     # the source value is kept


# ------------------------------------------------------------------------------------------ completeness
def test_missing_block_makes_project_partial_never_complete(units_for):
    up = parse(units_for, 12, "mixed", drop={"E"})
    assert up.state == "partial" and "E" in up.note
    assert {r["block_name"]: r["outcome"] for r in up.block_fetch}["E"] == "not_fetched"
    assert up.project_fields()["units_row_count"] == 164


def test_failed_block_is_error_not_unbooked(units_for):
    up = parse(units_for, 5162, "commercial", failed={"1"}, drop={"1"})
    assert up.state == "error" and not up.units and up.project_fields()["units_row_count"] is None
    assert up.block_fetch[0]["outcome"] == "error"


def test_nothing_fetched_yet(units_for):
    assert parse(units_for, 5162, "commercial", drop={"1"}).state == "not_fetched"
    up = unit_parser.parse_units(1, "residential", {}, {}, set(), NOW)
    assert up.state == "not_fetched"


def test_empty_unit_lists_are_not_published(units_for):
    raw, unit_raw = units_for(5162)
    key = "5162|1"
    unit_raw[key] = ({"status": "200", "data": []}, unit_raw[key][1])
    up = unit_parser.parse_units(5162, "commercial", raw, unit_raw, set(), NOW)
    assert up.state == "not_published" and up.block_fetch[0]["outcome"] == "empty" and not up.units


def test_no_form3_published():
    raw = {"qtr_form_ids": ({"status": "200", "data": {"formOneId": 1, "formTwoId": None, "formThreeId": None}}, {})}
    up = unit_parser.parse_units(7, "residential", raw, {}, set(), NOW)
    assert up.state == "not_published"


def test_stale_response_for_an_older_form3_is_ignored(units_for):
    raw, unit_raw = units_for(5162)
    j, meta = unit_raw["5162|1"]
    unit_raw["5162|1"] = (j, dict(meta, url=meta["url"].replace("formThreeId=", "formThreeId=1")))
    assert unit_parser.parse_units(5162, "commercial", raw, unit_raw, set(), NOW).state == "not_fetched"


def test_empty_non_expected_name_is_ignored(units_for):
    raw, unit_raw = units_for(3797)
    raw["block_chart"] = ({"data": [{"bname": "A+B+C", "booked": "1", "unbooked": "0"}]}, raw["block_chart"][1])
    ft = raw["qtr_form_ids"][0]["data"]["formThreeId"]
    unit_raw["3797|A+B+C"] = ({"status": "200", "data": []},
                              {"id": 0, "url": f"fixture://units#blockName=A+B+C&formThreeId={ft}", "fetched_at": "x"})
    up = unit_parser.parse_units(3797, "residential", raw, unit_raw, set(), NOW)
    assert up.state == "complete" and len(up.units) == 224


# ------------------------------------------------------------------------------------------ normalisation
def test_status_mapping_does_not_assume_equivalence():
    assert normalize_unit_status("BOOKED") == "booked" and normalize_unit_status(" booked ") == "booked"
    assert normalize_unit_status("UNBOOKED") == "unbooked"
    for other in ("SOLD", "REGISTERED", "AVAILABLE", "Allotted"):
        assert normalize_unit_status(other) == "other"
    assert normalize_unit_status(None) == "unknown" and normalize_unit_status("  ") == "unknown"
    rows = unit_parser.build_unit_rows("A", [{"flatNo": "1", "status": "SOLD"}, {"flatNo": "2", "status": None}],
                                       1, None, "residential")
    assert [(r["status_source"], r["status_norm"]) for r in rows] == [("SOLD", "other"), (None, "unknown")]


def test_unit_label_helpers():
    assert unit_label_kind("101") == unit_label_kind("15/1") == unit_label_kind("A-1-01") == "single"
    assert unit_label_kind("101 to 904") == unit_label_kind("GF 1 to 14 and FF 1 to 14") == "range"
    assert unit_label_kind("101 - 904") == "range"
    assert unit_label_kind("101, 102") == "list"
    assert normalize_unit_label("  A  101 ") == "a 101" and normalize_unit_label(None) == "(blank)"
    assert classify_usage("Shop") == "shop" and classify_usage("Office") == "office"
    assert classify_usage("Residential") == "residential" and classify_usage("Other") == "other"
    assert classify_usage("Residential", "plotted") == "plot"
    assert parse_date("2019-10-26T00:00:00.000+0530") == "2019-10-26"


def test_dup_seq_is_deterministic_and_pii_never_copied():
    rows = [{"flatNo": "101 to 904", "usage": "Residential", "carpetArea": c, "areaofExBalcony": 0, "status": "BOOKED",
             "alloteeName": "Someone", "mobileNumber": "9999999999", "kycId": "X", "typeofKYC": "PAN",
             "unitConsideration": "******"} for c in (30.0, 20.0, 25.0)]
    a = unit_parser.build_unit_rows("A", rows, 1, None, "residential")
    random.Random(1).shuffle(rows)
    b = unit_parser.build_unit_rows("A", rows, 1, None, "residential")
    assert [(u["dup_seq"], u["carpet_area_sqm"]) for u in a] == [(0, 20.0), (1, 25.0), (2, 30.0)] == \
           [(u["dup_seq"], u["carpet_area_sqm"]) for u in b]
    assert not PII & set(a[0]) and "Someone" not in str(a)


def test_block_candidates_order_and_dedupe():
    names, expected, src = block_candidates({"block_names": {"A": [], "B": []},
                                             "block_chart": {"data": [{"bname": "A+B"}]},
                                             "block_master": {"data": [{"blockName": "A"}]}})
    assert names == ["A", "B", "A+B"] and expected == ["A", "B"] and src == "block_names"
    assert block_candidates({"block_names": {}, "block_chart": None, "block_master": None}) == ([], [], None)


# ------------------------------------------------------------------------------------------ upsert / history
@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    repo._columns_cache.clear()
    return conn


def _units(status="BOOKED", extra=()):
    rows = [{"flatNo": "101", "usage": "Residential", "carpetArea": 30.0, "areaofExBalcony": 2.0, "status": status,
             "dateOfAgrrement": None}, {"flatNo": "102", "usage": "Residential", "carpetArea": 31.0,
                                        "areaofExBalcony": 0, "status": "UNBOOKED"}] + list(extra)
    return unit_parser.build_unit_rows("A", rows, 1, None, "residential")


def test_upsert_is_idempotent_and_tracks_changes(db):
    s1 = repo.upsert_units(db, 5, _units(), {"A"}, "t1")
    assert s1["inserted"] == 2
    assert db.execute("SELECT COUNT(*) FROM project_unit_history").fetchone()[0] == 0   # initial load is not a change
    repo.upsert_units(db, 5, _units(), {"A"}, "t2")
    assert db.execute("SELECT COUNT(*) FROM project_unit_inventory").fetchone()[0] == 2
    assert db.execute("SELECT COUNT(*) FROM project_unit_history").fetchone()[0] == 0
    row = db.execute("SELECT * FROM project_unit_inventory WHERE unit_no_norm='101'").fetchone()
    assert row["first_seen_at"] == "t1" and row["last_changed_at"] == "t1" and row["last_seen_at"] == "t2"
    # status change -> exactly one history row, last_changed moves
    repo.upsert_units(db, 5, _units("UNBOOKED"), {"A"}, "t3")
    h = db.execute("SELECT change_type, status_old, status_new FROM project_unit_history").fetchall()
    assert [tuple(x) for x in h] == [("status_changed", "BOOKED", "UNBOOKED")]
    assert db.execute("SELECT last_changed_at FROM project_unit_inventory WHERE unit_no_norm='101'").fetchone()[0] == "t3"


def test_new_and_disappeared_units(db):
    repo.upsert_units(db, 5, _units(), {"A"}, "t1")
    extra = {"flatNo": "103", "usage": "Residential", "carpetArea": 32.0, "areaofExBalcony": 0, "status": "BOOKED"}
    repo.upsert_units(db, 5, _units(extra=[extra]), {"A"}, "t2")
    assert [r[0] for r in db.execute("SELECT change_type FROM project_unit_history")] == ["appeared"]
    # unit 103 vanishes from a block that was fetched successfully -> is_current = 0, never deleted
    repo.upsert_units(db, 5, _units(), {"A"}, "t3")
    r = db.execute("SELECT is_current FROM project_unit_inventory WHERE unit_no_norm='103'").fetchone()
    assert r[0] == 0 and db.execute("SELECT COUNT(*) FROM project_unit_inventory").fetchone()[0] == 3
    assert [r[0] for r in db.execute("SELECT change_type FROM project_unit_history ORDER BY id")] == \
           ["appeared", "disappeared"]


def test_failed_block_never_makes_units_disappear(db):
    repo.upsert_units(db, 5, _units(), {"A"}, "t1")
    repo.upsert_units(db, 5, [], set(), "t2")        # the block fetch failed this time
    assert db.execute("SELECT COUNT(*) FROM project_unit_inventory WHERE is_current=1").fetchone()[0] == 2
    assert db.execute("SELECT COUNT(*) FROM project_unit_history").fetchone()[0] == 0


def test_form3_snapshots_and_latest_flag(db):
    h1 = {"project_id": 5, "form_three_id": 1, "submitted_on": "2026-01-01", "is_latest": 1}
    h2 = dict(h1, form_three_id=2, submitted_on="2026-04-01")
    repo.upsert_form3(db, h1, "t1")
    repo.upsert_form3(db, h2, "t2")
    rows = {r["form_three_id"]: r["is_latest"] for r in db.execute("SELECT * FROM project_form3")}
    assert rows == {1: 0, 2: 1}


def test_additive_migration_keeps_old_tables_working():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE projects (project_id INTEGER PRIMARY KEY, project_name TEXT, reg_serial TEXT)")
    conn.execute("INSERT INTO projects(project_id, project_name) VALUES (12, 'Old project')")
    init_schema(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(projects)")}
    assert {"unit_data_state", "units_scraped_at"} <= cols
    assert tuple(conn.execute("SELECT project_name, unit_data_state FROM projects").fetchone()) == ("Old project", None)
    assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 1
