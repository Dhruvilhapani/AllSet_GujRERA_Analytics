"""Unit tests for the pandas / NumPy metric functions (webapp/server/analytics.py).

Each test pins one documented rule from webapp/METRICS.md with small synthetic frames.
"""

from datetime import date

import numpy as np
import pandas as pd

from webapp.server import analytics as an

AS_OF = date(2026, 6, 30)


def projects(*rows):
    base = {
        "project_id": None,
        "name": "",
        "status": "Ongoing",
        "stalled": 0,
        "detail_crawled": 1,
        "locality": None,
        "registration_date": None,
        "original_completion_date": None,
        "current_completion_date": None,
        "days_beyond_current": np.nan,
        "extension_count": np.nan,
        "progress_pct": np.nan,
        "total_units": np.nan,
        "sold_units": np.nan,
        "unsold_units": np.nan,
    }
    return pd.DataFrame([{**base, **r} for r in rows])


def progress(*rows):
    return pd.DataFrame(rows, columns=["project_id", "seq", "period_date", "progress_pct"])


# ------------------------------------------------------------------ yearly progress
def test_yearly_progress_uses_last_report_per_year_and_mean_across_projects():
    pr = progress(
        (1, 1, "2020-03-01", 30.0),
        (1, 2, "2020-12-01", 60.0),
        (2, 1, "2020-06-01", 20.0),
        (1, 3, "2021-03-01", 90.0),
    )
    out = an.yearly_progress(
        pr, projects({"project_id": 1, "total_units": 100}, {"project_id": 2, "total_units": 300}), AS_OF
    )
    assert out["points"] == [
        {"year": 2020, "avg_progress": 40.0, "weighted_progress": 30.0, "n_projects": 2},
        {"year": 2021, "avg_progress": 90.0, "weighted_progress": 90.0, "n_projects": 1},
    ]


def test_yearly_progress_excludes_future_labels_caps_at_100_and_drops_nulls():
    pr = progress((1, 1, "2025-03-01", 120.0), (1, 2, "2027-01-01", 100.0), (2, 1, "2025-06-01", None))
    out = an.yearly_progress(pr, projects({"project_id": 1}, {"project_id": 2}), AS_OF)
    assert out["excluded_future_points"] == 1
    assert out["points"] == [{"year": 2025, "avg_progress": 100.0, "weighted_progress": None, "n_projects": 1}]


def test_yearly_progress_empty_has_no_points():
    out = an.yearly_progress(progress(), projects({"project_id": 1}), AS_OF)
    assert out["points"] == []
    assert out["method"]


def test_yearly_progress_min_projects_omits_thin_years():
    pr = progress((1, 1, "2017-03-01", 100.0), (1, 2, "2018-03-01", 50.0), (2, 1, "2018-06-01", 70.0))
    out = an.yearly_progress(pr, projects({"project_id": 1}, {"project_id": 2}), AS_OF, min_projects=2)
    assert [p["year"] for p in out["points"]] == [2018]
    assert out["omitted_years"] == [2017]
    assert "2017" in out["method"]


def test_progress_revisions_flags_large_drops():
    pr = progress((1, 1, "2020-03-01", 83.0), (1, 2, "2020-06-01", 36.0), (1, 3, "2020-09-01", 40.0))
    rev = an.progress_revisions(pr)
    assert rev[["from_pct", "to_pct"]].values.tolist() == [[83.0, 36.0]]


# ------------------------------------------------------------------ status
def test_status_breakdown_counts_analysed_only_and_stalled_is_a_flag():
    df = projects(
        {"project_id": 1, "status": "Completed"},
        {"project_id": 2, "status": "Ongoing", "stalled": 1},
        {"project_id": 3, "status": "Delayed"},
        {"project_id": 4, "status": "Pending"},
        {"project_id": 5, "status": "Not crawled", "detail_crawled": 0},
    )
    assert an.status_breakdown(df) == {
        "completed": 1,
        "ongoing": 1,
        "delayed": 1,
        "pending": 1,
        "stalled": 1,
        "not_crawled": 1,
        "analysed": 4,
    }


def test_avg_latest_progress_null_when_no_reports():
    assert an.avg_latest_progress(projects({"project_id": 1})) is None
    assert (
        an.avg_latest_progress(projects({"project_id": 1, "progress_pct": 50}, {"project_id": 2, "progress_pct": 110}))
        == 75.0
    )


# ------------------------------------------------------------------ sales
def test_sales_by_project_missing_is_not_zero():
    df = projects(
        {"project_id": 1, "name": "A", "sold_units": 30, "unsold_units": 10},
        {"project_id": 2, "name": "B", "sold_units": None, "unsold_units": 10},
        {"project_id": 3, "name": "C", "sold_units": 0, "unsold_units": 0},
    )
    out = an.sales_by_project(df)
    assert [(p["name"], p["sales_pct"]) for p in out["projects"]] == [("A", 75.0)]
    assert out["projects_with_data"] == 1
    assert out["projects_without_data"] == 2


def test_sales_distribution_bands():
    df = projects(
        {"project_id": 1, "sold_units": 100, "unsold_units": 0},
        {"project_id": 2, "sold_units": 999, "unsold_units": 1},
        {"project_id": 3, "sold_units": 0, "unsold_units": 10},
    )
    bands = {b["band"]: b["projects"] for b in an.sales_distribution(df)}
    assert (bands["100%"], bands["80-99%"], bands["0-20%"], bands["40-60%"]) == (1, 1, 1, 0)


def test_best_worst_thresholds():
    df = projects(
        {"project_id": 1, "name": "Big", "sold_units": 90, "unsold_units": 10, "progress_pct": 30},
        {"project_id": 2, "name": "Mid", "sold_units": 10, "unsold_units": 30, "progress_pct": 70},
        {"project_id": 3, "name": "Tiny", "sold_units": 1, "unsold_units": 9, "progress_pct": 5},  # < 20 units
        {
            "project_id": 4,
            "name": "Done",
            "status": "Completed",
            "sold_units": 50,
            "unsold_units": 50,
            "progress_pct": 100,
        },
    )
    out = {h["label"]: h for h in an.best_worst(df)}
    assert (out["Highest sales"]["name"], out["Lowest sales"]["name"]) == ("Big", "Mid")
    assert (out["Highest progress"]["name"], out["Lowest progress"]["name"]) == ("Mid", "Tiny")
    assert all(h["metric"] for h in out.values())


def test_best_worst_hidden_with_fewer_than_two_eligible():
    assert an.best_worst(projects({"project_id": 1, "sold_units": 50, "unsold_units": 50, "progress_pct": 10})) == []


# ------------------------------------------------------------------ extensions / delays
def test_extension_overview_counts_approved_only_and_months():
    df = projects(
        {
            "project_id": 1,
            "name": "Ext",
            "original_completion_date": "2021-01-01",
            "current_completion_date": "2022-01-01",
        },
        {"project_id": 2, "name": "NoDate", "original_completion_date": None, "current_completion_date": "2022-01-01"},
        {"project_id": 3, "name": "Rejected"},
    )
    ext = pd.DataFrame(
        [(1, 1, "approved"), (1, 2, "approved"), (2, 1, "approved"), (3, 1, "rejected")],
        columns=["project_id", "seq", "status"],
    )
    out = an.extension_overview(df, ext)
    assert (out["projects_with_extensions"], out["extension_events"]) == (2, 3)
    rows = {r["name"]: r for r in out["projects"]}
    assert rows["Ext"]["extension_months"] == 12.0
    assert rows["Ext"]["extensions"] == 2
    assert rows["NoDate"]["extension_months"] is None
    assert out["avg_extension_months"] == 12.0
    assert out["reason_available"] is False


def test_extended_project_is_not_a_delay():
    df = projects(
        {"project_id": 1, "status": "Ongoing", "extension_count": 2},
        {"project_id": 2, "status": "Delayed", "days_beyond_current": np.nan},
        {"project_id": 3, "status": "Delayed", "days_beyond_current": 90},
    )
    assert [d["project_id"] for d in an.delayed_projects(df)] == [3, 2]
    assert an.delayed_projects(df)[1]["days_past_approved_date"] is None


# ------------------------------------------------------------------ location / growth / legal
def test_location_breakdown_unknown_handling():
    df = projects(
        {"project_id": 1, "locality": "A", "progress_pct": 40},
        {"project_id": 2, "locality": "A", "progress_pct": np.nan},
        {"project_id": 3, "locality": None},
    )
    rows = an.location_breakdown(df)
    assert rows[0] == {"area": "A", "projects": 2, "completed": 0, "delayed": 0, "avg_progress": 40.0}
    assert rows[1]["area"] == "Location not published"
    assert rows[1]["avg_progress"] is None
    assert [r["area"] for r in an.location_breakdown(df, include_unknown=False)] == ["A"]


def test_growth_and_geographic_expansion():
    df = projects(
        {"project_id": 1, "registration_date": "2019-02-01", "locality": "A"},
        {"project_id": 2, "registration_date": "2019-09-01", "locality": "A"},
        {"project_id": 3, "registration_date": "2021-01-01", "locality": "B"},
        {"project_id": 4, "registration_date": "2022-01-01", "locality": None},
    )
    assert an.portfolio_growth(df) == [
        {"year": 2019, "projects": 2},
        {"year": 2021, "projects": 1},
        {"year": 2022, "projects": 1},
    ]
    exp = an.geographic_expansion(df)
    assert exp["points"] == [{"year": 2019, "areas": 1}, {"year": 2021, "areas": 2}]
    assert exp["projects_without_locality"] == 1


def test_legal_breakdown():
    legal = pd.DataFrame(
        [
            ("complaint", "rera_complaint", None),
            ("complaint", None, None),
            ("suo_motu", None, None),
            ("challan", None, 5000.0),
        ],
        columns=["record_type", "category", "penalty_amount"],
    )
    out = an.legal_breakdown(legal)
    assert (out["complaints"], out["suo_motu"], out["challans"], out["penalty_amount"]) == (2, 1, 1, 5000.0)
    assert {c["category"] for c in out["categories"]} == {"Complaint to Authority (Form A)", "Complaint (other)"}
    assert an.legal_breakdown(legal.iloc[0:0])["categories"] == []


def test_complaint_state():
    assert an.complaint_state("CLOSED", None) == "closed"
    assert an.complaint_state("DELETED", None) == "closed"
    assert an.complaint_state("HEARING_SCHEDULE", None) == "open"
    assert an.complaint_state("order_published", "Final") == "closed"
    assert an.complaint_state("order_published", "Interim") == "open"
    assert an.complaint_state("order_published", None) == "unknown"
    assert an.complaint_state(None, None) == "unknown"


def test_developer_complaints_lists_all_projects_newest_first():
    legal = pd.DataFrame(
        [
            (1, "P1", "complaint", "A", "rera_complaint", None, "2020-01-01", "CLOSED", None, None),
            (2, "P2", "complaint", "B", "adjudication_complaint", None, "2023-01-01", "BENCH_ASSIGN", None, None),
            (2, "P2", "complaint", "C", None, None, None, None, None, None),
            (2, "P2", "challan", "X", "penalty_challan", None, None, None, None, None),
        ],
        columns=[
            "project_id",
            "project_name",
            "record_type",
            "reference",
            "category",
            "subtype",
            "filed_date",
            "status",
            "order_date",
            "order_url",
        ],
    )
    out = an.developer_complaints(legal)
    assert (out["total"], out["open"], out["closed"], out["unknown"], out["projects_with_complaints"]) == (
        3,
        1,
        1,
        1,
        2,
    )
    assert [r["reference"] for r in out["records"]] == ["B", "A", "C"]
    types = {t["type"]: t for t in out["by_type"]}
    assert types["Adjudication (Form B)"] == {
        "type": "Adjudication (Form B)",
        "open": 1,
        "closed": 0,
        "unknown": 0,
        "total": 1,
    }
    assert an.developer_complaints(legal.iloc[0:0])["records"] == []


def test_clean_converts_numpy_and_nan():
    assert an.clean({"a": np.int64(3), "b": [np.float64("nan"), 1.5]}) == {"a": 3, "b": [None, 1.5]}
