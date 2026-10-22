"""'Last updated on GujRERA': the portal's last-submission date and the filing dates behind a project's update history."""
import sqlite3

import pandas as pd

from analytics.app_export import FILING_TITLES, _day, _filing_events
from database import repositories as repo
from database.db import init_schema
from parser.project_parser import ProjectParser

RUN = "2026-10-22"


def parse(pid, raw_for, list_row_for, mutate=None):
    raw = dict(raw_for(pid))
    if mutate:
        mutate(raw)
    return ProjectParser(list_row_for(pid), raw, RUN, "2026-10-22T00:00:00+00:00").parse().rows["projects"][0]


def test_portal_last_submission_is_parsed_without_fraction_or_timezone_shift(raw_for, list_row_for):
    p = parse(12, raw_for, list_row_for)
    assert p["portal_last_submitted_at"] == "2019-04-05 16:00:58"           # '2019-04-05 16:00:58.0' as published
    assert p["details_updated_on"] is None or len(p["details_updated_on"]) == 10


def test_a_failed_or_missing_submission_answer_is_null(raw_for, list_row_for):
    p = parse(32294, raw_for, list_row_for)                                  # portal answers status 300 / data null
    assert p["portal_last_submitted_at"] is None
    raw_missing = parse(12, raw_for, list_row_for, mutate=lambda r: r.pop("last_submitted"))
    assert raw_missing["portal_last_submitted_at"] is None                    # not fetched yet -> NULL, never a guess


def test_engineer_certificate_date(raw_for, list_row_for):
    p = parse(476, raw_for, list_row_for)
    assert p["engineer_cert_submitted_on"] is None or len(p["engineer_cert_submitted_on"]) == 10


def test_a_new_filing_is_a_detected_change():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    repo._columns_cache.clear()
    base = {"project_id": 1, "project_name": "X", "portal_last_submitted_at": "2026-01-01 10:00:00"}
    assert repo.upsert_project(conn, base, "r1", "t1") == []                  # first collection is not a change
    assert repo.upsert_project(conn, base, "r2", "t2") == []
    changes = repo.upsert_project(conn, {**base, "portal_last_submitted_at": "2026-03-05 09:00:00"}, "r3", "t3")
    assert [c[0] for c in changes] == ["portal_last_submitted_at"]
    row = conn.execute("SELECT first_seen_at, last_changed_at FROM projects WHERE project_id=1").fetchone()
    assert (row["first_seen_at"], row["last_changed_at"]) == ("t1", "t3")


def test_filing_events_use_published_dates_only():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    conn.execute("INSERT INTO project_form3(project_id, form_three_id, submitted_on, number_of_units, is_latest) "
                 "VALUES (5, 9, '2026-09-30', 120, 1)")
    conn.execute("INSERT INTO project_form3(project_id, form_three_id, submitted_on, is_latest) VALUES (5, 8, '2025-01-01', 0)")
    conn.execute("INSERT INTO project_amenities(project_id, item_key, form1_submitted_on) VALUES (5, 'a', '2026-08-01')")
    conn.execute("INSERT INTO project_amenities(project_id, item_key, form1_submitted_on) VALUES (5, 'b', '2026-08-01')")
    p = pd.DataFrame([
        {"project_id": 5, "portal_last_submitted_at": "2026-10-02 11:00:00", "last_qpr_submitted_at": "2026-07-15",
         "latest_progress_quarter": "June 2026", "details_updated_on": None, "engineer_cert_submitted_on": "2026-06-01"},
        {"project_id": 6, "portal_last_submitted_at": None, "last_qpr_submitted_at": float("nan"),
         "latest_progress_quarter": None, "details_updated_on": None, "engineer_cert_submitted_on": None},
    ])
    ev = {(e[0], e[2]): e for e in _filing_events(conn, p, "5,6")}
    assert ev[(5, "portal_submission")][1] == "2026-10-02"                    # date only
    assert ev[(5, "qpr")][1:5] == ("2026-07-15", "qpr", FILING_TITLES["qpr"], "June 2026")
    assert ev[(5, "unit_booking")][1] == "2026-09-30" and ev[(5, "unit_booking")][4] == "120 units declared"
    assert ev[(5, "facilities")][1] == "2026-08-01"                           # one row per form, not per facility
    assert ev[(5, "engineer_certificate")][1] == "2026-06-01"
    assert [k for k in ev if k[0] == 6] == []                                 # unknown / NaN dates produce no event
    assert len(ev) == 5                                                       # the older Form 3 is not an "update"
    assert _day(None) is None and _day("not a date") is None and _day("2026-10-02 11:00:00") == "2026-10-02"
