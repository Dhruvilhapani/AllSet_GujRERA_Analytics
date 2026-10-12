"""`cli.py analyze`: compute project + developer metrics and the analytical risk score; persist them."""
from __future__ import annotations

import json
import sqlite3
from datetime import date

import pandas as pd

from analytics import developer_analysis, project_analysis, risk_analysis
from database.db import utcnow
from settings import risk_config


def build(conn: sqlite3.Connection, settings: dict, run_date: str | None = None) -> dict[str, pd.DataFrame]:
    run_date = run_date or date.today().isoformat()
    cfg = risk_config()
    proj = project_analysis.compute(conn, run_date, settings["analysis"])
    risk = risk_analysis.score_projects(proj, cfg)
    proj = proj.merge(risk, on="project_id", how="left")
    devs = developer_analysis.compute(proj)
    dev_risk = risk_analysis.score_developers(proj, cfg)
    devs = devs.merge(dev_risk, on="developer_id", how="left") if len(devs) else devs
    names = pd.read_sql_query("SELECT developer_id, canonical_name, entity_type, district AS developer_district, "
                              "declared_group_name FROM developers", conn)
    devs = names.merge(devs, on="developer_id", how="right") if len(devs) else devs
    groups = pd.read_sql_query("SELECT * FROM developer_groups", conn)
    dev_groups = developer_analysis.compute_groups(proj, groups) if len(groups) else pd.DataFrame()
    return {"projects": proj, "developers": devs, "developer_groups": dev_groups, "run_date": run_date,
            "risk_label": cfg["label"]}


def run_analytics(conn: sqlite3.Connection, settings: dict) -> dict:
    res = build(conn, settings)
    proj, devs = res["projects"], res["developers"]
    now = utcnow()
    conn.execute("BEGIN")
    conn.execute("DELETE FROM project_metrics")
    cols = ["sales_pct", "unsold_pct", "completion_pct", "previous_progress_pct", "progress_velocity_pp_per_qtr",
            "months_since_last_qpr", "stalled_flag", "days_beyond_original", "days_beyond_current",
            "total_extension_days", "complaints_total", "complaints_pending", "suo_motu_total", "qpr_defaults",
            "risk_score"]
    for r in proj.to_dict("records"):
        comps = {k[5:]: r.get(k) for k in r if k.startswith("risk_") and k not in ("risk_score", "risk_flags")}
        penalties = sum(int(r.get(k) or 0) for k in ("suo_motu_penalty_orders", "complaint_penalty_orders", "challans_total")
                        if r.get(k) == r.get(k))
        conn.execute(
            f"""INSERT INTO project_metrics(project_id, {','.join(cols)}, penalties_total, penalty_amount_total,
                risk_components, risk_flags, computed_at) VALUES (?,{','.join('?' * len(cols))},?,?,?,?,?)""",
            [r["project_id"], *[_clean(r.get(c)) for c in cols], penalties if r.get("detail_crawled") else None,
             _clean(r.get("challan_amount_total")), json.dumps(_jsonable(comps)), json.dumps(r.get("risk_flags") or []),
             now])
    conn.execute("DELETE FROM developer_metrics")
    for r in devs.to_dict("records"):
        comps = {k[5:]: r.get(k) for k in r if k.startswith("risk_") and k not in ("risk_score",)}
        conn.execute("INSERT INTO developer_metrics(developer_id, metrics, risk_score, risk_components, risk_flags, "
                     "computed_at) VALUES (?,?,?,?,?,?)",
                     (int(r["developer_id"]), json.dumps(_jsonable(r), default=str), _clean(r.get("risk_score")),
                      json.dumps(_jsonable(comps)), json.dumps(_jsonable(r.get("flag_counts") or {})), now))
    conn.execute("COMMIT")
    return {"projects_scored": int(proj["risk_score"].notna().sum()), "projects": len(proj),
            "developers": len(devs), "developer_groups_pan_confirmed": len(res["developer_groups"]),
            "run_date": res["run_date"], "label": res["risk_label"]}


def _clean(v):
    if v is None:
        return None
    if isinstance(v, float) and v != v:
        return None
    if hasattr(v, "item"):
        return v.item()
    return v


def _jsonable(d):
    if isinstance(d, dict):
        return {k: _jsonable(v) for k, v in d.items()}
    if isinstance(d, list):
        return [_jsonable(x) for x in d]
    return _clean(d)
