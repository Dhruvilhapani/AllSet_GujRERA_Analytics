"""Interactive district dashboard (single self-contained HTML file with the data embedded).

`python cli.py dashboard --district Ahmedabad` -> exports/<district>/<district>_dashboard.html
Every number shown is computed here from the normalized tables; nothing is estimated.
"""
from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

import pandas as pd

from analytics import area_report as ar

TEMPLATE = Path(__file__).resolve().parents[1] / "exports" / "templates" / "dashboard.html"


def _v(x):
    if x is None:
        return None
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    if hasattr(x, "item"):
        x = x.item()
        if isinstance(x, float) and math.isnan(x):
            return None
    return x


def _records(df: pd.DataFrame, cols: list[str]) -> list[list]:
    cols = [c for c in cols if c in df.columns]
    return [[_v(v) for v in row] for row in df[cols].itertuples(index=False)]


def _year(s):
    return pd.to_datetime(s, errors="coerce").dt.year


def build_data(conn: sqlite3.Connection, settings: dict, district: str) -> dict:
    p, res = ar.scope_projects(conn, settings, district)
    entity, devs, review = ar.resolve_entities(conn, set(p["developer_id"].dropna().astype(int)))
    p["entity_id"] = p["developer_id"].map(lambda x: entity.get(int(x)) if pd.notna(x) else None)
    d = ar.developer_table(p, devs, conn)
    p = p.merge(d[["entity_id", "developer"]], on="entity_id", how="left")
    rk = ar.rankings(d)
    outl = ar.project_outliers(p)
    geo_l = ar.geography(p, "locality")
    geo_t = ar.geography(p, "taluka")
    q = ar.data_quality(conn, p, d, review, res)
    cr = p[p["detail_crawled"] == 1]
    inv = cr[cr["booked_units"].notna() & cr["unbooked_units"].notna()]
    sold, unsold = int(inv["booked_units"].sum()), int(inv["unbooked_units"].sum())
    ids = ",".join(str(int(x)) for x in cr["project_id"]) or "0"

    # ---- trends (years)
    reg = p.assign(y=pd.to_datetime(p["registration_date"], errors="coerce").dt.year)
    reg_year = reg.groupby(["y", "project_category"]).size().unstack(fill_value=0)
    years = [int(y) for y in reg_year.index if not pd.isna(y)]
    trend_reg = {"years": years,
                 "residential": [int(reg_year.loc[y].get("residential", 0)) for y in years],
                 "mixed": [int(reg_year.loc[y].get("mixed", 0)) for y in years]}
    ext = pd.read_sql_query(f"SELECT approved_on FROM project_extensions WHERE status='approved' AND project_id IN ({ids})", conn)
    comp = pd.read_sql_query(f"SELECT COALESCE(filed_date, order_date) d FROM complaints WHERE project_id IN ({ids})", conn)
    act = pd.read_sql_query(f"SELECT initiated_date d, action_category c FROM regulatory_actions WHERE project_id IN ({ids})", conn)
    done = cr[cr["status_derived"] == "completed"]
    span = list(range(2017, pd.Timestamp.today().year + 1))

    def per_year(series):
        c = _year(series).value_counts()
        return [int(c.get(y, 0)) for y in span]

    trend_events = {"years": span,
                    "completion_filings": per_year(done["completion_filing_date"]),
                    "extensions_approved": per_year(ext["approved_on"]),
                    "complaints": per_year(comp["d"]),
                    "suo_motu": per_year(act.loc[act["c"] == "suo_motu_proceeding", "d"])}
    due = {"years": span,
           "original_due": per_year(cr["original_completion_date"]),
           "current_due": per_year(cr["current_completion_date"])}

    # ---- distributions
    cats = cr["execution_category"].value_counts()
    status_mix = [[k, int(v)] for k, v in cats.items()]
    bins = [(-0.1, 25, "0–25%"), (25, 50, "25–50%"), (50, 75, "50–75%"), (75, 99.999, "75–99%"), (99.999, 1000, "100%")]
    prog = cr["completion_pct"].dropna()
    prog_hist = [[lab, int(((prog > lo) & (prog <= hi)).sum())] for lo, hi, lab in bins]
    sp = inv["sales_pct"].dropna()
    sbins = [(-0.1, 20, "0–20%"), (20, 40, "20–40%"), (40, 60, "40–60%"), (60, 80, "60–80%"), (80, 99.999, "80–99%"),
             (99.999, 1000, "100%")]
    sales_hist = [[lab, int(((sp > lo) & (sp <= hi)).sum())] for lo, hi, lab in sbins]
    dl = cr.loc[cr["status_derived"] != "completed", "days_beyond_current"].dropna()
    dbins = [(-1, 0, "On time"), (0, 180, "< 6 months"), (180, 365, "6–12 months"), (365, 730, "1–2 years"),
             (730, 100000, "> 2 years")]
    delay_hist = [[lab, int(((dl > lo) & (dl <= hi)).sum())] for lo, hi, lab in dbins]
    ec = cr["extension_count"].fillna(0).astype(int)
    ext_hist = [["0", int((ec == 0).sum())], ["1", int((ec == 1).sum())], ["2", int((ec == 2).sum())], ["3+", int((ec >= 3).sum())]]
    ccat = pd.read_sql_query(f"SELECT category, COUNT(*) n FROM complaints WHERE project_id IN ({ids}) GROUP BY 1 ORDER BY 2 DESC", conn)
    asub = pd.read_sql_query(f"""SELECT COALESCE(action_subtype_label, action_category) k, COUNT(*) n FROM regulatory_actions
                                 WHERE project_id IN ({ids}) GROUP BY 1 ORDER BY 2 DESC""", conn)

    # ---- developers
    dev_cols = ["developer", "projects", "projects_analyzed", "residential_projects", "mixed_projects", "completed", "ongoing",
                "overdue", "delayed", "delay_rate_pct", "extended", "total_extensions", "units", "sold", "unsold", "sales_pct",
                "avg_progress_pct", "completion_rate_pct", "complaints_total", "complaints_pending", "suo_motu_proceedings",
                "challans", "regulatory_actions", "stalled", "entity_id", "promoter_account_ids"]
    top = lambda df, col, n=15: _records(df[df[col].fillna(0) > 0].sort_values([col, "projects"], ascending=False).head(n),  # noqa: E731
                                         ["developer", col, "projects", "projects_analyzed"])
    elig = d[d["projects_analyzed"] >= ar.MIN_PROJECTS_PCT]
    units_top = d.sort_values("units", ascending=False).head(15)
    data = {
        "district": district, "run_date": res["run_date"], "quality": q,
        "kpi": {"projects": len(p), "residential": int((p["project_category"] == "residential").sum()),
                "mixed": int((p["project_category"] == "mixed").sum()), "analysed": len(cr),
                "developers": int(cr["entity_id"].nunique()),
                "delayed": int(cr["delayed"].fillna(0).sum()),
                "units": int(cr["total_units"].dropna().sum()), "sold": sold, "unsold": unsold,
                "sales_pct": ar._pct(sold, sold + unsold),
                "sales_pct_active": (lambda a: ar._pct(int(a["booked_units"].sum()),
                                                       int(a["booked_units"].sum() + a["unbooked_units"].sum())))(
                    inv[inv["status_derived"] != "completed"]),
                "completed": int(cats.get("Completed", 0)), "overdue": int(cats.get("Overdue", 0)),
                "extended_projects": int((ec > 0).sum()),
                "avg_progress": ar._r(cr["completion_pct"].mean()),
                "complaints": int(cr["complaints_total"].fillna(0).sum()),
                "suo_motu": int(cr["suo_motu_total"].fillna(0).sum()),
                "challans": int(cr["challans_total"].fillna(0).sum()),
                "challan_amount": float(cr["challan_amount_total"].fillna(0).sum())},
        "trend_reg": trend_reg, "trend_events": trend_events, "due": due,
        "status_mix": status_mix, "prog_hist": prog_hist, "sales_hist": sales_hist, "delay_hist": delay_hist,
        "ext_hist": ext_hist, "complaint_cats": _records(ccat, ["category", "n"]), "action_types": _records(asub, ["k", "n"]),
        "top": {
            "projects": top(d, "projects"), "completed": top(d, "completed"), "overdue": top(d, "overdue"),
            "extensions": top(d, "total_extensions"), "complaints": top(d, "complaints_total"),
            "regulatory": top(d, "regulatory_actions"), "unsold": top(d[d["unsold"].notna()], "unsold"),
            "units_sold_unsold": _records(units_top, ["developer", "sold", "unsold", "units", "projects"]),
            "completion_rate": _records(elig.sort_values(["completion_rate_pct", "projects"], ascending=False).head(15),
                                        ["developer", "completion_rate_pct", "projects", "projects_analyzed"]),
            "sales_pct": _records(rk["C_sales_performance"].head(15), ["developer", "sales_pct", "projects", "projects_with_unit_data"]),
            "delay_exposure": _records(rk["D_delay_exposure"].head(15), ["developer", "delay_exposure_index", "projects", "projects_analyzed"]),
            "regulatory_density": _records(rk["E_regulatory_exposure_density"].head(15),
                                           ["developer", "regulatory_items_per_project", "projects", "projects_analyzed"]),
        },
        "developers": {"cols": [c for c in dev_cols if c in d.columns],
                       "rows": _records(d.sort_values(["projects", "units"], ascending=False), dev_cols)},
        "localities": {"cols": ["locality", "projects", "units", "sold", "unsold", "unsold_pct", "delay_rate_pct",
                                "avg_progress_pct", "overdue", "developers", "top_developers"],
                       "rows": _records(geo_l, ["locality", "projects", "units", "sold", "unsold", "unsold_pct",
                                                "delay_rate_pct", "avg_progress_pct", "overdue", "developers", "top_developers"])},
        "talukas": _records(geo_t, ["taluka", "projects", "units", "sold", "unsold", "unsold_pct", "delay_rate_pct",
                                    "avg_progress_pct"]),
        "scatter": _records(inv[inv["completion_pct"].notna()],
                            ["project_name", "developer", "completion_pct", "sales_pct", "booked_units", "unbooked_units",
                             "status_derived"]),
        "outliers": _records(outl, ["pattern", "project_name", "reg_no", "developer", "locality", "why_flagged"]),
        "projects": {"cols": ["project_id", "reg_no_current", "project_name", "project_category", "developer", "locality",
                              "taluka", "execution_category", "registration_date", "original_completion_date",
                              "current_completion_date", "extension_count", "days_beyond_current", "completion_pct",
                              "total_units", "booked_units", "unbooked_units", "sales_pct", "complaints_total",
                              "suo_motu_total", "challans_total"],
                     "rows": _records(p.sort_values("project_id"),
                                      ["project_id", "reg_no_current", "project_name", "project_category", "developer",
                                       "locality", "taluka", "execution_category", "registration_date",
                                       "original_completion_date", "current_completion_date", "extension_count",
                                       "days_beyond_current", "completion_pct", "total_units", "booked_units",
                                       "unbooked_units", "sales_pct", "complaints_total", "suo_motu_total",
                                       "challans_total"])},
    }
    return data


def build_dashboard(conn: sqlite3.Connection, settings: dict, district: str, out: str | None = None) -> str:
    data = build_data(conn, settings, district)
    html = TEMPLATE.read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False, default=str, separators=(",", ":")).replace("</", "<\\/")
    html = html.replace("/*__DATA__*/null", payload).replace("__DISTRICT__", district)
    path = Path(out) if out else Path(settings["exports_dir"]) / district.lower() / f"{district.lower()}_dashboard.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    # page body only (the artifact host adds the document skeleton) ...
    path.with_name(path.stem + "_page.html").write_text(html, encoding="utf-8")
    # ... and a standalone file that opens directly in any browser
    path.write_text('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                    '<meta name="viewport" content="width=device-width, initial-scale=1">'
                    '<style>body{margin:0}</style></head><body>' + html + "</body></html>", encoding="utf-8")
    return str(path)
