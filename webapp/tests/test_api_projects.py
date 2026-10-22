"""Projects list, blocks and unit-wise booking endpoints, multi-city / multi-category filters.

Synthetic rows pin the rules: unit data that was not captured is never presented as unbooked, a missing units file
is reported (not an error), area/date values stay NULL when unknown, and older snapshots keep working.
"""

import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gujrera_scraper"))
from analytics.app_export import SCHEMA, natural_sort_key  # noqa: E402

from webapp.server import cache  # noqa: E402
from webapp.server.app import app  # noqa: E402


def _ins(conn, table, row):
    conn.execute(f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' * len(row))})", list(row.values()))  # noqa: S608


PROJECTS = [
    dict(
        project_id=1,
        developer_id=1,
        name="Alpha Heights",
        name_search="alpha heights pr/1",
        rera_number="PR/1",
        category="residential",
        city="Ahmedabad",
        district="Ahmedabad",
        locality="Bopal",
        status="Ongoing",
        total_units=6,
        sold_units=4,
        unsold_units=2,
        registration_date="2021-01-01",
        detail_crawled=1,
        unit_data_state="complete",
        units_as_of="2026-09-30",
        units_row_count=6,
    ),
    dict(
        project_id=2,
        developer_id=1,
        name="Alpha Plaza",
        name_search="alpha plaza pr/2",
        rera_number="PR/2",
        category="commercial",
        city="Ahmedabad",
        district="Ahmedabad",
        locality="SG Highway",
        status="Completed",
        registration_date="2019-01-01",
        detail_crawled=1,
        unit_data_state="partial",
        unit_data_note="unit lists missing for blocks: B",
        units_as_of="2026-09-30",
        units_row_count=2,
    ),
    dict(
        project_id=3,
        developer_id=2,
        name="Beta Plots",
        name_search="beta plots pr/3",
        rera_number="PR/3",
        category="plotted",
        city="Gandhinagar",
        district="Gandhinagar",
        locality="Sector 5",
        status="Ongoing",
        registration_date="2022-01-01",
        detail_crawled=1,
        unit_data_state="not_published",
    ),
    dict(
        project_id=4,
        developer_id=2,
        name="Beta Homes",
        name_search="beta homes pr/4",
        rera_number="PR/4",
        category="mixed",
        city="Gandhinagar",
        district="Gandhinagar",
        locality="Sector 5",
        status="Delayed",
        registration_date="2020-01-01",
        detail_crawled=1,
        unit_data_state="not_fetched",
    ),
    dict(
        project_id=5,
        developer_id=2,
        name="Gamma Court",
        name_search="gamma court pr/5",
        rera_number="PR/5",
        category="residential",
        city="Gandhinagar",
        district="Gandhinagar",
        status="Not crawled",
        registration_date="2023-01-01",
        detail_crawled=0,
        unit_data_state="not_fetched",
    ),
]
DEVS = [
    dict(
        developer_id=1,
        name="Alpha Estate LLP",
        name_search="alpha estate llp",
        projects_total=2,
        projects_analyzed=2,
        commercial_projects=1,
        residential_projects=1,
    ),
    dict(
        developer_id=2,
        name="Beta Builders",
        name_search="beta builders",
        projects_total=3,
        projects_analyzed=2,
        plotted_projects=1,
        mixed_projects=1,
    ),
]


def unit(block, no, status, usage="Residential", carpet=40.0, area=2.0, agr=None, kind="single"):
    return dict(
        project_id=1,
        block_name=block,
        unit_no=no,
        unit_no_norm=no.lower(),
        dup_seq=0,
        unit_sort=natural_sort_key(no.lower()),
        unit_kind=kind,
        usage=usage,
        usage_class=usage.lower(),
        carpet_area_sqm=carpet,
        area_excl_balcony_terrace_sqm=area,
        status=status,
        status_norm=(status or "").lower() if status in ("BOOKED", "UNBOOKED") else ("other" if status else "unknown"),
        agreement_date=agr,
        form3_submitted_on="2026-09-30",
    )


UNITS = [
    unit("A", "101", "BOOKED", agr="2024-05-01"),
    unit("A", "102", "BOOKED"),
    unit("A", "1001", "UNBOOKED", area=0.0),
    unit("A", "20", "SOLD", carpet=None, area=None),
    unit("B", "B-1", "BOOKED", usage="Shop"),
    unit("B", "B-2", None, usage="Shop"),
]


def build(tmp_path, with_units=True, with_city=True):
    app_db = tmp_path / "snap.db"
    c = sqlite3.connect(app_db)
    c.executescript(SCHEMA)
    for k, v in {
        "district": "Ahmedabad + Gandhinagar",
        "projects_total": "5",
        "projects_analyzed": "4",
        "dataset_complete": "0",
    }.items():
        _ins(c, "meta", {"key": k, "value": v})
    for p in PROJECTS:
        _ins(c, "projects", {k: v for k, v in p.items() if with_city or k != "city"})
    for d in DEVS:
        _ins(c, "developers", d)
    for city, cat, total, an in (
        ("Ahmedabad", "residential", 1, 1),
        ("Ahmedabad", "commercial", 1, 1),
        ("Gandhinagar", "plotted", 1, 1),
        ("Gandhinagar", "mixed", 1, 1),
        ("Gandhinagar", "residential", 1, 0),
    ):
        _ins(c, "meta_counts", dict(city=city, category=cat, total=total, analyzed=an))
    _ins(
        c,
        "project_blocks",
        dict(
            project_id=1,
            block_name="A",
            planned_units=4,
            sold_units=3,
            unsold_units=1,
            unit_rows=4,
            unit_booked=2,
            unit_unbooked=1,
            unit_other=1,
            fetch_outcome="ok",
        ),
    )
    _ins(
        c,
        "project_blocks",
        dict(
            project_id=1,
            block_name="B",
            planned_units=2,
            sold_units=1,
            unsold_units=1,
            unit_rows=2,
            unit_booked=1,
            unit_unbooked=0,
            unit_other=1,
            fetch_outcome="ok",
        ),
    )
    _ins(
        c,
        "project_form3",
        dict(project_id=1, form_three_id=9, submitted_on="2026-09-30", number_of_units=6, is_latest=1),
    )
    for key, label, cat, src, appl, prog in (
        ("internalRoad", "Internal roads and footpaths", "roads", "form_one_common", "YES", 70.0),
        ("security", "Security", "safety_security", "form_one_common", "NO", 0.0),
        ("other:1", "Club House", "named_by_promoter", "form_one_other", "YES", 100.0),
    ):
        _ins(
            c,
            "project_amenities",
            dict(
                project_id=1,
                item_key=key,
                label=label,
                category=cat,
                source=src,
                applicable=appl,
                progress_pct=prog,
                form1_submitted_on="2026-09-11",
            ),
        )
    c.execute("UPDATE project_blocks SET floors=12, lifts=2 WHERE project_id=1 AND block_name='A'")
    _ins(c, "project_infrastructure", dict(project_id=1, category="nearby", name="airport_distance", value="9 km"))
    _ins(
        c,
        "project_documents",
        dict(
            project_id=1,
            document_type="registration_certificate",
            document_name="PR/1",
            document_date="2021-01-01",
            url="https://x/doc",
        ),
    )
    for i, (lat, lng) in enumerate([(23.0, 72.5), (23.0, 72.6), (23.1, 72.6)]):
        _ins(c, "project_polygon", dict(project_id=1, seq=i, lat=lat, lng=lng))
    if with_units:
        for r in UNITS:
            _ins(c, "unit_inventory", r)
    else:  # a snapshot published before the unit-wise layer has no unit table at all
        c.execute("DROP TABLE unit_inventory")
    c.commit()
    c.close()
    return app_db


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RERA_APP_DB", str(build(tmp_path)))
    cache.clear()
    return TestClient(app)


# ------------------------------------------------------------------------------------------------ meta / list
def test_meta_exposes_cities_categories_and_counts(client):
    m = client.get("/api/meta").json()
    assert m["cities"] == ["Ahmedabad", "Gandhinagar"]
    assert m["categories"] == ["residential", "mixed", "commercial", "plotted"]
    assert m["units_available"] is True
    assert {"city": "Gandhinagar", "category": "plotted", "total": 1, "analyzed": 1} in m["meta_counts"]
    assert m["localities_by_city"].get("Gandhinagar") == ["Sector 5"]


@pytest.mark.parametrize(
    "params, expected",
    [
        ("", {1, 2, 3, 4, 5}),
        ("city=Ahmedabad", {1, 2}),
        ("city=gandhinagar", {3, 4, 5}),  # case-insensitive
        ("category=commercial", {2}),
        ("category=plotted,mixed", {3, 4}),
        ("category=bogus", {1, 2, 3, 4, 5}),  # unknown categories are ignored, not an error
        ("city=Gandhinagar&category=residential", {5}),
        ("status=Ongoing", {1, 3}),
        ("locality=Sector 5", {3, 4}),
        ("developer=1", {1, 2}),
        ("developer=beta", {3, 4, 5}),
        ("q=PR/2", {2}),
        ("q=alpha&city=Gandhinagar", set()),
    ],
)
def test_project_list_filters(client, params, expected):
    body = client.get(f"/api/projects?{params}").json()
    assert {p["project_id"] for p in body["projects"]} == expected and body["total"] == len(expected)


def test_project_list_rows_pagination_sort_and_unit_state(client):
    body = client.get("/api/projects?sort_by=name&sort_dir=asc&page_size=2&page=2").json()
    assert [p["name"] for p in body["projects"]] == ["Beta Homes", "Beta Plots"] and body["total"] == 5
    row = next(p for p in client.get("/api/projects").json()["projects"] if p["project_id"] == 5)
    assert row["unit_data_state"] == "not_fetched" and row["total_units"] is None and row["city"] == "Gandhinagar"
    assert client.get("/api/projects?city=Ahmedabad").json()["status_counts"] == {"Ongoing": 1, "Completed": 1}
    assert client.get("/api/projects?sort_by=; DROP TABLE projects").status_code == 200  # whitelist, not SQL


def test_global_search_finds_projects_developers_and_localities(client):
    r = client.get("/api/search?q=alpha").json()
    assert [p["name"] for p in r["projects"]] == ["Alpha Heights", "Alpha Plaza"] or {
        p["name"] for p in r["projects"]
    } == {"Alpha Heights", "Alpha Plaza"}
    assert r["projects_total"] == 2 and r["developers_total"] == 1 and r["developers"][0]["name"] == "Alpha Estate LLP"
    row = next(p for p in r["projects"] if p["name"] == "Alpha Plaza")
    assert (row["city"], row["category"], row["locality"], row["rera_number"]) == (
        "Ahmedabad",
        "commercial",
        "SG Highway",
        "PR/2",
    )
    assert client.get("/api/search?q=pr/3").json()["projects"][0]["name"] == "Beta Plots"  # RERA number
    loc = client.get("/api/search?q=sector").json()["localities"]
    assert loc == [{"name": "Sector 5", "city": "Gandhinagar", "projects": 2}]


def test_global_search_matches_every_word_in_any_order_and_ignores_case(client):
    assert {p["name"] for p in client.get("/api/search?q=HEIGHTS alpha").json()["projects"]} == {"Alpha Heights"}
    assert client.get("/api/search?q=alpha gamma").json()["projects_total"] == 0
    assert [p["name"] for p in client.get("/api/projects?q=heights alpha").json()["projects"]] == ["Alpha Heights"]
    assert client.get("/api/developers?q=estate alpha").json()["total"] == 1


def test_global_search_edge_cases(client):
    empty = {"projects": [], "projects_total": 0, "developers": [], "developers_total": 0, "localities": []}
    for q in ("", "a", "  ", "zzzzzz"):
        r = client.get("/api/search", params={"q": q}).json()
        assert {k: r[k] for k in empty} == empty
    r = client.get("/api/search?q=beta&limit=1").json()
    assert len(r["projects"]) == 1 and r["projects_total"] == 2  # total counts all matches
    assert len(client.get("/api/search?q=beta&limit=999").json()["projects"]) <= 10
    assert client.get("/api/search?q=%27%3B%20DROP%20TABLE%20projects%3B--").status_code == 200  # bound parameters
    assert client.get("/api/projects").json()["total"] == 5


def test_project_prefix_matches_rank_first(client):
    names = [p["name"] for p in client.get("/api/search?q=beta").json()["projects"]]
    assert names[0].lower().startswith("beta") and len(names) == 2


def test_project_list_can_return_every_project_in_one_page(client):
    body = client.get("/api/projects?page_size=10000").json()
    assert body["page_size"] == 10000 and len(body["projects"]) == body["total"] == 5
    assert client.get("/api/projects?page_size=99999999").json()["page_size"] == 10000  # still capped
    assert client.get("/api/developers?page_size=10000").json()["page_size"] == 200  # other lists keep their cap


def test_developers_and_dashboard_take_city_and_category(client):
    assert client.get("/api/developers?city=Ahmedabad").json()["total"] == 1
    assert client.get("/api/developers?category=plotted").json()["developers"][0]["name"] == "Beta Builders"
    d = client.get("/api/dashboard?city=Gandhinagar").json()
    assert d["kpis"]["projects"] == 3
    reg = {r["year"]: r for r in d["registrations_by_year"]}
    assert reg[2022]["plotted"] == 1 and "commercial" not in reg[2022]  # commercial has no Gandhinagar project
    assert client.get("/api/dashboard").json()["kpis"]["projects"] == 5


# ------------------------------------------------------------------------------------------------ project detail
def test_project_detail_has_new_sections_and_keeps_old_keys(client):
    p = client.get("/api/projects/1").json()
    for key in ("progress", "blocks", "legal", "events", "extensions", "developer_name", "city"):
        assert key in p
    assert p["form3"]["submitted_on"] == "2026-09-30" and p["infrastructure"][0]["value"] == "9 km"
    assert p["documents"][0]["url"] == "https://x/doc" and p["polygon"] == [[23.0, 72.5], [23.0, 72.6], [23.1, 72.6]]
    assert p["unit_data_state"] == "complete" and p["units_available"] is True
    assert p["blocks"][0]["unit_booked"] == 2 and p["blocks"][0]["sold_units"] == 3  # two sources side by side


def test_amenities_and_block_floors_are_exposed(client):
    p = client.get("/api/projects/1").json()
    by_key = {a["item_key"]: a for a in p["amenities"]}
    assert set(by_key) == {"internalRoad", "security", "other:1"}
    assert by_key["other:1"]["label"] == "Club House" and by_key["other:1"]["source"] == "form_one_other"
    assert (by_key["security"]["applicable"], by_key["security"]["progress_pct"]) == ("NO", 0.0)
    blocks = {b["block_name"]: b for b in p["blocks"]}
    assert (blocks["A"]["floors"], blocks["A"]["lifts"]) == (12, 2) and blocks["B"]["floors"] is None
    assert client.get("/api/projects/2").json()["amenities"] == []  # nothing collected -> empty, not "no amenities"


def test_blocks_endpoint(client):
    b = client.get("/api/projects/1/blocks").json()
    assert [x["block_name"] for x in b["blocks"]] == ["A", "B"] and b["unit_data_state"] == "complete"
    assert client.get("/api/projects/999/blocks").status_code == 404


# ------------------------------------------------------------------------------------------------ units
def test_units_listing_filters_and_summary(client):
    u = client.get("/api/projects/1/units?block=A").json()
    assert u["total"] == 4 and u["summary"] == {"booked": 2, "unbooked": 1, "other": 1, "unknown": 0, "total": 4}
    # natural order: 20, 101, 102, 1001 (not lexical)
    assert [x["unit_no"] for x in u["units"]] == ["20", "101", "102", "1001"]
    booked = client.get("/api/projects/1/units?block=A&status=booked").json()
    assert [x["unit_no"] for x in booked["units"]] == ["101", "102"]
    assert booked["summary"]["total"] == 4  # chips keep counting all statuses
    assert client.get("/api/projects/1/units?q=b-1").json()["units"][0]["unit_no"] == "B-1"
    shops = client.get("/api/projects/1/units?usage=shop").json()
    assert shops["total"] == 2 and shops["summary"]["unknown"] == 1 and "residential" in shops["usage_classes"]
    assert client.get("/api/projects/1/units?status=bogus").json()["total"] == 6  # invalid filter ignored


def test_unknown_values_stay_null_and_zero_stays_zero(client):
    rows = {x["unit_no"]: x for x in client.get("/api/projects/1/units?block=A").json()["units"]}
    assert rows["20"]["carpet_area_sqm"] is None and rows["20"]["area_excl_balcony_terrace_sqm"] is None
    assert rows["20"]["status"] == "SOLD" and rows["20"]["status_norm"] == "other"  # source value preserved
    assert rows["1001"]["area_excl_balcony_terrace_sqm"] == 0.0 and rows["1001"]["agreement_date"] is None
    assert rows["101"]["agreement_date"] == "2024-05-01"
    unknown = client.get("/api/projects/1/units?block=B&status=unknown").json()["units"]
    assert [x["unit_no"] for x in unknown] == ["B-2"] and unknown[0]["status"] is None


def test_unit_sorting_and_paging(client):
    u = client.get("/api/projects/1/units?sort_by=carpet&sort_dir=desc&page_size=2").json()
    assert u["total"] == 6 and len(u["units"]) == 2 and u["page_size"] == 2
    assert client.get("/api/projects/1/units?page_size=100000").json()["page_size"] == 500
    carpets = [x["carpet_area_sqm"] for x in client.get("/api/projects/1/units?sort_by=carpet").json()["units"]]
    assert carpets[-1] is None  # unknown sorts last


def test_projects_without_complete_unit_data_are_reported_not_empty(client):
    u = client.get("/api/projects/2/units").json()
    assert u["unit_data_state"] == "partial" and "B" in u["unit_data_note"] and u["total"] == 0
    assert client.get("/api/projects/4/units").json()["unit_data_state"] == "not_fetched"
    assert client.get("/api/projects/999/units").status_code == 404


def test_snapshot_without_unit_table_degrades_gracefully(tmp_path, monkeypatch):
    monkeypatch.setenv("RERA_APP_DB", str(build(tmp_path, with_units=False)))
    cache.clear()
    c = TestClient(app)
    u = c.get("/api/projects/1/units").json()
    assert u["available"] is False and u["units"] == [] and u["summary"]["total"] == 0
    assert c.get("/api/meta").json()["units_available"] is False
    assert c.get("/api/projects/1").json()["units_available"] is False


def test_snapshot_without_city_column_uses_district(tmp_path, monkeypatch):
    """Snapshots published before the multi-city layout have no `city` column."""
    path = build(tmp_path, with_units=False)
    conn = sqlite3.connect(path)
    conn.execute("DROP INDEX ix_projects_city_category")
    conn.execute("DROP INDEX ix_projects_locality")
    conn.execute("ALTER TABLE projects DROP COLUMN city")
    conn.execute("DROP TABLE meta_counts")
    conn.commit()
    conn.close()
    monkeypatch.setenv("RERA_APP_DB", str(path))
    cache.clear()
    c = TestClient(app)
    assert {p["project_id"] for p in c.get("/api/projects?city=Gandhinagar").json()["projects"]} == {3, 4, 5}
    m = c.get("/api/meta").json()
    assert m["cities"] == ["Ahmedabad", "Gandhinagar"] and m["categories"]
    assert c.get("/api/projects/3").json()["city"] == "Gandhinagar"
