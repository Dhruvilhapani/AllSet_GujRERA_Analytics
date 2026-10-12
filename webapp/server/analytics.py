"""Analytics layer (pandas / NumPy) — every developer and market metric the app shows.

Pure functions take DataFrames and return plain dicts/lists, so they are unit-tested in isolation and the
frontend never computes a business metric. Definitions are documented in webapp/METRICS.md; the short form is
in each docstring. Rules that apply everywhere:

* Missing is never zero: a value the source does not publish stays None and is excluded from averages.
* "Analysed" = the project's GujRERA detail has been crawled (detail_crawled = 1); counts over checked data
  (status, complaints, extensions) only use analysed projects.
* No interpolation and no estimation: a year without reports is absent from a trend.
"""

from __future__ import annotations

import json
import re
from datetime import date

import numpy as np
import pandas as pd

from . import db

STATUSES = ["Completed", "Ongoing", "Delayed", "Pending"]
MIN_UNITS_FOR_SALES_RANK = 20
DAYS_PER_MONTH = 30.4375
LEADERBOARD_SIZE = 15
LOCALITY_TOP = 15
SALES_BINS = [0, 20, 40, 60, 80, 100, np.inf]
SALES_LABELS = ["0-20%", "20-40%", "40-60%", "60-80%", "80-99%", "100%"]
REVISION_DROP_PP = 30.0
MARKET_MIN_PROJECTS_PER_YEAR = 10


# ---------------------------------------------------------------------------------------------------- helpers
def clean(value):
    """numpy / pandas scalars -> plain Python; NaN / NaT -> None (recursively)."""
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and (np.isnan(value) or np.isinf(value)):
        return None
    if value is pd.NaT:
        return None
    return value


def _round(x, digits=1):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), digits)


def _analysed(projects: pd.DataFrame) -> pd.DataFrame:
    return projects[projects["detail_crawled"] == 1]


def _year(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype("string").str.slice(0, 4), errors="coerce")


# ---------------------------------------------------------------------------------------------------- progress
def yearly_progress(
    progress: pd.DataFrame, projects: pd.DataFrame, as_of: date | None = None, min_projects: int = 1
) -> dict:
    """Average project progress per calendar year.

    Source: project_progress (quarterly physical progress as reported to GujRERA).
    Per project and year: the LAST progress reported in that year (by period, then report order), capped at
    100. Per year: arithmetic mean across the projects that reported in that year (`n_projects`), plus the
    units-weighted mean (projects without unit data are excluded from the weighted mean only).
    Points labelled after `as_of` are project-end filings labelled with a scheduled future period: excluded and
    counted in `excluded_future_points`. Years with fewer than `min_projects` reporting projects are omitted
    (listed in `omitted_years`); the market trend uses 10, developer trends keep every year.
    """
    as_of = as_of or date.today()
    method = (
        "Per project: last progress reported in each calendar year (capped at 100%). Per year: mean across "
        "projects that reported that year. Future-labelled project-end filings excluded."
    )
    df = progress.dropna(subset=["period_date", "progress_pct"]).copy()
    if df.empty:
        return {"points": [], "excluded_future_points": 0, "omitted_years": [], "method": method}
    future = df["period_date"] > as_of.isoformat()
    excluded = int(future.sum())
    df = df[~future]
    df["year"] = _year(df["period_date"])
    df["progress"] = np.clip(df["progress_pct"].astype(float), 0, 100)
    last = df.sort_values(["project_id", "period_date", "seq"]).groupby(["project_id", "year"]).tail(1)
    units = projects.set_index("project_id")["total_units"] if "total_units" in projects else pd.Series(dtype=float)
    last = last.join(units.rename("units"), on="project_id")
    points, omitted = [], []
    for year, g in last.groupby("year"):
        vals = g["progress"].to_numpy(dtype=float)
        if len(vals) < min_projects:
            omitted.append(int(year))
            continue
        w = g["units"].to_numpy(dtype=float)
        has_w = ~np.isnan(w) & (w > 0)
        points.append(
            {
                "year": int(year),
                "avg_progress": _round(np.mean(vals)),
                "weighted_progress": _round(np.average(vals[has_w], weights=w[has_w])) if has_w.any() else None,
                "n_projects": len(vals),
            }
        )
    if omitted:
        method += f" Years with fewer than {min_projects} reporting projects omitted: {', '.join(map(str, omitted))}."
    return {"points": points, "excluded_future_points": excluded, "omitted_years": omitted, "method": method}


def progress_revisions(progress: pd.DataFrame, drop_pp: float = REVISION_DROP_PP) -> pd.DataFrame:
    """QA: consecutive reports where progress fell by more than `drop_pp` points (published revisions)."""
    df = progress.dropna(subset=["progress_pct"]).sort_values(["project_id", "period_date", "seq"])
    rows = []
    for pid, g in df.groupby("project_id"):
        vals = g["progress_pct"].to_numpy(dtype=float)
        drops = np.diff(vals)
        for i in np.flatnonzero(drops < -drop_pp):
            rows.append(
                {"project_id": pid, "from_pct": vals[i], "to_pct": vals[i + 1], "period": g["period_date"].iloc[i + 1]}
            )
    return pd.DataFrame(rows, columns=["project_id", "from_pct", "to_pct", "period"])


# ---------------------------------------------------------------------------------------------------- status
def status_breakdown(projects: pd.DataFrame) -> dict:
    """Counts by app status over analysed projects (+ how many Ongoing projects are stalled, + not crawled)."""
    a = _analysed(projects)
    counts = a["status"].value_counts()
    ongoing = a[a["status"] == "Ongoing"]
    return {
        **{s.lower(): int(counts.get(s, 0)) for s in STATUSES},
        "stalled": int((ongoing["stalled"] == 1).sum()) if "stalled" in ongoing else 0,
        "not_crawled": int(len(projects) - len(a)),
        "analysed": len(a),
    }


def avg_latest_progress(projects: pd.DataFrame) -> float | None:
    """Mean of the latest reported progress of analysed projects that have a report."""
    vals = _analysed(projects)["progress_pct"].dropna().to_numpy(dtype=float)
    return _round(np.mean(np.clip(vals, 0, 100))) if len(vals) else None


# ---------------------------------------------------------------------------------------------------- sales
def sales_by_project(projects: pd.DataFrame) -> dict:
    """Sales % = sold ÷ (sold + unsold) per project with booking data, highest first, with distribution stats."""
    a = _analysed(projects).dropna(subset=["sold_units", "unsold_units"])
    a = a[(a["sold_units"] + a["unsold_units"]) > 0].copy()
    a["sales_pct"] = a["sold_units"] / (a["sold_units"] + a["unsold_units"]) * 100
    a = a.sort_values("sales_pct", ascending=False)
    rows = [
        {
            "project_id": int(r.project_id),
            "name": r.name,
            "sales_pct": _round(r.sales_pct),
            "sold": int(r.sold_units),
            "total": int(r.sold_units + r.unsold_units),
            "status": r.status,
        }
        for r in a.itertuples()
    ]
    vals = a["sales_pct"].to_numpy(dtype=float)
    return {
        "projects": rows,
        "median_sales_pct": _round(np.median(vals)) if len(vals) else None,
        "projects_with_data": len(vals),
        "projects_without_data": int(len(_analysed(projects)) - len(vals)),
    }


def best_worst(projects: pd.DataFrame) -> list[dict]:
    """Highest / lowest sales project (>= 20 units of booking data) and highest / lowest progress project
    (non-completed, with a report). Each entry states its metric; omitted when fewer than 2 projects qualify."""
    out = []
    a = _analysed(projects).copy()
    s = a.dropna(subset=["sold_units", "unsold_units"])
    s = s[(s["sold_units"] + s["unsold_units"]) >= MIN_UNITS_FOR_SALES_RANK].copy()
    s["sales_pct"] = s["sold_units"] / (s["sold_units"] + s["unsold_units"]) * 100
    if len(s) >= 2:
        hi, lo = s.loc[s["sales_pct"].idxmax()], s.loc[s["sales_pct"].idxmin()]
        out.append(
            {
                "label": "Highest sales",
                "metric": "Sales % (projects with 20+ units)",
                "project_id": int(hi.project_id),
                "name": hi["name"],
                "value": _round(hi.sales_pct),
            }
        )
        out.append(
            {
                "label": "Lowest sales",
                "metric": "Sales % (projects with 20+ units)",
                "project_id": int(lo.project_id),
                "name": lo["name"],
                "value": _round(lo.sales_pct),
            }
        )
    p = a[(a["status"] != "Completed") & a["progress_pct"].notna()]
    if len(p) >= 2:
        hi, lo = p.loc[p["progress_pct"].idxmax()], p.loc[p["progress_pct"].idxmin()]
        out.append(
            {
                "label": "Highest progress",
                "metric": "Latest construction progress (not completed)",
                "project_id": int(hi.project_id),
                "name": hi["name"],
                "value": _round(min(hi.progress_pct, 100)),
            }
        )
        out.append(
            {
                "label": "Lowest progress",
                "metric": "Latest construction progress (not completed)",
                "project_id": int(lo.project_id),
                "name": lo["name"],
                "value": _round(min(lo.progress_pct, 100)),
            }
        )
    return out


def sales_distribution(projects: pd.DataFrame) -> list[dict]:
    """Number of projects per Sales % band (projects with booking data only)."""
    vals = pd.Series([r["sales_pct"] for r in sales_by_project(projects)["projects"]], dtype=float)
    bands = pd.cut(vals, bins=SALES_BINS, labels=SALES_LABELS, right=False, include_lowest=True)
    # 100% is its own band (right=False puts exactly-100 into [100, inf)).
    counts = bands.value_counts().reindex(SALES_LABELS, fill_value=0)
    return [{"band": b, "projects": int(n)} for b, n in counts.items()]


# -------------------------------------------------------------------------------------------- extensions / delays
def extension_overview(projects: pd.DataFrame, extensions: pd.DataFrame) -> dict:
    """Projects with approved extensions: original vs latest approved completion and the total extension
    duration. Extensions are recorded GujRERA approvals; they are not counted as delays."""
    a = _analysed(projects)
    ext = extensions[extensions["status"] == "approved"] if len(extensions) else extensions
    counts = ext.groupby("project_id").size() if len(ext) else pd.Series(dtype=int)
    extended = a[a["project_id"].isin(counts.index)].copy()
    orig = pd.to_datetime(extended["original_completion_date"], errors="coerce")
    revised = pd.to_datetime(extended["current_completion_date"], errors="coerce")
    extended["months"] = ((revised - orig).dt.days / DAYS_PER_MONTH).round(1)
    extended["events"] = extended["project_id"].map(counts)
    extended = extended.sort_values(["months", "events"], ascending=False, na_position="last")
    rows = [
        {
            "project_id": int(r.project_id),
            "name": r.name,
            "extensions": int(r.events),
            "original_completion_date": r.original_completion_date,
            "revised_completion_date": r.current_completion_date,
            "extension_months": None if pd.isna(r.months) or r.months < 0 else float(r.months),
            "status": r.status,
        }
        for r in extended.itertuples()
    ]
    months = extended["months"].dropna().to_numpy(dtype=float)
    return {
        "projects_with_extensions": len(extended),
        "extension_events": int(counts.sum()) if len(counts) else 0,
        "avg_extension_months": _round(np.mean(months)) if len(months) else None,
        "max_extension_months": _round(np.max(months)) if len(months) else None,
        "reason_available": False,
        "projects": rows,
    }


def delayed_projects(projects: pd.DataFrame) -> list[dict]:
    """Projects past their latest approved completion date with no completion filing (status Delayed)."""
    d = _analysed(projects)
    d = d[d["status"] == "Delayed"].sort_values("days_beyond_current", ascending=False, na_position="last")
    return [
        {
            "project_id": int(r.project_id),
            "name": r.name,
            "original_completion_date": r.original_completion_date,
            "approved_completion_date": r.current_completion_date,
            "days_past_approved_date": None if pd.isna(r.days_beyond_current) else int(r.days_beyond_current),
            "extensions": None if pd.isna(r.extension_count) else int(r.extension_count),
            "progress_pct": _round(r.progress_pct),
        }
        for r in d.itertuples()
    ]


# ---------------------------------------------------------------------------------------------------- location / growth
def location_breakdown(projects: pd.DataFrame, top: int | None = None, include_unknown: bool = True) -> list[dict]:
    """Projects per GujRERA locality (moje) with average latest progress of analysed projects there.
    `include_unknown=False` drops projects without a published locality (used for market rankings)."""
    df = projects if include_unknown else projects.dropna(subset=["locality"])
    df = df.assign(area=df["locality"].fillna("Location not published"))
    g = (
        df.groupby("area")
        .agg(
            projects=("project_id", "size"),
            completed=("status", lambda s: int((s == "Completed").sum())),
            delayed=("status", lambda s: int((s == "Delayed").sum())),
            avg_progress=(
                "progress_pct",
                lambda s: _round(np.nanmean(np.clip(s.to_numpy(dtype=float), 0, 100))) if s.notna().any() else None,
            ),
        )
        .reset_index()
        .sort_values(["projects", "area"], ascending=[False, True])
    )
    if top:
        g = g.head(top)
    return [clean(r) for r in g.to_dict("records")]


def portfolio_growth(projects: pd.DataFrame) -> list[dict]:
    """Projects registered per year (GujRERA approval year), all registered projects."""
    years = _year(projects["registration_date"]).dropna().astype(int)
    counts = years.value_counts().sort_index()
    return [{"year": int(y), "projects": int(n)} for y, n in counts.items()]


def geographic_expansion(projects: pd.DataFrame) -> dict:
    """Cumulative number of distinct localities in which the developer had registered projects, by year."""
    df = projects.assign(year=_year(projects["registration_date"])).dropna(subset=["year"])
    known = df.dropna(subset=["locality"]).sort_values("year")
    seen: set = set()
    points = []
    for year, g in known.groupby("year"):
        seen |= set(g["locality"])
        points.append({"year": int(year), "areas": len(seen)})
    return {"points": points, "projects_without_locality": int(df["locality"].isna().sum())}


# ---------------------------------------------------------------------------------------------------- legal
LEGAL_LABEL = {
    "rera_complaint": "Complaint to Authority (Form A)",
    "adjudication_complaint": "Adjudication (Form B)",
    "review": "Review",
    "ratification": "Ratification",
    "conciliation": "Conciliation",
    "complaint_unspecified_form": "Complaint (form not stated)",
    "unknown": "Complaint (other)",
}


def legal_breakdown(legal: pd.DataFrame) -> dict:
    """Counts by record type and complaint category (factual records; no conclusions)."""
    if legal.empty:
        return {"complaints": 0, "suo_motu": 0, "challans": 0, "penalty_amount": 0.0, "categories": []}
    comp = legal[legal["record_type"] == "complaint"]
    cats = comp["category"].fillna("unknown").map(lambda c: LEGAL_LABEL.get(c, c)).value_counts()
    challans = legal[legal["record_type"] == "challan"]
    return {
        "complaints": len(comp),
        "suo_motu": int((legal["record_type"] == "suo_motu").sum()),
        "challans": len(challans),
        "penalty_amount": float(challans["penalty_amount"].fillna(0).sum()),
        "categories": [{"category": c, "records": int(n)} for c, n in cats.items()],
    }


# A published complaint status is "closed" when it records an end of the case. Same rule as the pipeline's
# complaint_is_pending (analytics/project_analysis.py), plus deleted complaints, which are not open.
CLOSED_STATUS_RX = re.compile(r"CLOS|DISPOS|DISMISS|WITHDRAW|REJECT|ORDER_PASS|DECIDED|COMPLETE|SETTLE|DELET", re.I)


def complaint_state(status: str | None, order_type: str | None) -> str:
    """'open' | 'closed' | 'unknown' from the published status (and order type when only an order is published)."""
    if not isinstance(status, str) or not status:  # None / NaN from pandas
        return "unknown"
    order_type = order_type if isinstance(order_type, str) else None
    if status == "order_published":
        kind = (order_type or "").lower()
        return "closed" if kind.startswith("final") else "open" if kind.startswith("interim") else "unknown"
    return "closed" if CLOSED_STATUS_RX.search(status) else "open"


def developer_complaints(legal: pd.DataFrame) -> dict:
    """Every complaint against the developer's projects, newest first, with open / closed counts by type.
    Complaints are attributed through the project they were filed against (GujRERA's own link)."""
    comp = legal[legal["record_type"] == "complaint"].copy()
    if comp.empty:
        return {
            "total": 0,
            "open": 0,
            "closed": 0,
            "unknown": 0,
            "projects_with_complaints": 0,
            "by_type": [],
            "records": [],
        }
    comp["state"] = [complaint_state(s, o) for s, o in zip(comp["status"], comp["subtype"], strict=True)]
    comp["type"] = comp["category"].fillna("unknown").map(lambda c: LEGAL_LABEL.get(c, c))
    comp = comp.sort_values(["filed_date", "reference"], ascending=False, na_position="last")
    by_type = (
        comp.groupby("type")["state"]
        .value_counts()
        .unstack(fill_value=0)
        .reindex(columns=["open", "closed", "unknown"], fill_value=0)
    )
    by_type["total"] = by_type.sum(axis=1)
    by_type = by_type.sort_values("total", ascending=False)
    counts = comp["state"].value_counts()
    records = [
        {
            "reference": r.reference,
            "project_id": r.project_id,
            "project_name": r.project_name,
            "type": r.type,
            "filed_date": r.filed_date,
            "status": r.status,
            "state": r.state,
            "order_date": r.order_date,
            "order_type": r.subtype,
            "order_url": r.order_url,
        }
        for r in comp.itertuples()
    ]
    return {
        "total": len(comp),
        "open": int(counts.get("open", 0)),
        "closed": int(counts.get("closed", 0)),
        "unknown": int(counts.get("unknown", 0)),
        "projects_with_complaints": int(comp["project_id"].nunique()),
        "by_type": [{"type": t, **{k: int(v) for k, v in row.items()}} for t, row in by_type.iterrows()],
        "records": clean(records),
    }


# ---------------------------------------------------------------------------------------------------- loaders
_PROJECT_COLS = (
    "project_id, developer_id, name, rera_number, category, taluka, locality, status, status_detail, stalled, "
    "registration_date, original_completion_date, current_completion_date, completion_filing_date, extension_count, "
    "days_beyond_current, progress_pct, progress_quarter, total_units, sold_units, unsold_units, sales_pct, "
    "complaints_total, suo_motu, challans, detail_crawled"
)
_NUMERIC = [
    "stalled",
    "extension_count",
    "days_beyond_current",
    "progress_pct",
    "total_units",
    "sold_units",
    "unsold_units",
    "sales_pct",
    "complaints_total",
    "suo_motu",
    "challans",
    "detail_crawled",
]


def _frame(rows: list[dict], columns: list[str] | None = None) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=columns) if columns else pd.DataFrame(rows)
    for c in _NUMERIC:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _project_frame(where: str = "1 = 1", params: dict | None = None) -> pd.DataFrame:
    rows = db.query(f"SELECT {_PROJECT_COLS} FROM projects WHERE {where}", params)  # noqa: S608 -- constant where
    return _frame(rows, [c.strip() for c in _PROJECT_COLS.split(",")])


def _progress_frame(where: str, params: dict | None = None) -> pd.DataFrame:
    # `where` is a constant join/filter written in this module; values are bound.
    sql = f"SELECT pp.project_id, pp.seq, pp.period_date, pp.progress_pct FROM project_progress pp {where}"  # noqa: S608
    rows = db.query(sql, params)
    return _frame(rows, ["project_id", "seq", "period_date", "progress_pct"])


# ---------------------------------------------------------------------------------------------------- reports
def developer_report(developer_id: int, as_of: date | None = None) -> dict | None:
    """Everything the developer page shows, in page order."""
    rows = db.query("SELECT * FROM developers WHERE developer_id = :id", {"id": developer_id})
    if not rows:
        return None
    dev = rows[0]
    p = {"id": developer_id}
    projects = _project_frame("developer_id = :id", p)
    progress = _progress_frame("JOIN projects pr ON pr.project_id = pp.project_id WHERE pr.developer_id = :id", p)
    extensions = pd.DataFrame(
        db.query(
            "SELECT e.* FROM project_extensions e JOIN projects pr ON pr.project_id = e.project_id "
            "WHERE pr.developer_id = :id",
            p,
        ),
        columns=[
            "project_id",
            "seq",
            "extension_reg_no",
            "submitted_on",
            "approved_on",
            "status",
            "previous_completion_date",
            "extended_completion_date",
            "extension_days",
        ],
    )
    legal_rows = db.query(
        "SELECT l.project_id, pr.name AS project_name, l.record_type, l.reference, l.category, l.subtype, "
        "l.filed_date, l.order_date, l.status, l.outcome, l.penalty_amount, l.order_url "
        "FROM project_legal l JOIN projects pr ON pr.project_id = l.project_id WHERE pr.developer_id = :id "
        "ORDER BY COALESCE(l.order_date, l.filed_date) DESC",
        p,
    )
    events = db.query(
        "SELECT e.project_id, pr.name AS project_name, e.event_date, e.event_type, e.title, e.detail, e.reference "
        "FROM project_events e JOIN projects pr ON pr.project_id = e.project_id WHERE pr.developer_id = :id "
        "ORDER BY e.event_date DESC",
        p,
    )
    legal_df = pd.DataFrame(
        legal_rows,
        columns=[
            "project_id",
            "project_name",
            "record_type",
            "reference",
            "category",
            "subtype",
            "filed_date",
            "order_date",
            "status",
            "outcome",
            "penalty_amount",
            "order_url",
        ],
    )
    status = status_breakdown(projects)
    complaints = developer_complaints(legal_df)
    analysed = bool(dev["projects_analyzed"])
    kpis = {
        "projects": int(dev["projects_total"]),
        "analysed": int(dev["projects_analyzed"]),
        "completed": status["completed"] if analysed else None,
        "ongoing": status["ongoing"] if analysed else None,
        "delayed": status["delayed"] if analysed else None,
        "avg_progress": avg_latest_progress(projects),
        "complaints": complaints["total"] if analysed else None,
        "open_complaints": complaints["open"] if analysed else None,
        "regulatory_actions": None
        if dev["suo_motu"] is None
        else int(dev["suo_motu"] or 0) + int(dev["challans"] or 0),
    }
    project_rows = [
        clean(
            {
                "project_id": r.project_id,
                "name": r.name,
                "rera_number": r.rera_number,
                "location": r.locality or r.taluka,
                "category": r.category,
                "status": r.status,
                "stalled": r.stalled,
                "units": r.total_units,
                "sold": r.sold_units,
                "remaining": r.unsold_units,
                "sales_pct": r.sales_pct,
                "progress_pct": r.progress_pct,
                "registration_date": r.registration_date,
                "completion_date": r.current_completion_date,
                "detail_crawled": r.detail_crawled,
            }
        )
        for r in projects.sort_values("registration_date", ascending=False).itertuples()
    ]
    report = {
        "developer": {
            k: dev[k]
            for k in (
                "developer_id",
                "name",
                "entity_type",
                "merge_basis",
                "declared_group_name",
                "first_registration_year",
                "projects_total",
                "projects_analyzed",
                "residential_projects",
                "mixed_projects",
            )
        },
        "aliases": _json(dev["aliases"]),
        "similar_names": _json(dev["similar_names"]),
        "promoter_account_ids": _json(dev["promoter_account_ids"]),
        "kpis": kpis,
        "yearly_progress": yearly_progress(progress, projects, as_of),
        "projects": project_rows,
        "sales": sales_by_project(projects),
        "best_worst": best_worst(projects),
        "construction": status,
        "extensions": extension_overview(projects, extensions),
        "delays": delayed_projects(projects),
        "complaints": complaints,
        "regulatory": {
            **{k: v for k, v in legal_breakdown(legal_df).items() if k in ("suo_motu", "challans", "penalty_amount")},
            "records": [r for r in legal_rows if r["record_type"] != "complaint"],
        },
        "events": events,
        "locations": location_breakdown(projects),
        "growth": {"registrations": portfolio_growth(projects), "expansion": geographic_expansion(projects)},
    }
    return clean(report)


def market_report(as_of: date | None = None) -> dict:
    """Dashboard: market-level metrics over the whole district dataset."""
    projects = _project_frame()
    progress = _progress_frame("")
    status = status_breakdown(projects)
    devs = pd.DataFrame(
        db.query(
            "SELECT developer_id, name, projects_total, projects_analyzed, completed, delayed, avg_progress_pct, "
            "completion_rate_pct, total_extensions, complaints_total FROM developers"
        )
    )
    leaderboard = []
    if len(devs):
        top = devs.sort_values(["projects_total", "projects_analyzed"], ascending=False).head(LEADERBOARD_SIZE)
        leaderboard = [clean(r) for r in top.to_dict("records")]
    legal = pd.DataFrame(
        db.query("SELECT record_type, category, penalty_amount FROM project_legal"),
        columns=["record_type", "category", "penalty_amount"],
    )
    a = _analysed(projects)
    reg = projects.assign(year=_year(projects["registration_date"])).dropna(subset=["year"])
    registrations = [
        {
            "year": int(y),
            "residential": int((g["category"] == "residential").sum()),
            "mixed": int((g["category"] == "mixed").sum()),
        }
        for y, g in reg.groupby("year")
    ]
    return clean(
        {
            "kpis": {
                "projects": len(projects),
                "analysed": len(a),
                "developers": int(a["developer_id"].dropna().nunique()),
                "completed": status["completed"],
                "ongoing": status["ongoing"],
                "delayed": status["delayed"],
                "pending": status["pending"],
                "avg_progress": avg_latest_progress(projects),
                "complaints": int(a["complaints_total"].fillna(0).sum()),
            },
            "yearly_progress": yearly_progress(progress, projects, as_of, MARKET_MIN_PROJECTS_PER_YEAR),
            "registrations_by_year": registrations,
            "status": status,
            "sales_distribution": sales_distribution(projects),
            "leaderboard": leaderboard,
            "localities": location_breakdown(a, top=LOCALITY_TOP, include_unknown=False),
            "projects_without_locality": int(a["locality"].isna().sum()),
            "legal": legal_breakdown(legal),
        }
    )


def _json(raw):
    if raw is None:
        return []
    return json.loads(raw) if isinstance(raw, str) else raw
