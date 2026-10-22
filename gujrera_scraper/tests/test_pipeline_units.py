"""End to end on recorded fixtures: raw_responses -> parse_all -> unit tables (no network)."""
import copy
import hashlib
import json
import sqlite3

import pytest

from database import repositories as repo
from database.db import RawStore, init_schema
from parser.pipeline import parse_all
from scraper.network_client import BASE_URL
from settings import settings as load_settings
from tests.conftest import load_raw, load_units, list_row


def _store(raw: RawStore, key, entity_type, entity_id, body, url):
    data = json.dumps(body).encode()
    raw.save_raw(url=url, status=200, content_type="application/json", body=data, sha256=hashlib.sha256(data).hexdigest(),
                 size=len(data), endpoint_key=key, entity_type=entity_type, entity_id=entity_id)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    init_schema(c)
    repo._columns_cache.clear()
    return c


def populate(conn, pid=12, drop_blocks=()):
    raw = RawStore(conn)
    row = list_row(pid)
    _store(raw, "list_mixed", "universe", "list_mixed", {"data": [row]}, BASE_URL + "list_mixed")
    for key, (j, meta) in load_raw(pid).items():
        _store(raw, key, "project", pid, j, f"{BASE_URL}{key}/{pid}")
    units_raw_project, unit_raw = load_units(pid)
    for key in ("qtr_form_ids", "block_names", "block_master", "form_three"):
        if key in units_raw_project:
            _store(raw, key, "project", pid, units_raw_project[key][0], f"{BASE_URL}{key}/{pid}")
    for eid, (j, meta) in unit_raw.items():
        if eid.split("|", 1)[1] in drop_blocks:
            continue
        ft = units_raw_project["qtr_form_ids"][0]["data"]["formThreeId"]
        block = eid.split("|", 1)[1]
        _store(raw, "unit_list", "project_block", eid, j,
               f"{BASE_URL}formthree/public/get-inv-details-for-view#blockName={block}&formThreeId={ft}")
    return raw


def settings_for_test():
    s = copy.deepcopy(load_settings())
    s["target_project_types"] = ["list_mixed"]
    s["units"] = {"districts": ["Ahmedabad"], "refetch_days": 30}
    return s


def test_parse_all_builds_unit_tables_and_enriches_blocks(conn):
    populate(conn)
    stats = parse_all(conn, settings_for_test(), project_ids=[12])
    assert stats["unit_state_complete"] == 1 and stats["units_inserted"] == 220
    p = conn.execute("SELECT unit_data_state, units_row_count, project_category FROM projects WHERE project_id=12").fetchone()
    assert tuple(p) == ("complete", 220, "mixed")
    assert conn.execute("SELECT COUNT(*) FROM project_unit_inventory WHERE project_id=12 AND is_current=1").fetchone()[0] == 220
    blocks = {r["block_name"]: r for r in conn.execute("SELECT * FROM project_blocks WHERE project_id=12")}
    assert set(blocks) == {"A", "B", "C", "D", "E"}
    a = blocks["A"]
    assert (a["booked_units"], a["unbooked_units"]) == (31, 6)        # portal block chart, untouched
    assert (a["unit_rows"], a["unit_booked"], a["unit_unbooked"]) == (37, 31, 6)
    assert a["block_id_src"] == 12275 and a["dev_end_date"] == "2019-12-31"      # block master
    fetch = {r["block_name"]: r["outcome"] for r in conn.execute("SELECT * FROM project_block_fetch WHERE project_id=12")}
    assert set(fetch.values()) == {"ok"}
    # type-level open-terrace area (registration inventory) is now parsed
    terrace = conn.execute("SELECT open_terrace_area_sqm FROM project_units WHERE project_id=12 AND "
                           "unit_type_raw='2 BHK'").fetchone()[0]
    assert terrace == 2.04


def test_reparse_is_idempotent_and_partial_never_looks_complete(conn):
    populate(conn)
    parse_all(conn, settings_for_test(), project_ids=[12])
    stats = parse_all(conn, settings_for_test(), project_ids=[12])
    assert stats.get("units_inserted", 0) == 0 and stats["units_updated"] == 220
    assert conn.execute("SELECT COUNT(*) FROM project_unit_inventory").fetchone()[0] == 220
    assert conn.execute("SELECT COUNT(*) FROM project_unit_history").fetchone()[0] == 0

    c2 = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    c2.row_factory = sqlite3.Row
    init_schema(c2)
    populate(c2, drop_blocks={"E"})
    stats = parse_all(c2, settings_for_test(), project_ids=[12])
    assert stats["unit_state_partial"] == 1
    state, note = tuple(c2.execute("SELECT unit_data_state, unit_data_note FROM projects WHERE project_id=12").fetchone())
    assert state == "partial" and "E" in note


def test_failed_fetch_in_queue_is_reported_as_error_not_unbooked(conn):
    populate(conn, drop_blocks={"A", "B", "C", "D", "E"})
    conn.execute("INSERT INTO crawl_runs(run_id, mode, started_at) VALUES ('r', 'units', 't')")
    for b in "ABCDE":
        conn.execute("INSERT INTO crawl_queue(run_id, entity_type, entity_id, endpoint_key, path, status) "
                     "VALUES ('r', 'project_block', ?, 'unit_list', 'p', 'failed')", (f"12|{b}",))
    parse_all(conn, settings_for_test(), project_ids=[12])
    assert conn.execute("SELECT unit_data_state FROM projects WHERE project_id=12").fetchone()[0] == "error"
    assert conn.execute("SELECT COUNT(*) FROM project_unit_inventory").fetchone()[0] == 0


def test_out_of_scope_district_gets_no_unit_state(conn):
    populate(conn)
    s = settings_for_test()
    s["units"]["districts"] = ["Surat"]
    parse_all(conn, s, project_ids=[12])
    assert conn.execute("SELECT unit_data_state FROM projects WHERE project_id=12").fetchone()[0] is None
    assert conn.execute("SELECT COUNT(*) FROM project_unit_inventory").fetchone()[0] == 0


def test_infrastructure_is_parsed_from_registration_details(conn):
    populate(conn)
    parse_all(conn, settings_for_test(), project_ids=[12])
    rows = {(r["category"], r["name"]): r["value"] for r in conn.execute("SELECT * FROM project_infrastructure")}
    assert rows[("external_development", "road_sysetm_dev_by")] == "Local Authority"
    assert rows[("external_development", "water_supply_by")] == "Self Development"


def test_amenities_are_parsed_for_unit_scope_projects_only(conn):
    populate(conn)
    stats = parse_all(conn, settings_for_test(), project_ids=[12])
    rows = {r["item_key"]: r for r in conn.execute("SELECT * FROM project_amenities WHERE project_id=12")}
    assert stats["amenity_rows"] == len(rows) >= 20 and "internalRoad" in rows
    assert rows["internalRoad"]["applicable"] in ("YES", "NO")
    parse_all(conn, settings_for_test(), project_ids=[12])            # re-parse replaces, never duplicates
    assert conn.execute("SELECT COUNT(*) FROM project_amenities WHERE project_id=12").fetchone()[0] == len(rows)

    out = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    out.row_factory = sqlite3.Row
    init_schema(out)
    populate(out)
    s = settings_for_test()
    s["units"]["districts"] = ["Surat"]
    parse_all(out, s, project_ids=[12])
    assert out.execute("SELECT COUNT(*) FROM project_amenities").fetchone()[0] == 0
