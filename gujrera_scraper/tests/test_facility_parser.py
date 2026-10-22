"""Facilities / amenities from Form 1 (recorded fixtures) and per-block floors / lifts."""
from parser import facility_parser
from parser.facility_parser import COMMON_ITEMS, parse_block_floors, parse_facilities


def by_key(rows):
    return {r["item_key"]: r for r in rows}


def test_common_facilities_keep_applicability_progress_and_dates(raw_for):
    rows = by_key(parse_facilities(476, raw_for(476)))
    assert set(COMMON_ITEMS) <= set(rows)                       # every portal item of the form is reported
    road = rows["internalRoad"]
    assert (road["applicable"], road["progress_pct"], road["label"]) == ("YES", 70.0, "Internal roads and footpaths")
    assert (road["planned_start"], road["planned_end"]) == ("2015-12-15", "2025-01-25")
    assert road["actual_start"] is None and road["actual_end"] is None      # '' in the source -> unknown, not a date
    assert road["remarks"] is None                                           # '-' is not a remark
    assert rows["communityBuildings"]["progress_pct"] == 100.0
    sec = rows["security"]
    assert sec["applicable"] == "NO" and sec["planned_start"] is None       # 'NA' dates are unknown, not invented
    assert all(r["project_id"] == 476 and r["source"] == "form_one_common" for r in rows.values())
    assert rows["internalRoad"]["form_one_id"] == 273232


def test_named_extra_facilities_are_kept_verbatim(raw_for):
    rows = parse_facilities(184, raw_for(184))
    club = next(r for r in rows if r["source"] == "form_one_other")
    assert club["label"] == "Club House" and club["category"] == "named_by_promoter"
    assert (club["applicable"], club["progress_pct"], club["remarks"]) == ("YES", 100.0, "COMPLETED CLUB HOUSE")
    assert (club["planned_start"], club["planned_end"]) == ("2016-10-01", "2022-12-31")
    assert club["item_key"].startswith("other:")


def test_placeholder_remarks_are_not_remarks():
    for placeholder in ("NO REMARK", "No remarks", "NIL", "NA", "-", " ", "N.A."):
        assert facility_parser._remark(placeholder) is None
    assert facility_parser._remark("Work  in   progress") == "Work in progress"


def test_placeholder_captions_are_not_amenities(raw_for):
    raw = dict(raw_for(184))
    others = raw["form_one"][0]["data"]["formOneB"]["oneBOtherDetails"]
    for cap in ("No", "NA", "nil", " - ", ""):
        others.append(dict(others[0], fromBOntherId=900 + len(others), caption=cap))
    named = [r for r in parse_facilities(184, raw) if r["source"] == "form_one_other"]
    assert [r["label"] for r in named] == ["Club House"]


def test_projects_without_extras_have_no_named_items(raw_for):
    assert not [r for r in parse_facilities(476, raw_for(476)) if r["source"] == "form_one_other"]


def test_missing_form_one_means_not_declared_not_empty_amenities(raw_for):
    raw = dict(raw_for(476))
    raw.pop("form_one")
    assert parse_facilities(476, raw) == [] and parse_block_floors(raw) == {}
    assert parse_facilities(476, {"form_one": ({"data": None}, {})}) == []


def test_unknown_portal_items_are_not_dropped(raw_for):
    raw = dict(raw_for(476))
    j = raw["form_one"][0]
    j["data"]["formOneB"].update({"rooftopGardenYesNo": "YES", "rooftopGardenWorkDone": "40"})
    row = by_key(parse_facilities(476, raw))["rooftopGarden"]
    assert (row["label"], row["applicable"], row["progress_pct"]) == ("Rooftop garden", "YES", 40.0)


def test_block_floors_and_lifts(raw_for):
    floors = parse_block_floors(raw_for(476))
    assert floors["A1"] == {"floors": 6, "lifts": 1}
    assert parse_block_floors(raw_for(12))["A"] == {"floors": None, "lifts": None}   # not filled in -> NULL
    assert facility_parser.form_one_data({}) is None
