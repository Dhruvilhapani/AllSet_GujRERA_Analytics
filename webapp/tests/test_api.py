"""API tests over a snapshot built with the pipeline's own schema.

Fixture rows are synthetic test values (not business data) chosen to pin the
rules that matter: unknown values stay null (never 0), search is partial and
case-insensitive, unknown values sort last, filters only match real values, a
missing snapshot is a 503, the dashboard only counts analysed projects, yearly
progress uses each project's last report of the year and drops future-labelled
filings, and extensions are reported separately from delays.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gujrera_scraper"))
from analytics.app_export import SCHEMA  # noqa: E402 -- pipeline package lives beside the webapp

from webapp.server import cache  # noqa: E402
from webapp.server.app import app  # noqa: E402

DEVELOPERS = [
    {
        "developer_id": 1,
        "name": "Alpha Estate LLP",
        "name_search": "alpha estate llp",
        "projects_total": 3,
        "projects_analyzed": 3,
        "completed": 1,
        "ongoing": 0,
        "delayed": 1,
        "pending": 0,
        "sales_pct": 80.0,
        "avg_progress_pct": 70.0,
        "complaints_total": 2,
        "complaints_pending": 1,
        "suo_motu": 0,
        "challans": 0,
        "extended_projects": 1,
        "total_extensions": 1,
        "completion_rate_pct": 50.0,
        "localities": json.dumps([["Northpark", 2]]),
        "aliases": json.dumps([]),
    },
    {
        "developer_id": 2,
        "name": "Beta Builders",
        "name_search": "beta builders",
        "projects_total": 1,
        "projects_analyzed": 1,
        "completed": 0,
        "ongoing": 1,
        "delayed": 0,
        "pending": 0,
        "sales_pct": 20.0,
        "complaints_total": 0,
        "total_extensions": 0,
    },
    {
        "developer_id": 3,
        "name": "Gamma Homes",
        "name_search": "gamma homes",
        "projects_total": 1,
        "projects_analyzed": 0,
    },
]

PROJECTS = [
    {
        "project_id": 10,
        "developer_id": 1,
        "name": "Alpha One",
        "category": "residential",
        "locality": "Northpark",
        "status": "Completed",
        "total_units": 100,
        "sold_units": 100,
        "unsold_units": 0,
        "sales_pct": 100.0,
        "progress_pct": 100.0,
        "registration_date": "2018-01-01",
        "detail_crawled": 1,
        "complaints_total": 1,
    },
    {
        "project_id": 11,
        "developer_id": 1,
        "name": "Alpha Two",
        "category": "mixed",
        "locality": "Northpark",
        "status": "Delayed",
        "total_units": 200,
        "sold_units": 140,
        "unsold_units": 60,
        "sales_pct": 70.0,
        "progress_pct": 40.0,
        "original_completion_date": "2021-06-30",
        "current_completion_date": "2022-06-30",
        "extension_count": 1,
        "days_beyond_current": 400,
        "registration_date": "2020-01-01",
        "detail_crawled": 1,
        "complaints_total": 1,
    },
    {
        "project_id": 12,
        "developer_id": 2,
        "name": "Beta Park",
        "category": "residential",
        "locality": "Southside",
        "status": "Ongoing",
        "total_units": 50,
        "sold_units": 10,
        "unsold_units": 40,
        "sales_pct": 20.0,
        "registration_date": "2022-01-01",
        "detail_crawled": 1,
        "complaints_total": 0,
    },
    {
        "project_id": 13,
        "developer_id": 3,
        "name": "Gamma Court",
        "category": "residential",
        "status": "Not crawled",
        "registration_date": "2023-01-01",
        "detail_crawled": 0,
    },
]

PROGRESS = [
    (10, "June 2019", "2019-06-01", 50.0),
    (10, "December 2019", "2019-12-01", 80.0),
    (10, "March 2020", "2020-03-01", 100.0),
    (11, "March 2020", "2020-03-01", 20.0),
    (11, "March 2021", "2021-03-01", 40.0),
    (11, "January 2099", "2099-01-01", 45.0),  # project-end filing labelled with a future month
]


def _insert(conn, table, row):
    cols, marks = ",".join(row), ",".join("?" * len(row))
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(row.values()))  # noqa: S608


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "snapshot.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    for key, value in {
        "district": "Testcity",
        "projects_total": "5",
        "projects_analyzed": "4",
        "projects_pending": "1",
        "developers_identified": "3",
        "dataset_complete": "0",
        "crawl_state": "running",
    }.items():
        _insert(conn, "meta", {"key": key, "value": value})
    for d in DEVELOPERS:
        _insert(conn, "developers", d)
    for p in PROJECTS:
        _insert(conn, "projects", p)
    for seq, (pid, label, period, pct) in enumerate(PROGRESS):
        _insert(
            conn,
            "project_progress",
            {"project_id": pid, "seq": seq, "quarter_label": label, "period_date": period, "progress_pct": pct},
        )
    _insert(
        conn,
        "project_extensions",
        {
            "project_id": 11,
            "seq": 1,
            "extension_reg_no": "EXT/1",
            "approved_on": "2021-05-01",
            "status": "approved",
            "previous_completion_date": "2021-06-30",
            "extended_completion_date": "2022-06-30",
            "extension_days": 365,
        },
    )
    _insert(
        conn,
        "project_legal",
        {
            "project_id": 11,
            "record_type": "complaint",
            "reference": "CMP/A/1",
            "category": "rera_complaint",
            "filed_date": "2023-01-02",
            "status": "CLOSED",
        },
    )
    _insert(
        conn,
        "project_legal",
        {
            "project_id": 10,
            "record_type": "complaint",
            "reference": "CMP/B/2",
            "category": "adjudication_complaint",
            "filed_date": "2024-03-05",
            "status": "HEARING_SCHEDULE",
        },
    )
    _insert(
        conn,
        "project_legal",
        {
            "project_id": 11,
            "record_type": "challan",
            "reference": "CH/1",
            "category": "penalty_challan",
            "penalty_amount": 5000.0,
        },
    )
    _insert(
        conn,
        "project_events",
        {"project_id": 11, "event_date": "2023-01-02", "event_type": "complaint_filed", "title": "Complaint filed"},
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("RERA_APP_DB", str(path))
    cache.clear()
    return TestClient(app)


def test_meta_reports_partial_dataset(client):
    body = client.get("/api/meta").json()
    assert body["projects_analyzed"] == 4
    assert body["projects_pending"] == 1
    assert body["dataset_complete"] is False
    assert body["localities"] == ["Northpark"]


def test_search_partial_case_insensitive(client):
    names = [d["name"] for d in client.get("/api/developers?q=ALPHA").json()["developers"]]
    assert names == ["Alpha Estate LLP"]
    assert client.get("/api/developers?q=est").json()["total"] == 1


def test_unknown_values_stay_null_and_sort_last(client):
    devs = client.get("/api/developers?sort_by=sales_pct&sort_dir=desc").json()["developers"]
    assert [d["developer_id"] for d in devs] == [1, 2, 3]
    assert devs[2]["sales_pct"] is None
    assert devs[2]["complaints_total"] is None
    assert devs[2]["completed"] is None


def test_list_has_no_unit_columns(client):
    dev = client.get("/api/developers").json()["developers"][0]
    assert not {"units", "sold", "unsold"} & set(dev)
    assert {"avg_progress_pct", "pending", "extended_projects"} <= set(dev)


@pytest.mark.parametrize(
    ("query", "expected"),
    [("has_delayed=1", [1]), ("category=mixed", [1]), ("locality=Southside", [2]), ("min_sales_pct=50", [1])],
)
def test_filters(client, query, expected):
    devs = client.get(f"/api/developers?{query}").json()["developers"]
    assert [d["developer_id"] for d in devs] == expected


def test_compare_and_pagination(client):
    devs = client.get("/api/developers?ids=2,1&sort_by=name&sort_dir=asc").json()["developers"]
    assert [d["developer_id"] for d in devs] == [1, 2]
    body = client.get("/api/developers?page=2&page_size=2").json()
    assert body["total"] == 3
    assert len(body["developers"]) == 1


def test_bad_input_falls_back(client):
    res = client.get("/api/developers?sort_by=drop%20table&page=x&page_size=-5")
    assert res.status_code == 200
    assert res.json()["page"] == 1


def test_developer_report(client):
    body = client.get("/api/developers/1").json()
    assert body["developer"]["name"] == "Alpha Estate LLP"
    k = body["kpis"]
    assert (k["projects"], k["completed"], k["ongoing"], k["delayed"]) == (3, 1, 0, 1)
    assert k["avg_progress"] == 70.0  # mean of latest progress 100 and 40
    assert [p["project_id"] for p in body["projects"]] == [11, 10]  # newest registration first
    alpha_two = body["projects"][0]
    assert (alpha_two["units"], alpha_two["sold"], alpha_two["remaining"]) == (200, 140, 60)
    years = {p["year"]: p for p in body["yearly_progress"]["points"]}
    assert years[2019]["avg_progress"] == 80.0  # last report of the year, not the mean of the year
    assert years[2020] == {"year": 2020, "avg_progress": 60.0, "weighted_progress": 46.7, "n_projects": 2}
    assert body["yearly_progress"]["excluded_future_points"] == 1
    ext = body["extensions"]
    assert (ext["projects_with_extensions"], ext["extension_events"]) == (1, 1)
    assert ext["projects"][0]["extension_months"] == 12.0
    assert [d["project_id"] for d in body["delays"]] == [11]
    c = body["complaints"]
    assert (c["total"], c["open"], c["closed"], c["projects_with_complaints"]) == (2, 1, 1, 2)
    # one developer-wide list across projects, newest first
    assert [(r["reference"], r["project_name"], r["state"]) for r in c["records"]] == [
        ("CMP/B/2", "Alpha One", "open"),
        ("CMP/A/1", "Alpha Two", "closed"),
    ]
    assert (k["complaints"], k["open_complaints"]) == (2, 1)
    assert (body["regulatory"]["challans"], body["regulatory"]["penalty_amount"]) == (1, 5000.0)
    assert [r["reference"] for r in body["regulatory"]["records"]] == ["CH/1"]
    assert body["growth"]["registrations"] == [{"year": 2018, "projects": 1}, {"year": 2020, "projects": 1}]
    assert body["growth"]["expansion"]["points"][-1]["areas"] == 1


def test_developer_report_has_no_revenue_or_portfolio_unit_kpis(client):
    body = client.get("/api/developers/1").json()
    assert "revenue" not in json.dumps(body).lower()
    assert not {"units", "sold", "unsold"} & set(body["kpis"])


def test_unanalysed_developer_reports_null_not_zero(client):
    body = client.get("/api/developers/3").json()
    assert body["kpis"]["completed"] is None
    assert body["kpis"]["avg_progress"] is None
    assert body["yearly_progress"]["points"] == []
    assert body["construction"]["not_crawled"] == 1


def test_not_found(client):
    assert client.get("/api/developers/99").status_code == 404
    assert client.get("/api/projects/99").status_code == 404
    assert client.get("/api/nope").status_code == 404


def test_project_detail_and_not_crawled(client):
    body = client.get("/api/projects/11").json()
    assert body["developer_name"] == "Alpha Estate LLP"
    assert body["progress"][0]["progress_pct"] == 20.0
    assert body["extensions"][0]["extension_days"] == 365
    gamma = client.get("/api/projects/13").json()
    assert gamma["status"] == "Not crawled"
    assert gamma["sold_units"] is None
    assert gamma["extensions"] == []


def test_dashboard(client):
    body = client.get("/api/dashboard").json()
    k = body["kpis"]
    assert k["projects"] == 4
    assert k["analysed"] == 3
    assert k["developers"] == 2
    assert (k["completed"], k["ongoing"], k["delayed"], k["pending"]) == (1, 1, 1, 0)
    assert k["complaints"] == 2
    assert not {"units", "sold", "unsold", "sales_pct", "revenue"} & set(k)
    assert body["status"]["not_crawled"] == 1
    # market trend needs >= 10 reporting projects per year; the fixture's years are all thinner
    assert body["yearly_progress"]["points"] == []
    assert body["yearly_progress"]["omitted_years"] == [2019, 2020, 2021]
    bands = {b["band"]: b["projects"] for b in body["sales_distribution"]}
    assert (bands["100%"], bands["60-80%"], bands["20-40%"]) == (1, 1, 1)
    assert body["leaderboard"][0]["name"] == "Alpha Estate LLP"
    assert body["localities"][0] == {
        "area": "Northpark",
        "projects": 2,
        "completed": 1,
        "delayed": 1,
        "avg_progress": 70.0,
    }
    assert body["registrations_by_year"][-1] == {"year": 2023, "residential": 1, "mixed": 0}


def test_missing_snapshot_is_503(client, monkeypatch, tmp_path):
    monkeypatch.setenv("RERA_APP_DB", str(tmp_path / "missing.db"))
    res = client.get("/api/developers")
    assert res.status_code == 503
    assert "not available" in res.json()["error"]
