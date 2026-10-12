"""Project-level metrics (one row per project) computed from the normalized tables."""
from __future__ import annotations

import re
import sqlite3

import pandas as pd

from analytics import delay_analysis as dl
from analytics.inventory_analysis import sales_pct

RESOLVED_RX = re.compile(r"CLOS|DISPOS|DISMISS|WITHDRAW|REJECT|ORDER_PASS|DECIDED|COMPLETE|SETTLE", re.I)


def complaint_is_pending(status: str | None, order_type: str | None) -> bool | None:
    """True / False when the published status allows a decision, None when unknown."""
    if status and status != "order_published":
        return not bool(RESOLVED_RX.search(status))
    if status == "order_published":
        if order_type and order_type.lower().startswith("final"):
            return False
        if order_type and order_type.lower().startswith("interim"):
            return True
    return None


def load_projects(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query("""
        SELECT p.*, l.district, l.taluka, pd.developer_id, d.canonical_name AS developer_name
        FROM projects p
        LEFT JOIN project_locations l ON l.project_id = p.project_id
        LEFT JOIN project_developers pd ON pd.project_id = p.project_id AND pd.role = 'promoter'
        LEFT JOIN developers d ON d.developer_id = pd.developer_id""", conn)
    return df.drop_duplicates("project_id")


def progress_history(conn: sqlite3.Connection) -> dict[int, list[tuple[str | None, float]]]:
    out: dict[int, list] = {}
    for r in conn.execute("""SELECT project_id, period_date, seq, physical_progress_pct FROM project_progress
                             WHERE physical_progress_pct IS NOT NULL ORDER BY project_id, period_date, seq"""):
        out.setdefault(r["project_id"], []).append((r["period_date"], r["physical_progress_pct"]))
    return out


def complaint_counts(conn: sqlite3.Connection) -> pd.DataFrame:
    rows = []
    for r in conn.execute("SELECT project_id, category, status, order_type, penalty_flag FROM complaints "
                          "WHERE project_id IS NOT NULL"):
        pend = complaint_is_pending(r["status"], r["order_type"])
        rows.append({"project_id": r["project_id"], "complaints_total": 1,
                     "complaints_pending": int(pend is True), "complaints_resolved": int(pend is False),
                     "complaints_status_unknown": int(pend is None),
                     "complaint_penalty_orders": int((r["penalty_flag"] or "").upper() == "YES")})
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["project_id", "complaints_total", "complaints_pending", "complaints_resolved",
                                     "complaints_status_unknown", "complaint_penalty_orders"])
    return df.groupby("project_id", as_index=False).sum()


def action_counts(conn: sqlite3.Connection) -> pd.DataFrame:
    rows = []
    for r in conn.execute("SELECT project_id, action_category, penalty_flag, penalty_amount FROM regulatory_actions "
                          "WHERE project_id IS NOT NULL"):
        is_sm = r["action_category"] == "suo_motu_proceeding"
        is_ch = r["action_category"] == "penalty_challan"
        rows.append({"project_id": r["project_id"], "suo_motu_total": int(is_sm),
                     "suo_motu_penalty_orders": int(is_sm and (r["penalty_flag"] or "").upper() == "YES"),
                     "challans_total": int(is_ch), "challan_amount_total": (r["penalty_amount"] or 0) if is_ch else 0})
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["project_id", "suo_motu_total", "suo_motu_penalty_orders", "challans_total",
                                     "challan_amount_total"])
    return df.groupby("project_id", as_index=False).sum()


def qpr_defaults(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query("""SELECT project_id, SUM(defaulter_code IS NOT NULL) AS qpr_defaults,
                                       COUNT(*) AS qpr_slots, SUM(status='SUBMITTED') AS qpr_submitted
                                FROM project_quarters GROUP BY project_id""", conn)


def compute(conn: sqlite3.Connection, run_date: str, analysis_cfg: dict) -> pd.DataFrame:
    df = load_projects(conn)
    hist = progress_history(conn)
    for extra in (complaint_counts(conn), action_counts(conn), qpr_defaults(conn)):
        df = df.merge(extra, on="project_id", how="left")
    count_cols = ["complaints_total", "complaints_pending", "complaints_resolved", "complaints_status_unknown",
                  "complaint_penalty_orders", "suo_motu_total", "suo_motu_penalty_orders", "challans_total",
                  "challan_amount_total", "qpr_defaults"]
    # counts are only meaningful for projects whose detail was crawled; others stay NULL (unknown)
    crawled = df["project_id"].isin(
        {r[0] for r in conn.execute("SELECT DISTINCT CAST(entity_id AS INTEGER) FROM raw_responses "
                                    "WHERE entity_type='project' AND endpoint_key='applications'")})
    for c in count_cols:
        df[c] = df[c].where(df[c].notna(), 0)
        df.loc[~crawled, c] = None
    df["detail_crawled"] = crawled.astype(int)

    lookback = int(analysis_cfg["stalled_lookback_quarters"])
    min_delta = float(analysis_cfg["stalled_min_delta_pp"])
    no_qpr_months = float(analysis_cfg["stalled_no_qpr_months"])
    out = []
    for r in df.itertuples(index=False):
        completed = r.status_derived == "completed"
        h = hist.get(r.project_id, [])
        prev = h[-2][1] if len(h) >= 2 else None
        velocity = None
        if len(h) >= 2:
            window = h[-(lookback + 1):]
            velocity = round((window[-1][1] - window[0][1]) / (len(window) - 1), 2)
        last_qpr = r.last_qpr_submitted_at or r.latest_progress_date
        m_since = dl.months_between(last_qpr, run_date) if last_qpr else None
        stalled = None
        if r.detail_crawled:
            if completed or (r.latest_progress_pct is not None and r.latest_progress_pct >= 100):
                stalled = 0
            else:
                window = h[-(lookback + 1):]
                low_gain = len(window) == lookback + 1 and (window[-1][1] - window[0][1]) < min_delta
                silent = m_since is not None and m_since >= no_qpr_months
                stalled = int(bool(low_gain or silent)) if (h or m_since is not None) else None
        out.append({
            "project_id": r.project_id,
            "sales_pct": sales_pct(r.booked_units, r.unbooked_units),
            "unsold_pct": None if sales_pct(r.booked_units, r.unbooked_units) is None
            else round(100 - sales_pct(r.booked_units, r.unbooked_units), 2),
            "completion_pct": None if r.latest_progress_pct is None or pd.isna(r.latest_progress_pct)
            else min(float(r.latest_progress_pct), 100.0),
            "previous_progress_pct": prev,
            "progress_velocity_pp_per_qtr": velocity,
            "months_since_last_qpr": m_since,
            "stalled_flag": stalled,
            "days_beyond_original": dl.days_beyond(r.original_completion_date, completed, r.completion_filing_date, run_date),
            "days_beyond_current": dl.days_beyond(r.current_completion_date, completed, r.completion_filing_date, run_date),
            "total_extension_days": (lambda d: max(d, 0) if d is not None else None)(
                dl.days_between(r.original_completion_date, r.current_completion_date)),
            "timeline_elapsed_pct": dl.timeline_elapsed_pct(r.project_start_date, r.current_completion_date, run_date),
        })
    m = pd.DataFrame(out)
    return df.merge(m, on="project_id", how="left")
