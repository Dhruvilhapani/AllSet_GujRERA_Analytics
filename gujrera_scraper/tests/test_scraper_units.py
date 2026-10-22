"""Crawl scope, POST client and the unit-list fan-out."""
import gzip
import hashlib
import json
import sqlite3

import httpx

from database.db import RawStore, init_schema
from parser.pipeline import load_universe
from parser.project_parser import category_of
from scraper.network_client import BASE_URL, GujReraClient
from scraper.orchestrator import Crawler
from scraper.project_listing import in_scope, in_unit_scope

SETTINGS = {
    "network": {"workers": 2, "max_task_attempts": 3},
    "target_project_types": ["list_residential", "list_commercial"],
    "district_scoped_types": ["list_commercial"],
    "target_districts": ["Ahmedabad", "Gandhinagar"],
    "priority": {"districts": ["Ahmedabad"], "endpoints": ["core"]},
    "units": {"districts": ["Ahmedabad", "Gandhinagar"], "refetch_days": 30},
}


def row(pid, district, ptype="Residential/Group Housing"):
    return {"projectRegId": pid, "districtName": district, "projectType": ptype, "regNo": f"PR/{pid}"}


def store(raw: RawStore, key, entity_type, entity_id, body, url=None):
    data = json.dumps(body).encode()
    raw.save_raw(url=url or f"{BASE_URL}{key}/{entity_id}", status=200, content_type="application/json", body=data,
                 sha256=hashlib.sha256(data).hexdigest(), size=len(data), endpoint_key=key, entity_type=entity_type,
                 entity_id=entity_id)


def make_db():
    conn = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    raw = RawStore(conn)
    store(raw, "list_residential", "universe", "list_residential",
          {"data": [row(12, "Ahmedabad"), row(13, "AHmedabad"), row(14, "Gandhinagar"), row(99, "Surat")]})
    store(raw, "list_commercial", "universe", "list_commercial",
          {"data": [row(21, "Ahmedabad", "Commercial"), row(22, "Surat", "Commercial")]})
    return conn, raw


def test_scope_filters_only_district_scoped_types():
    assert in_scope(row(1, "Surat"), "list_residential", SETTINGS)                  # legacy all-Gujarat type
    assert not in_scope(row(22, "Surat"), "list_commercial", SETTINGS)
    assert in_scope(row(21, "AHMEDABAD"), "list_commercial", SETTINGS)             # case-insensitive
    assert in_unit_scope(row(14, "gandhinagar"), SETTINGS) and not in_unit_scope(row(99, "Surat"), SETTINGS)
    _conn, raw = make_db()
    universe, _ = load_universe(raw, SETTINGS["target_project_types"], SETTINGS)
    assert sorted(universe) == [12, 13, 14, 21, 99]                                  # Surat commercial excluded


def test_category_of_new_types():
    assert category_of("Commercial") == "commercial" and category_of("Plotted Development") == "plotted"
    assert category_of("Mixed Development") == "mixed" and category_of("Residential/Group Housing") == "residential"


def test_unit_layer_is_only_queued_for_unit_districts():
    conn, _raw = make_db()
    crawler = Crawler(conn, None, SETTINGS)
    run = crawler.new_run("full")
    crawler.enqueue_projects(run, [12, 99])
    keys = {(r["entity_id"], r["endpoint_key"]) for r in conn.execute("SELECT entity_id, endpoint_key FROM crawl_queue")}
    assert ("12", "qtr_form_ids") in keys and ("12", "block_names") in keys and ("12", "block_master") in keys
    assert ("99", "core") in keys and ("99", "qtr_form_ids") not in keys                 # other districts: no unit layer
    assert ("12", "form_three") not in keys                                              # dependent: needs the Form 3 id


def test_form_three_is_queued_from_the_latest_form_id_not_from_core():
    conn, _raw = make_db()
    crawler = Crawler(conn, None, SETTINGS)
    run = crawler.new_run("full")
    crawler._enqueue_latest_forms(run, "12", {"data": {"formThreeId": 242392, "formOneId": 777}})
    crawler._enqueue_latest_forms(run, "13", {"data": {"formThreeId": None, "formOneId": None}})
    rows = [tuple(r) for r in conn.execute("SELECT entity_id, endpoint_key, path FROM crawl_queue")]
    assert sorted(rows) == [("12", "form_one", "formone/public/getfrom-one-byformone-id/777"),
                            ("12", "form_three", "formthree/public/getform-three-byid/242392")]


def test_unit_list_fan_out_is_per_block_post_and_incremental():
    conn, raw = make_db()
    store(raw, "qtr_form_ids", "project", "12", {"data": {"formThreeId": 500}})
    store(raw, "block_names", "project", "12", {"A": ["1"], "B": ["2"]})
    store(raw, "block_chart", "project", "12", {"data": [{"bname": "A+B", "booked": "1", "unbooked": "0"}]})
    store(raw, "qtr_form_ids", "project", "99", {"data": {"formThreeId": 600}})          # Surat: out of scope
    store(raw, "block_names", "project", "99", {"X": []})
    store(raw, "qtr_form_ids", "project", "14", {"data": {"formThreeId": None}})          # no Form 3 -> nothing to ask
    crawler = Crawler(conn, None, SETTINGS)
    run = crawler.new_run("units")
    assert crawler.enqueue_unit_lists(run) == 3
    tasks = [(r["entity_type"], r["entity_id"], r["path"], json.loads(r["body"]))
             for r in conn.execute("SELECT * FROM crawl_queue ORDER BY entity_id")]
    assert tasks == [("project_block", "12|A", "formthree/public/get-inv-details-for-view", {"blockName": "A", "formThreeId": 500}),
                     ("project_block", "12|A+B", "formthree/public/get-inv-details-for-view", {"blockName": "A+B", "formThreeId": 500}),
                     ("project_block", "12|B", "formthree/public/get-inv-details-for-view", {"blockName": "B", "formThreeId": 500})]
    assert crawler.enqueue_unit_lists(run) == 0                                           # same run: unique queue rows
    # a fresh response for exactly this Form 3 id is not fetched again in a new run ...
    store(raw, "unit_list", "project_block", "12|A", {"data": []}, url=Crawler.unit_list_url(500, "A"))
    run2 = crawler.new_run("units")
    assert crawler.enqueue_unit_lists(run2) == 2
    # ... but a new quarterly Form 3 (new id) is
    store(raw, "qtr_form_ids", "project", "12", {"data": {"formThreeId": 501}})
    run3 = crawler.new_run("units")
    assert crawler.enqueue_unit_lists(run3) == 3


def test_post_json_sends_body_and_keys_the_url_by_body():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"], seen["path"], seen["body"] = request.method, request.url.path, json.loads(request.content)
        return httpx.Response(200, json={"data": []})

    client = GujReraClient(rate_per_sec=0)
    client._client = httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(handler))
    r = client.post_json("/formthree/public/get-inv-details-for-view", {"formThreeId": 5, "blockName": "A+B"}, store=False)
    assert seen == {"method": "POST", "path": "/formthree/public/get-inv-details-for-view",
                    "body": {"formThreeId": 5, "blockName": "A+B"}}
    assert r.url == BASE_URL + "formthree/public/get-inv-details-for-view#blockName=A+B&formThreeId=5"
    assert r.url == Crawler.unit_list_url(5, "A+B")        # what enqueue_unit_lists checks for freshness
    assert gzip.decompress(gzip.compress(r.content)) == r.content


def test_missing_only_skips_urls_that_are_already_fetched_and_requeues_dependents_from_stored():
    conn, raw = make_db()
    store(raw, "core", "project", "12", {"data": {"formOneId": 5, "formTwoId": 6, "promoterId": 7}}, url=BASE_URL + "project_reg/public/alldatabyprojectid/12")
    store(raw, "details", "project", "12", {"data": {}}, url=BASE_URL + "project_reg/public/getproject-details/12")
    store(raw, "qtr_form_ids", "project", "12", {"data": {"formThreeId": 500, "formOneId": 9}}, url=BASE_URL + "quarter/public/get-qtr-form-details/12")
    crawler = Crawler(conn, None, SETTINGS)
    crawler.skip_fetched = True
    run = crawler.new_run("full")
    crawler.enqueue_projects(run, [12])
    queued = {r["endpoint_key"] for r in conn.execute("SELECT endpoint_key FROM crawl_queue")}
    assert "core" not in queued and "details" not in queued and "qtr_form_ids" not in queued   # already stored
    assert {"applications", "block_names", "block_master", "block_chart"} <= queued               # gaps are queued
    crawler.enqueue_from_stored(run, [12])
    queued = {r["endpoint_key"]: r["path"] for r in conn.execute("SELECT endpoint_key, path FROM crawl_queue")}
    assert queued["form_two"].endswith("/6") and queued["block_progress"].endswith("/5")           # from the stored core
    assert queued["form_three"].endswith("/500") and queued["form_one"].endswith("/9")             # from the stored qtr ids
    assert "promoter" in {r["endpoint_key"] for r in conn.execute("SELECT endpoint_key FROM crawl_queue")}
    # without the flag everything is queued again
    c2 = Crawler(conn, None, SETTINGS)
    run2 = c2.new_run("full")
    c2.enqueue_projects(run2, [12])
    assert conn.execute("SELECT COUNT(*) FROM crawl_queue WHERE run_id=? AND endpoint_key='core'", (run2,)).fetchone()[0] == 1
