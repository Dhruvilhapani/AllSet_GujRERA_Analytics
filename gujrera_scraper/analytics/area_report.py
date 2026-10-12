"""District-level builder intelligence (built for Ahmedabad; any GujRERA district works).

`python cli.py area --district Ahmedabad` -> exports/<district>/ (CSVs, Excel, report .md/.html, evidence).

Conventions (see docs/data_dictionary.md):
  * "—" / empty = data unavailable from the source. 0 = reported or checked zero.
  * Complaint / action counts exist only for projects whose project application list was fetched (checked).
  * Sold = booked, unsold = unbooked (GujRERA block booking chart, QPR-fed). Nothing is estimated.
  * Developer = GujRERA promoter account, merged into one entity only when accounts share the same PAN (salted
    hash) or company registration number. Name similarity is flagged for review, never merged.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

from analytics.run import build
from exports.report import _table, render_html

MIN_PROJECTS_PCT = 3          # minimum projects for percentage-based developer rankings
MIN_UNITS_SALES = 100         # minimum units with booking data for sales-% rankings
NEAR_COMPLETION = 75.0
EARLY_STAGE = 25.0
HIGH_PROGRESS_NOT_DONE = 90.0

# evidence: metric -> (endpoint key whose stored response supports it, human source)
EVIDENCE_FIELDS = {
    "registration_date": ("list", "GujRERA registered-project list"),
    "original_completion_date": ("list", "GujRERA registered-project list (projOrgFDate)"),
    "current_completion_date": ("details", "GujRERA project details (completionDate)"),
    "extension_count": ("applications", "GujRERA project applications (Extension rows) + reg. no."),
    "latest_progress_pct": ("qpr_progress", "GujRERA QPR progress history"),
    "completion_filing_status": ("qe_filing", "GujRERA Q-E project-end filing"),
    "total_units": ("unit_summary", "GujRERA unit summary"),
    "booked_units": ("block_chart", "GujRERA block booking chart"),
    "unbooked_units": ("block_chart", "GujRERA block booking chart"),
    "complaints_total": ("applications", "GujRERA project applications + published judgement lists"),
    "suo_motu_total": ("applications", "GujRERA project applications + published judgement lists"),
    "challans_total": ("applications", "GujRERA project applications (Challan rows)"),
    "months_since_last_qpr": ("qpr_quarters", "GujRERA QPR filing calendar"),
}


# ---------------------------------------------------------------------------------------------- data assembly
def scope_projects(conn: sqlite3.Connection, settings: dict, district: str) -> tuple[pd.DataFrame, dict]:
    res = build(conn, settings)
    p = res["projects"]
    p = p[p["district"].fillna("").str.strip().str.lower() == district.strip().lower()].copy()
    loc = pd.read_sql_query("SELECT project_id, village_moje, pincode, latitude, longitude, planning_authority, "
                            "tp_number, full_address FROM project_locations", conn)
    p = p.merge(loc, on="project_id", how="left")
    p["locality"] = p["village_moje"].map(_clean_place)
    p["locality_source"] = p["locality"].map(lambda v: "GujRERA moje field" if v else None)
    fill_locality_from_address(p)
    p["taluka"] = p["taluka"].map(_clean_place)
    p["delayed"] = p["days_beyond_original"].map(lambda d: None if pd.isna(d) else int(d > 0))
    p["delay_months"] = (p["days_beyond_current"] / 30.4375).round(1)
    p["execution_category"] = p.apply(execution_category, axis=1)
    return p, res


def fill_locality_from_address(p: pd.DataFrame) -> None:
    """Where GujRERA's moje field is blank, use a locality name only if the official project address contains
    exactly one moje name that GujRERA itself publishes for >= 2 other projects in the district."""
    known = p["locality"].value_counts()
    names = [n for n, c in known.items() if c >= 2 and len(n) >= 4]
    pats = {n: re.compile(rf"\b{re.escape(n.lower())}\b") for n in names}
    for i, r in p[p["locality"].isna() & p["full_address"].notna()].iterrows():
        addr = str(r["full_address"]).lower()
        hits = [n for n, rx in pats.items() if rx.search(addr)]
        hits = [h for h in hits if not any(h != o and h.lower() in o.lower() for o in hits)]  # drop substrings
        if len(hits) == 1:
            p.at[i, "locality"] = hits[0]
            p.at[i, "locality_source"] = "official address text matched to a GujRERA moje name"


def _clean_place(s):
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    s = re.sub(r"\s+", " ", str(s)).strip(" ,.-")
    return s.title() if s else None


def execution_category(r) -> str:
    """Primary category (first match wins). Stalled only when the QPR data supports it (stalled_flag = 1)."""
    if not r.get("detail_crawled"):
        return "Unknown (detail not crawled)"
    st = r.get("status_derived")
    cp = r.get("completion_pct")
    cp = None if cp is None or pd.isna(cp) else float(cp)
    if st == "completed":
        return "Completed"
    if st == "cancelled_revoked":
        return "Cancelled/Revoked"
    if st == "overdue":
        return "Overdue"
    if r.get("stalled_flag") == 1:
        return "Stalled / No Recent Progress"
    if cp is None:
        return "Unknown (no progress data)"
    if cp >= NEAR_COMPLETION:
        return "Near Completion"
    if cp >= EARLY_STAGE:
        return "Under Construction"
    return "Early Stage"


def resolve_entities(conn: sqlite3.Connection, dev_ids: set[int]) -> tuple[dict[int, str], pd.DataFrame, pd.DataFrame]:
    """Union promoter accounts sharing PAN hash or company registration no.; flag similar names for review."""
    devs = pd.read_sql_query("SELECT developer_id, canonical_name, name_normalized, entity_type, pan_hash, "
                             "company_reg_number, declared_group_name FROM developers", conn)
    devs = devs[devs["developer_id"].isin(dev_ids)].copy()
    parent = {d: d for d in devs["developer_id"]}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    from parser.promoter_parser import valid_entity_reg_no
    devs["cin_llpin"] = devs["company_reg_number"].map(valid_entity_reg_no)
    basis: dict[int, set] = defaultdict(set)
    for col in ("pan_hash", "cin_llpin"):
        for _, g in devs[devs[col].notna()].groupby(col):
            ids = list(g["developer_id"])
            for i in ids[1:]:
                parent[find(i)] = find(ids[0])
            if len(ids) > 1:
                for i in ids:
                    basis[i].add(col)
    entity = {d: f"E{find(d)}" for d in devs["developer_id"]}
    devs["entity_id"] = devs["developer_id"].map(entity)
    devs["merge_basis"] = devs["developer_id"].map(lambda d: ",".join(sorted(basis[d])) or None)
    # review flags: identical normalised name or high fuzzy similarity across *different* entities
    review = []
    names = devs[["developer_id", "entity_id", "canonical_name", "name_normalized"]].dropna(subset=["name_normalized"])
    for nn, g in names.groupby("name_normalized"):
        if g["entity_id"].nunique() > 1:
            ids = g.to_dict("records")
            for a in ids:
                for b in ids:
                    if a["developer_id"] < b["developer_id"] and a["entity_id"] != b["entity_id"]:
                        review.append({"developer_id_1": a["developer_id"], "name_1": a["canonical_name"],
                                       "developer_id_2": b["developer_id"], "name_2": b["canonical_name"],
                                       "similarity": 100.0, "reason": "identical normalised name, different PAN/CIN"})
    try:
        from rapidfuzz import fuzz, process
        lst = names.to_dict("records")
        if 1 < len(lst) <= 20000:
            m = process.cdist([x["name_normalized"] for x in lst], [x["name_normalized"] for x in lst],
                              scorer=fuzz.token_sort_ratio, score_cutoff=92, workers=-1)
            seen = {(r["developer_id_1"], r["developer_id_2"]) for r in review}
            for i, j in zip(*m.nonzero()):
                a, b = lst[i], lst[j]
                if i < j and a["entity_id"] != b["entity_id"] and a["name_normalized"] != b["name_normalized"] \
                        and (a["developer_id"], b["developer_id"]) not in seen:
                    review.append({"developer_id_1": a["developer_id"], "name_1": a["canonical_name"],
                                   "developer_id_2": b["developer_id"], "name_2": b["canonical_name"],
                                   "similarity": float(m[i, j]), "reason": "similar name (token-sort ratio)"})
    except ImportError:
        pass
    return entity, devs, pd.DataFrame(review)


def developer_table(p: pd.DataFrame, devs: pd.DataFrame, conn: sqlite3.Connection) -> pd.DataFrame:
    comp = pd.read_sql_query("SELECT project_id, category, status, order_type, order_date, penalty_flag "
                             "FROM complaints WHERE project_id IS NOT NULL", conn)
    acts = pd.read_sql_query("SELECT project_id, action_category, action_subtype, order_date, penalty_flag, "
                             "penalty_amount FROM regulatory_actions WHERE project_id IS NOT NULL", conn)
    apps = pd.read_sql_query("SELECT project_id, appeal_key FROM appeals WHERE project_id IS NOT NULL", conn)
    names = devs.groupby("entity_id").agg(
        developer=("canonical_name", lambda s: Counter(s).most_common(1)[0][0]),
        promoter_accounts=("developer_id", "nunique"),
        promoter_account_ids=("developer_id", lambda s: ",".join(map(str, sorted(s)))),
        entity_type=("entity_type", "first"), merge_basis=("merge_basis", "first"),
        declared_group_name=("declared_group_name", "first")).reset_index()
    rows = []
    for eid, g in p[p["entity_id"].notna()].groupby("entity_id"):
        crawled = g[g["detail_crawled"] == 1]
        pids = set(crawled["project_id"])
        comp_g, acts_g = comp[comp["project_id"].isin(pids)], acts[acts["project_id"].isin(pids)]
        prog = crawled["completion_pct"].dropna()
        inv = crawled[crawled["booked_units"].notna() & crawled["unbooked_units"].notna()]
        sold, unsold = int(inv["booked_units"].sum()), int(inv["unbooked_units"].sum())
        known_orig = crawled[crawled["delayed"].notna()]
        late = crawled["days_beyond_current"].dropna()
        late_orig = crawled["days_beyond_original"].dropna()
        ext = crawled["extension_count"].fillna(0)
        ext_days = crawled.loc[ext > 0, "total_extension_days"].dropna()
        cat = crawled["execution_category"].value_counts()
        pend = comp_g["status"].map(_pending)
        rows.append({
            "entity_id": eid,
            "projects": len(g), "projects_analyzed": len(crawled),
            "residential_projects": int((g["project_category"] == "residential").sum()),
            "mixed_projects": int((g["project_category"] == "mixed").sum()),
            "completed": int(cat.get("Completed", 0)),
            "ongoing": int(len(crawled) - cat.get("Completed", 0) - cat.get("Cancelled/Revoked", 0)),
            "near_completion": int(cat.get("Near Completion", 0)),
            "under_construction": int(cat.get("Under Construction", 0)),
            "early_stage": int(cat.get("Early Stage", 0)),
            "overdue": int(cat.get("Overdue", 0)),
            "stalled": int(cat.get("Stalled / No Recent Progress", 0)),
            "cancelled_revoked": int(cat.get("Cancelled/Revoked", 0)),
            "status_unknown": int(sum(v for k, v in cat.items() if k.startswith("Unknown"))),
            "delayed": int(known_orig["delayed"].sum()),
            "delay_rate_pct": _pct(int(known_orig["delayed"].sum()), len(known_orig)),
            "extended": int((ext > 0).sum()),
            "total_extensions": int(ext.sum()),
            "repeated_extensions": int((ext >= 2).sum()),
            "avg_extension_days": _r(ext_days.mean()), "max_extension_days": _r(ext_days.max()),
            "completion_rate_pct": _pct(int(cat.get("Completed", 0)), len(crawled)),
            "avg_progress_pct": _r(prog.mean()), "median_progress_pct": _r(prog.median()),
            "min_progress_pct": _r(prog.min()), "max_progress_pct": _r(prog.max()),
            "units_weighted_progress_pct": _wavg(crawled, "completion_pct", "total_units"),
            "projects_with_progress_data": int(len(prog)),
            "progress_lt25": int((prog < 25).sum()), "progress_25_50": int(((prog >= 25) & (prog < 50)).sum()),
            "progress_50_75": int(((prog >= 50) & (prog < 75)).sum()), "progress_ge75": int((prog >= 75).sum()),
            "very_low_progress": int(((crawled["completion_pct"] < EARLY_STAGE)
                                      & (crawled["timeline_elapsed_pct"] >= 50)
                                      & (crawled["status_derived"] != "completed")).sum()),
            "high_progress_not_completed": int(((crawled["completion_pct"] >= HIGH_PROGRESS_NOT_DONE)
                                                & (crawled["status_derived"] != "completed")).sum()),
            "avg_delay_days": _r(late[late > 0].mean()), "max_delay_days": _r(late.max()),
            "avg_delay_vs_original_days": _r(late_orig[late_orig > 0].mean()),
            "units": int(g["total_units"].dropna().sum()),
            "projects_with_unit_data": int(len(inv)),
            "sold": sold if len(inv) else None, "unsold": unsold if len(inv) else None,
            "sales_pct": _pct(sold, sold + unsold), "unsold_pct": _pct(unsold, sold + unsold),
            "rera_complaints": int(comp_g["category"].isin(["rera_complaint", "complaint_unspecified_form", "unknown"]).sum()),
            "adjudication_complaints": int((comp_g["category"] == "adjudication_complaint").sum()),
            "review_and_other_applications": int(comp_g["category"].isin(["review", "ratification", "conciliation"]).sum()),
            "complaints_total": int(len(comp_g)),
            "complaints_resolved": int((pend == False).sum()),  # noqa: E712
            "complaints_pending": int((pend == True).sum()),  # noqa: E712
            "complaints_status_unknown": int(pend.isna().sum()),
            "projects_with_complaints": int(comp_g["project_id"].nunique()),
            "complaints_per_project": round(len(comp_g) / len(crawled), 3) if len(crawled) else None,
            "suo_motu_proceedings": int((acts_g["action_category"] == "suo_motu_proceeding").sum()),
            "orders_published": int(comp_g["order_date"].notna().sum() + acts_g["order_date"].notna().sum()),
            "penalty_orders": int((comp_g["penalty_flag"].fillna("").str.upper() == "YES").sum()
                                  + ((acts_g["action_category"] == "suo_motu_proceeding")
                                     & (acts_g["penalty_flag"].fillna("").str.upper() == "YES")).sum()),
            "challans": int((acts_g["action_category"] == "penalty_challan").sum()),
            "challan_amount": float(acts_g.loc[acts_g["action_category"] == "penalty_challan", "penalty_amount"].fillna(0).sum()),
            "appeals_name_matched": int(apps["project_id"].isin(pids).sum()),
            "court_proceedings": 0,     # GujRERA HC/SC lists are empty (checked); none officially referenced
            "qpr_filing_defaults": int(crawled["qpr_defaults"].fillna(0).sum()),
            "regulatory_actions": int(len(acts_g)),
            "projects_with_regulatory_actions": int(acts_g["project_id"].nunique()),
        })
    d = pd.DataFrame(rows).merge(names, on="entity_id", how="left")
    lead = ["entity_id", "developer", "promoter_accounts", "promoter_account_ids", "entity_type", "merge_basis",
            "declared_group_name"]
    return d[lead + [c for c in d.columns if c not in lead]]


def _pending(status):
    from analytics.project_analysis import complaint_is_pending
    return complaint_is_pending(status, None)


def _pct(a, b):
    return round(a / b * 100, 1) if b else None


def _r(v, n=1):
    return None if v is None or pd.isna(v) else round(float(v), n)


def _wavg(df, val, w):
    x = df[df[val].notna() & df[w].notna() & (df[w] > 0)]
    return round(float((x[val] * x[w]).sum() / x[w].sum()), 1) if len(x) else None


# ---------------------------------------------------------------------------------------------- rankings
def rankings(d: pd.DataFrame) -> dict[str, pd.DataFrame]:
    elig = d[d["projects_analyzed"] >= MIN_PROJECTS_PCT].copy()
    sales_elig = d[(d["projects_with_unit_data"] >= MIN_PROJECTS_PCT) & ((d["sold"].fillna(0) + d["unsold"].fillna(0)) >= MIN_UNITS_SALES)]
    out = {}
    out["A_portfolio_size"] = d.sort_values(["projects", "units"], ascending=False)
    b = elig.copy()
    b["completion_performance_index"] = b[["completion_rate_pct", "avg_progress_pct"]].assign(
        on_time=100 - b["delay_rate_pct"]).mean(axis=1, skipna=True).round(1)
    out["B_completion_performance"] = b.sort_values(["completion_performance_index", "projects"], ascending=False)
    out["C_sales_performance"] = sales_elig.sort_values(["sales_pct", "sold"], ascending=False)
    s = elig.copy()
    s["extension_rate_pct"] = (s["extended"] / s["projects_analyzed"] * 100).round(1)
    s["stalled_rate_pct"] = ((s["stalled"] + s["overdue"]) / s["projects_analyzed"] * 100).round(1)
    s["delay_exposure_index"] = s[["delay_rate_pct", "extension_rate_pct", "stalled_rate_pct"]].mean(axis=1, skipna=True).round(1)
    out["D_delay_exposure"] = s.sort_values(["delay_exposure_index", "projects"], ascending=False)
    out["D_project_stability"] = s.sort_values(["delay_exposure_index", "projects"], ascending=[True, False])
    e = d[d["projects_analyzed"] > 0].copy()
    e["regulatory_items"] = e["complaints_total"] + e["suo_motu_proceedings"] + e["challans"]
    e["regulatory_items_per_project"] = (e["regulatory_items"] / e["projects_analyzed"]).round(2)
    out["E_regulatory_exposure"] = e.sort_values(["regulatory_items", "regulatory_items_per_project"], ascending=False)
    out["E_regulatory_exposure_density"] = e[e["projects_analyzed"] >= MIN_PROJECTS_PCT].sort_values(
        ["regulatory_items_per_project", "regulatory_items"], ascending=False)
    inv = d[d["unsold"].notna()].copy()
    out["inventory_exposure_absolute"] = inv.sort_values("unsold", ascending=False)
    out["inventory_exposure_pct"] = sales_elig.sort_values(["unsold_pct", "unsold"], ascending=False)
    return out


# ---------------------------------------------------------------------------------------------- outliers
def project_outliers(p: pd.DataFrame) -> pd.DataFrame:
    c = p[p["detail_crawled"] == 1]
    rules = [
        ("Completed + high unsold inventory (as last reported)",
         lambda r: r["status_derived"] == "completed" and _ge(r["unsold_pct"], 30) and _ge(r["unbooked_units"], 10),
         lambda r: f"completed (basis: {r['status_derived_basis']}); {int(r['unbooked_units'])} of "
                   f"{int(r['booked_units'] + r['unbooked_units'])} units unbooked ({r['unsold_pct']}%)"),
        ("High construction progress + low sales",
         lambda r: r["status_derived"] != "completed" and _ge(r["completion_pct"], 75) and _le(r["sales_pct"], 40),
         lambda r: f"QPR progress {r['completion_pct']}% ({r['latest_progress_quarter']}) but only {r['sales_pct']}% booked"),
        ("Low construction progress + high sales",
         lambda r: r["status_derived"] != "completed" and _le(r["completion_pct"], 25) and _ge(r["sales_pct"], 70),
         lambda r: f"{r['sales_pct']}% booked with QPR progress {r['completion_pct']}% "
                   f"({r['timeline_elapsed_pct']}% of approved timeline elapsed)"),
        ("Long delay + high unsold inventory",
         lambda r: _ge(r["days_beyond_current"], 365) and _ge(r["unsold_pct"], 50),
         lambda r: f"{int(r['days_beyond_current'])} days past approved completion {r['current_completion_date']}; "
                   f"{r['unsold_pct']}% unbooked"),
        ("Long delay + complaints",
         lambda r: _ge(r["days_beyond_current"], 365) and _ge(r["complaints_total"], 2),
         lambda r: f"{int(r['days_beyond_current'])} days past approved completion; {int(r['complaints_total'])} complaints"),
        ("Repeated extensions + low progress",
         lambda r: _ge(r["extension_count"], 2) and _le(r["completion_pct"], 50) and r["status_derived"] != "completed",
         lambda r: f"{int(r['extension_count'])} extensions ({r['original_completion_date']} -> "
                   f"{r['current_completion_date']}); progress {r['completion_pct']}%"),
        ("Very long delay (> 2 years past approved date)",
         lambda r: r["status_derived"] == "overdue" and _ge(r["days_beyond_current"], 730),
         lambda r: f"{int(r['days_beyond_current'])} days past {r['current_completion_date']}, no project-end filing; "
                   f"last QPR progress {r['completion_pct']}%"),
        ("Stalled progress",
         lambda r: r["stalled_flag"] == 1,
         lambda r: f"progress {r['completion_pct']}% ; {r['months_since_last_qpr']} months since last QPR; "
                   f"velocity {r['progress_velocity_pp_per_qtr']} pp/quarter"),
        ("High complaints (>= 3)",
         lambda r: _ge(r["complaints_total"], 3),
         lambda r: f"{int(r['complaints_total'])} complaints ({int(r['complaints_pending'] or 0)} pending)"),
    ]
    rows = []
    for r in c.to_dict("records"):
        for name, cond, why in rules:
            try:
                if cond(r):
                    rows.append({"pattern": name, "project_id": r["project_id"], "project_name": r["project_name"],
                                 "reg_no": r["reg_no_current"], "developer": r.get("developer"),
                                 "locality": r.get("locality"), "status": r["status_derived"],
                                 "why_flagged": why(r)})
            except (TypeError, ValueError, KeyError):
                continue
    return pd.DataFrame(rows, columns=["pattern", "project_id", "project_name", "reg_no", "developer", "locality",
                                       "status", "why_flagged"])


def _ge(v, t):
    return v is not None and not pd.isna(v) and float(v) >= t


def _le(v, t):
    return v is not None and not pd.isna(v) and float(v) <= t


# ---------------------------------------------------------------------------------------------- geography
def geography(p: pd.DataFrame, level: str) -> pd.DataFrame:
    c = p[p[level].notna()]
    rows = []
    for loc, g in c.groupby(level):
        cr = g[g["detail_crawled"] == 1]
        inv = cr[cr["booked_units"].notna() & cr["unbooked_units"].notna()]
        sold, unsold = int(inv["booked_units"].sum()), int(inv["unbooked_units"].sum())
        known = cr[cr["delayed"].notna()]
        top = g.groupby("developer")["project_id"].count().sort_values(ascending=False)
        rows.append({level: loc, "projects": len(g), "projects_analyzed": len(cr),
                     "residential": int((g["project_category"] == "residential").sum()),
                     "mixed": int((g["project_category"] == "mixed").sum()),
                     "units": int(g["total_units"].dropna().sum()), "sold": sold, "unsold": unsold,
                     "sales_pct": _pct(sold, sold + unsold), "unsold_pct": _pct(unsold, sold + unsold),
                     "completed": int((cr["status_derived"] == "completed").sum()),
                     "overdue": int((cr["status_derived"] == "overdue").sum()),
                     "delayed": int(known["delayed"].sum()), "delay_rate_pct": _pct(int(known["delayed"].sum()), len(known)),
                     "avg_progress_pct": _r(cr["completion_pct"].mean()),
                     "developers": int(g["entity_id"].nunique()),
                     "top_developers": "; ".join(f"{k} ({v})" for k, v in top.head(3).items())})
    cols = [level, "projects", "projects_analyzed", "residential", "mixed", "units", "sold", "unsold", "sales_pct",
            "unsold_pct", "completed", "overdue", "delayed", "delay_rate_pct", "avg_progress_pct", "developers",
            "top_developers"]
    return pd.DataFrame(rows, columns=cols).sort_values("projects", ascending=False)


def developer_concentration(p: pd.DataFrame, d: pd.DataFrame, top_n: int = 20) -> pd.DataFrame:
    top = d.sort_values(["projects", "units"], ascending=False).head(top_n)["entity_id"]
    rows = []
    for eid in top:
        g = p[p["entity_id"] == eid]
        for loc, gg in g.groupby(g["locality"].fillna("(locality not published)")):
            cr = gg[gg["detail_crawled"] == 1]
            inv = cr[cr["booked_units"].notna() & cr["unbooked_units"].notna()]
            sold, unsold = int(inv["booked_units"].sum()), int(inv["unbooked_units"].sum())
            rows.append({"developer": gg["developer"].iloc[0], "entity_id": eid, "locality": loc,
                         "taluka": gg["taluka"].mode().iloc[0] if gg["taluka"].notna().any() else None,
                         "projects": len(gg), "units": int(gg["total_units"].dropna().sum()),
                         "completed": int((cr["status_derived"] == "completed").sum()),
                         "not_completed": int((cr["status_derived"] != "completed").sum()),
                         "sold": sold, "unsold": unsold, "sales_pct": _pct(sold, sold + unsold)})
    return pd.DataFrame(rows, columns=["developer", "entity_id", "locality", "taluka", "projects", "units", "completed",
                                       "not_completed", "sold", "unsold", "sales_pct"])


# ---------------------------------------------------------------------------------------------- evidence
def evidence(conn: sqlite3.Connection, p: pd.DataFrame) -> pd.DataFrame:
    urls = {}
    q = ("SELECT endpoint_key, entity_id, url, fetched_at FROM raw_responses WHERE entity_type='project' "
         "AND endpoint_key IN (%s)" % ",".join(f"'{v[0]}'" for v in EVIDENCE_FIELDS.values()))
    for r in conn.execute(q):
        urls[(r["endpoint_key"], int(r["entity_id"]))] = (r["url"], r["fetched_at"])
    lst = {}
    for k in ("list_residential", "list_mixed"):
        r = conn.execute("SELECT url, fetched_at FROM raw_responses WHERE endpoint_key=?", (k,)).fetchone()
        if r:
            lst[k] = (r["url"], r["fetched_at"])
    rows = []
    for r in p.to_dict("records"):
        list_src = lst.get("list_mixed" if r["project_category"] == "mixed" else "list_residential", (None, None))
        for field, (ep, src) in EVIDENCE_FIELDS.items():
            v = r.get(field)
            if v is None or (isinstance(v, float) and pd.isna(v)):
                continue
            url, ts = list_src if ep == "list" else urls.get((ep, r["project_id"]), (None, None))
            if url is None and ep == "details":   # fallback chain used by the parser
                url, ts = list_src
            rows.append({"project_id": r["project_id"], "reg_no": r["reg_no_current"], "project_name": r["project_name"],
                         "developer": r.get("developer"), "metric": field, "value": v, "source": src,
                         "source_url": url, "last_checked": ts})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------------------------- report
def build_area_report(conn: sqlite3.Connection, settings: dict, district: str, out_dir: str | None = None) -> dict:
    p, res = scope_projects(conn, settings, district)
    dev_ids = set(p["developer_id"].dropna().astype(int))
    entity, devs, review = resolve_entities(conn, dev_ids)
    p["entity_id"] = p["developer_id"].map(lambda x: entity.get(int(x)) if pd.notna(x) else None)
    d = developer_table(p, devs, conn)
    p = p.merge(d[["entity_id", "developer"]], on="entity_id", how="left")
    rk = rankings(d)
    outl = project_outliers(p)
    geo_t, geo_l = geography(p, "taluka"), geography(p, "locality")
    conc = developer_concentration(p, d)
    ev = evidence(conn, p)
    out = Path(out_dir or Path(settings["exports_dir"]) / district.lower())
    out.mkdir(parents=True, exist_ok=True)
    proj_cols = ["project_id", "reg_no_current", "project_name", "project_category", "developer", "entity_id",
                 "developer_id", "taluka", "locality", "locality_source", "pincode", "latitude", "longitude", "status_source",
                 "status_derived", "status_derived_basis", "execution_category", "registration_date",
                 "original_completion_date", "current_completion_date", "completion_filing_date", "extension_count",
                 "total_extension_days", "delayed", "days_beyond_original", "days_beyond_current", "delay_months",
                 "completion_pct", "latest_progress_quarter", "previous_progress_pct", "progress_velocity_pp_per_qtr",
                 "months_since_last_qpr", "stalled_flag", "timeline_elapsed_pct", "financial_progress_pct",
                 "total_units", "booked_units", "unbooked_units", "sales_pct", "unsold_pct", "residential_units",
                 "commercial_units", "total_land_area_sqm", "total_carpet_area_sqm", "complaints_total",
                 "complaints_pending", "complaints_resolved", "suo_motu_total", "challans_total", "challan_amount_total",
                 "qpr_defaults", "portal_progress_label", "detail_crawled", "risk_score", "risk_flags"]
    pout = p[[c for c in proj_cols if c in p.columns]].copy()
    pout["risk_flags"] = pout["risk_flags"].map(lambda f: "; ".join(x["flag"] for x in f) if isinstance(f, list) else None)
    files = {}
    for name, df in {"projects": pout, "developers": d, "developer_entity_map": devs, "developer_review_flags": review,
                     "outlier_projects": outl, "geography_taluka": geo_t, "geography_locality": geo_l,
                     "developer_locality_concentration": conc, "evidence_project_metrics": ev,
                     **{f"ranking_{k}": v for k, v in rk.items()}}.items():
        path = out / f"{district.lower()}_{name}.csv"
        df.to_csv(path, index=False, encoding="utf-8-sig")
        files[name] = f"{path} ({len(df)} rows)"
    quality = data_quality(conn, p, d, review, res)
    (out / f"{district.lower()}_data_quality.json").write_text(json.dumps(quality, indent=2, default=str), encoding="utf-8")
    md = render(district, p, d, rk, outl, geo_t, geo_l, conc, quality, res["run_date"])
    (out / f"{district.lower()}_builder_intelligence_report.md").write_text(md, encoding="utf-8")
    (out / f"{district.lower()}_builder_intelligence_report.html").write_text(
        render_html(md).replace("GujRERA Executive Report", f"{district} Builder Intelligence"), encoding="utf-8")
    with pd.ExcelWriter(out / f"{district.lower()}_builder_intelligence.xlsx", engine="openpyxl") as xw:
        for name, df in [("developers", d), ("projects", pout), ("outliers", outl), ("taluka", geo_t),
                         ("locality", geo_l), ("concentration", conc), ("review_flags", review)]:
            df.to_excel(xw, sheet_name=name, index=False)
    files["report"] = str(out / f"{district.lower()}_builder_intelligence_report.md")
    return {"files": files, "quality": quality}


def data_quality(conn, p, d, review, res) -> dict:
    cr = p[p["detail_crawled"] == 1]
    n = len(p)

    def have(col):
        return int(cr[col].notna().sum())

    conflicts = conn.execute("SELECT COUNT(*), COUNT(DISTINCT entity_id) FROM field_conflicts WHERE entity_type='project' "
                             "AND entity_id IN (%s)" % ",".join(str(int(x)) for x in p["project_id"]) if n else "SELECT 0,0").fetchone()
    docs = conn.execute("SELECT COUNT(DISTINCT project_id), COUNT(*), SUM(download_status='downloaded') FROM project_documents "
                        "WHERE project_id IN (%s)" % ",".join(str(int(x)) for x in p["project_id"]) if n else "SELECT 0,0,0").fetchone()
    key = ["current_completion_date", "completion_pct", "total_units", "booked_units", "developer_id", "locality"]
    miss = {k: round(100 - (have(k) / len(cr) * 100), 1) if len(cr) else None for k in key}
    return {
        "projects_discovered": n,
        "residential": int((p["project_category"] == "residential").sum()),
        "mixed": int((p["project_category"] == "mixed").sum()),
        "unique_developer_entities": int(d["entity_id"].nunique()) if len(d) else 0,
        "unique_promoter_accounts": int(p["developer_id"].nunique()),
        "promoter_accounts_merged_by_pan_or_cin": int((d["promoter_accounts"] > 1).sum()) if len(d) else 0,
        "name_similarity_pairs_flagged_for_review": int(len(review)),
        "projects_successfully_analyzed": int(len(cr)),
        "projects_with_unit_data": int((cr["booked_units"].notna() & cr["unbooked_units"].notna()).sum()),
        "projects_with_progress_data": have("completion_pct"),
        "projects_with_complaint_data_checked": int(len(cr)),
        "projects_with_at_least_one_complaint": int((cr["complaints_total"].fillna(0) > 0).sum()),
        "projects_with_extension_data_checked": int(len(cr)),
        "projects_with_extensions": int((cr["extension_count"].fillna(0) > 0).sum()),
        "projects_with_document_records": int(docs[0] or 0), "document_records": int(docs[1] or 0),
        "documents_downloaded": int(docs[2] or 0),
        "missing_data_pct_among_analyzed": miss,
        "duplicate_project_records": int(p["project_id"].duplicated().sum()),
        "duplicate_registration_numbers": int(p["reg_no_current"].duplicated().sum()),
        "conflicting_field_records": int(conflicts[0] or 0), "projects_with_conflicts": int(conflicts[1] or 0),
        "run_date": res["run_date"],
    }


def _t(df, cols, n=20, headers=None):
    df = df.head(n)
    return _table(df, [c for c in cols if c in df.columns], headers)


def render(district, p, d, rk, outl, geo_t, geo_l, conc, q, run_date) -> str:
    cr = p[p["detail_crawled"] == 1]
    inv = cr[cr["booked_units"].notna() & cr["unbooked_units"].notna()]
    sold, unsold = int(inv["booked_units"].sum()), int(inv["unbooked_units"].sum())
    cat = cr["execution_category"].value_counts()
    elig = d[d["projects_analyzed"] >= MIN_PROJECTS_PCT]
    M = []
    a = M.append
    a(f"# {district} RERA Builder Intelligence — Residential & Mixed-use")
    a(f"*Source: official GujRERA public portal data, checked {run_date}. {q['projects_successfully_analyzed']:,} of "
      f"{q['projects_discovered']:,} {district} projects analysed in full. “—” = data unavailable (never zero).*")
    a("")
    a("> Neutral, evidence-based metrics. Complaints, extensions and proceedings are recorded facts, not findings of "
      "wrongdoing. Exposure rankings are analytical, not an official RERA rating.")
    sec = lambda t: (a(""), a(f"## {t}"), a(""))  # noqa: E731

    # ---------------- executive summary
    sec("Executive Summary")
    top_proj = d.sort_values(["projects", "units"], ascending=False).iloc[0] if len(d) else None
    top_units = d.sort_values("units", ascending=False).iloc[0] if len(d) else None
    top_done = d.sort_values(["completed", "projects"], ascending=False).iloc[0] if len(d) else None
    best_rate = elig.sort_values(["completion_rate_pct", "projects"], ascending=False).iloc[0] if len(elig) else None
    sales = rk["C_sales_performance"]
    top_sales = sales.iloc[0] if len(sales) else None
    top_unsold = rk["inventory_exposure_absolute"].iloc[0] if len(rk["inventory_exposure_absolute"]) else None
    top_delay = d.sort_values(["overdue", "delayed"], ascending=False).iloc[0] if len(d) else None
    top_ext = d.sort_values(["total_extensions", "extended"], ascending=False).iloc[0] if len(d) else None
    top_cmp = d.sort_values(["complaints_total", "projects_with_complaints"], ascending=False).iloc[0] if len(d) else None
    top_reg = d.sort_values(["regulatory_actions", "challans"], ascending=False).iloc[0] if len(d) else None
    qa = [
        ("Who are the largest developers?", top_proj is not None and
         f"{top_proj['developer']} by projects ({top_proj['projects']}); {top_units['developer']} by units ({top_units['units']:,})."),
        ("Who has the largest project portfolio?", top_proj is not None and f"{top_proj['developer']} — {top_proj['projects']} projects."),
        ("Who has completed the most projects?", top_done is not None and f"{top_done['developer']} — {top_done['completed']} completed."),
        ("Who has the highest completion rate (≥ 3 analysed projects)?", best_rate is not None and
         f"{best_rate['developer']} — {best_rate['completion_rate_pct']}% of {best_rate['projects_analyzed']} projects."),
        ("Who has the highest sales %?", top_sales is not None and
         f"{top_sales['developer']} — {top_sales['sales_pct']}% booked of {int(top_sales['sold'] + top_sales['unsold']):,} units "
         f"(≥ {MIN_PROJECTS_PCT} projects, ≥ {MIN_UNITS_SALES} units)."),
        ("Who has the highest unsold inventory?", top_unsold is not None and
         f"{top_unsold['developer']} — {int(top_unsold['unsold']):,} unbooked units ({top_unsold['unsold_pct']}%)."),
        ("Which developers have the most delayed projects?", top_delay is not None and
         f"{top_delay['developer']} — {top_delay['overdue']} overdue, {top_delay['delayed']} past original date."),
        ("Which developers have the most extensions?", top_ext is not None and
         f"{top_ext['developer']} — {top_ext['total_extensions']} extensions on {top_ext['extended']} projects."),
        ("Which developers have the most complaints?", top_cmp is not None and
         f"{top_cmp['developer']} — {top_cmp['complaints_total']} complaints on {top_cmp['projects_with_complaints']} projects."),
        ("Which developers have the most regulatory actions?", top_reg is not None and
         f"{top_reg['developer']} — {top_reg['regulatory_actions']} (suo-motu {top_reg['suo_motu_proceedings']}, "
         f"challans {top_reg['challans']})."),
    ]
    for qq, ans in qa:
        a(f"- **{qq}** {ans or '—'}")
    od = cr[cr["status_derived"] == "overdue"].sort_values("days_beyond_current", ascending=False)
    if len(od):
        a(f"- **Which projects look most delayed?** " + "; ".join(
            f"{r.project_name} ({r.developer}, {int(r.days_beyond_current)} days past {r.current_completion_date})"
            for r in od.head(5).itertuples()))
    hu = cr[cr["unbooked_units"].notna()].sort_values("unbooked_units", ascending=False)
    if len(hu):
        a(f"- **Which projects have high unsold inventory?** " + "; ".join(
            f"{r.project_name} ({int(r.unbooked_units)} unbooked, {r.unsold_pct}%)" for r in hu.head(5).itertuples()))
    strong = strong_execution(d)
    if len(strong):
        a("- **Which developers show strong overall execution?** " + "; ".join(strong["developer"].head(8)) +
          f" (criteria in *Top Performing Developers*).")
    hx = rk["D_delay_exposure"].head(5)["developer"].tolist()
    rx = rk["E_regulatory_exposure_density"].head(5)["developer"].tolist()
    ix = rk["inventory_exposure_pct"].head(5)["developer"].tolist()
    a(f"- **Which developers have higher exposure?** Delay: {', '.join(hx) or '—'}. Regulatory: {', '.join(rx) or '—'}. "
      f"Inventory: {', '.join(ix) or '—'}.")

    # ---------------- market overview
    sec(f"{district} RERA Market Overview")
    ov = pd.DataFrame([
        ("Projects (Residential / Mixed)", f"{q['projects_discovered']:,} ({q['residential']:,} / {q['mixed']:,})"),
        ("Developer entities (promoter accounts)", f"{q['unique_developer_entities']:,} ({q['unique_promoter_accounts']:,})"),
        ("Projects analysed in full", f"{q['projects_successfully_analyzed']:,}"),
        *[(f"— {k}", f"{int(v):,}") for k, v in cat.items()],
        ("Projects past original completion date", f"{int(cr['delayed'].fillna(0).sum()):,}"),
        ("Projects with ≥ 1 extension", f"{int((cr['extension_count'].fillna(0) > 0).sum()):,}"),
        ("Units (declared)", f"{int(cr['total_units'].dropna().sum()):,}"),
        ("Booked (sold) / unbooked (unsold)", f"{sold:,} / {unsold:,}"),
        ("Sales % / unsold %", f"{_pct(sold, sold + unsold)}% / {_pct(unsold, sold + unsold)}%"),
        ("Sales % — projects not yet completed (live QPR data)",
         (lambda a: f"{_pct(int(a['booked_units'].sum()), int(a['booked_units'].sum() + a['unbooked_units'].sum()))}% "
                    f"of {int(a['booked_units'].sum() + a['unbooked_units'].sum()):,} units")(
             inv[inv["status_derived"] != "completed"])),
        ("Complaints / suo-motu proceedings / challans",
         f"{int(cr['complaints_total'].fillna(0).sum()):,} / {int(cr['suo_motu_total'].fillna(0).sum()):,} / "
         f"{int(cr['challans_total'].fillna(0).sum()):,}"),
    ], columns=["Metric", "Value"])
    a(_table(ov, ["Metric", "Value"]))

    # ---------------- master comparison table
    sec("Developer Master Ranking (primary comparison table)")
    a(f"Sorted by projects. Full table for all {len(d):,} developers: `{district.lower()}_developers.csv`.")
    a("")
    a(_t(d.sort_values(["projects", "units"], ascending=False),
         ["developer", "projects", "completed", "ongoing", "delayed", "extended", "units", "sold", "unsold", "sales_pct",
          "avg_progress_pct", "complaints_total", "regulatory_actions"], 50,
         ["Developer", "Projects", "Completed", "Ongoing", "Delayed", "Extended", "Units", "Sold", "Unsold", "Sales %",
          "Avg Progress %", "Complaints", "Regulatory Actions"]))

    # ---------------- rankings
    sec("Rankings (neutral, multiple dimensions)")
    for title, key, cols in [
        ("A — Portfolio size", "A_portfolio_size", ["developer", "projects", "units", "residential_projects", "mixed_projects"]),
        ("B — Completion performance (≥ 3 analysed projects)", "B_completion_performance",
         ["developer", "projects_analyzed", "completion_rate_pct", "avg_progress_pct", "delay_rate_pct", "completion_performance_index"]),
        ("C — Sales performance (≥ 3 projects with unit data, ≥ 100 units)", "C_sales_performance",
         ["developer", "projects_with_unit_data", "sold", "unsold", "sales_pct"]),
        ("D — Delay exposure (≥ 3 analysed projects; higher = more delays/extensions/overdue/stalled)", "D_delay_exposure",
         ["developer", "projects_analyzed", "delay_rate_pct", "extension_rate_pct", "stalled_rate_pct", "delay_exposure_index"]),
        ("D — Project stability (lowest delay exposure, ≥ 3 analysed projects)", "D_project_stability",
         ["developer", "projects_analyzed", "delay_rate_pct", "extension_rate_pct", "stalled_rate_pct", "delay_exposure_index"]),
        ("E — Regulatory exposure (absolute)", "E_regulatory_exposure",
         ["developer", "projects_analyzed", "complaints_total", "suo_motu_proceedings", "challans", "regulatory_items",
          "regulatory_items_per_project"]),
        ("E — Regulatory exposure density (per analysed project, ≥ 3 projects)", "E_regulatory_exposure_density",
         ["developer", "projects_analyzed", "regulatory_items", "regulatory_items_per_project"]),
    ]:
        a(f"### {title}")
        a("")
        a(_t(rk[key], cols, 20))
        a("")

    # ---------------- top lists
    sec("Top 20 Lists")
    tops = [
        ("Most projects", d.sort_values("projects", ascending=False), "projects"),
        ("Most units", d.sort_values("units", ascending=False), "units"),
        ("Most completed projects", d.sort_values("completed", ascending=False), "completed"),
        ("Highest completion rate (≥ 3 analysed)", elig.sort_values(["completion_rate_pct", "projects"], ascending=False), "completion_rate_pct"),
        ("Highest sales % (≥ 3 projects, ≥ 100 units)", sales, "sales_pct"),
        ("Highest unsold inventory (units)", rk["inventory_exposure_absolute"], "unsold"),
        ("Highest unsold % (≥ 3 projects, ≥ 100 units)", rk["inventory_exposure_pct"], "unsold_pct"),
        ("Most delayed projects (past original date)", d.sort_values("delayed", ascending=False), "delayed"),
        ("Most overdue projects", d.sort_values("overdue", ascending=False), "overdue"),
        ("Most extensions", d.sort_values("total_extensions", ascending=False), "total_extensions"),
        ("Most complaints", d.sort_values("complaints_total", ascending=False), "complaints_total"),
        ("Most regulatory actions", d.sort_values("regulatory_actions", ascending=False), "regulatory_actions"),
    ]
    for title, df, metric in tops:
        a(f"### {title}")
        a("")
        a(_t(df, ["developer", metric, "projects", "projects_analyzed"], 20, ["Developer", metric, "Projects", "Sample size (analysed)"]))
        a("")

    # ---------------- project performance & categories
    sec("Project Performance Analysis")
    a(_table(cat.rename_axis("category").reset_index(name="projects"), ["category", "projects"]))
    a("")
    a("Category rules: Completed (Q-E/Form 4 filing or portal Completed) → Overdue (past latest approved date) → "
      "Stalled (QPR gain < 2 pp over the last 3 reports, or no QPR for ≥ 9 months) → Near Completion (≥ 75%) → "
      "Under Construction (25–75%) → Early Stage (< 25%).")

    sec("Sales & Inventory Analysis")
    a(f"Booked = sold, unbooked = unsold (GujRERA block booking chart). {q['projects_with_unit_data']:,} projects publish "
      f"a booking split; projects without it are excluded, not counted as zero. Cancelled units are not published separately.")
    a("")
    a("### Highest sales developers")
    a("")
    a(_t(sales, ["developer", "projects_with_unit_data", "sold", "unsold", "sales_pct"], 20))
    a("")
    a("### Largest unsold inventory (absolute)")
    a("")
    a(_t(rk["inventory_exposure_absolute"], ["developer", "projects_with_unit_data", "unsold", "unsold_pct"], 20))
    a("")
    pi = inv.copy()
    for title, df in [("Projects with the most unbooked units", pi.sort_values("unbooked_units", ascending=False)),
                      ("Fully / nearly sold projects (≥ 95% booked, ≥ 50 units)",
                       pi[(pi["sales_pct"] >= 95) & ((pi["booked_units"] + pi["unbooked_units"]) >= 50)].sort_values("booked_units", ascending=False)),
                      ("Lowest sales (≤ 20% booked, ≥ 50 units)",
                       pi[(pi["sales_pct"] <= 20) & ((pi["booked_units"] + pi["unbooked_units"]) >= 50)].sort_values("unbooked_units", ascending=False))]:
        a(f"### {title}")
        a("")
        a(_t(df, ["project_name", "developer", "locality", "status_derived", "completion_pct", "booked_units",
                  "unbooked_units", "sales_pct"], 20))
        a("")
    for pat in ["Completed + high unsold inventory (as last reported)", "High construction progress + low sales",
                "Low construction progress + high sales", "Long delay + high unsold inventory"]:
        sub = outl[outl["pattern"] == pat] if len(outl) else outl
        a(f"### {pat} ({len(sub)} projects)")
        a("")
        if len(sub):
            a(_t(sub, ["project_name", "developer", "locality", "why_flagged"], 15))
        a("")

    sec("Construction & Completion Analysis")
    a(_t(elig.sort_values("projects", ascending=False),
         ["developer", "projects_with_progress_data", "avg_progress_pct", "median_progress_pct", "min_progress_pct",
          "max_progress_pct", "progress_lt25", "progress_25_50", "progress_50_75", "progress_ge75",
          "very_low_progress", "high_progress_not_completed", "stalled"], 30))
    a("")
    prof = elig[elig["projects_with_progress_data"] >= MIN_PROJECTS_PCT].copy()
    prof["profile"] = prof.apply(lambda r: "consistently high (all ≥ 75%)" if r["min_progress_pct"] is not None and r["min_progress_pct"] >= 75
                                 else "consistently low (all < 50%)" if r["max_progress_pct"] is not None and r["max_progress_pct"] < 50
                                 else "mixed", axis=1)
    a(_table(prof["profile"].value_counts().rename_axis("progress profile").reset_index(name="developers"),
             ["progress profile", "developers"]))
    a("")

    sec("Delay & Extension Analysis")
    a("Delay = days past the original / latest approved completion date (at the project-end filing for completed "
      "projects, today otherwise). An approved extension is a factual project-history event, not wrongdoing.")
    a("")
    a(_t(d.sort_values(["delayed", "overdue"], ascending=False),
         ["developer", "projects_analyzed", "delayed", "delay_rate_pct", "overdue", "avg_delay_days", "max_delay_days",
          "extended", "total_extensions", "avg_extension_days", "max_extension_days", "repeated_extensions"], 30))
    a("")
    avg_rate = _pct(int((cr["extension_count"].fillna(0) > 0).sum()), len(cr))
    hi = elig.assign(ext_rate=(elig["extended"] / elig["projects_analyzed"] * 100).round(1))
    hi = hi[hi["ext_rate"] >= 2 * (avg_rate or 0)].sort_values(["ext_rate", "extended"], ascending=False)
    a(f"Developers whose extension frequency is at least twice the {district} average ({avg_rate}% of projects): {len(hi)}.")
    a("")
    a(_t(hi, ["developer", "projects_analyzed", "extended", "ext_rate", "total_extensions"], 20))
    a("")
    a("### Most delayed projects")
    a("")
    a(_t(od, ["project_name", "developer", "locality", "original_completion_date", "current_completion_date",
              "extension_count", "days_beyond_current", "completion_pct"], 25))

    sec("Complaint & Regulatory Analysis")
    a("Categories are kept separate. Court proceedings: GujRERA's High Court / Supreme Court lists were checked and are "
      "empty; Appellate Tribunal orders publish party names only (name-matched, low confidence).")
    a("")
    a(_t(d.sort_values(["complaints_total", "regulatory_actions"], ascending=False),
         ["developer", "projects_analyzed", "rera_complaints", "adjudication_complaints", "review_and_other_applications",
          "complaints_resolved", "complaints_pending", "complaints_status_unknown", "projects_with_complaints",
          "complaints_per_project", "suo_motu_proceedings", "orders_published", "penalty_orders", "challans",
          "challan_amount", "appeals_name_matched", "court_proceedings", "qpr_filing_defaults"], 30,
         ["Developer", "Analysed", "RERA complaints", "Adjudication", "Review/other", "Resolved", "Pending", "Status n/a",
          "Projects w/ complaints", "Per project", "Suo-motu", "Orders", "Penalty orders", "Challans", "Challan Rs",
          "Appeals", "Court", "QPR defaults"]))

    sec("Geographic Analysis")
    a("Areas are GujRERA's own fields: taluka (sub-district) and moje / revenue village (locality). No zones or "
      "boundaries are inferred.")
    a("")
    a("### By taluka")
    a("")
    a(_t(geo_t, ["taluka", "projects", "units", "sold", "unsold", "unsold_pct", "delay_rate_pct", "overdue",
                 "avg_progress_pct", "developers", "top_developers"], 20))
    a("")
    g5 = geo_l[geo_l["projects"] >= 5]
    for title, df in [("Highest project concentration (localities)", geo_l),
                      ("Highest unsold inventory (localities, ≥ 5 projects)", g5.sort_values("unsold", ascending=False)),
                      ("Highest delay rate (localities, ≥ 5 projects)", g5.sort_values("delay_rate_pct", ascending=False)),
                      ("Highest average progress (localities, ≥ 5 projects)", g5.sort_values("avg_progress_pct", ascending=False))]:
        a(f"### {title}")
        a("")
        a(_t(df, ["locality", "projects", "units", "unsold", "unsold_pct", "delay_rate_pct", "avg_progress_pct",
                  "top_developers"], 15))
        a("")
    a("### Developer portfolio concentration (top 20 developers by projects)")
    a("")
    a(_t(conc.sort_values(["developer", "projects"], ascending=[True, False]),
         ["developer", "locality", "taluka", "projects", "units", "completed", "not_completed", "sold", "unsold", "sales_pct"], 80))

    sec("Outlier Projects")
    if len(outl):
        a(_table(outl["pattern"].value_counts().rename_axis("pattern").reset_index(name="projects"), ["pattern", "projects"]))
        a("")
        for pat, g in outl.groupby("pattern"):
            a(f"### {pat}")
            a("")
            a(_t(g, ["project_name", "reg_no", "developer", "locality", "why_flagged"], 15))
            a("")

    sec("Top Performing Developers")
    a(f"Criteria (≥ {MIN_PROJECTS_PCT} analysed projects): completion rate ≥ 50% or average progress ≥ 75%, delay rate "
      f"≤ 25%, no overdue or stalled project, sales ≥ 75% where unit data exist, ≤ 0.25 complaints per project and no "
      f"penalty / challan.")
    a("")
    a(_t(strong, ["developer", "projects_analyzed", "completed", "completion_rate_pct", "avg_progress_pct",
                  "delay_rate_pct", "sales_pct", "complaints_total", "regulatory_actions"], 25))

    sec("High Exposure Developers")
    for title, key, cols in [("Delay exposure", "D_delay_exposure", ["developer", "projects_analyzed", "delay_rate_pct",
                                                                     "overdue", "extended", "stalled", "delay_exposure_index"]),
                             ("Inventory exposure (unsold %)", "inventory_exposure_pct", ["developer", "projects_with_unit_data",
                                                                                         "unsold", "unsold_pct"]),
                             ("Regulatory exposure", "E_regulatory_exposure_density", ["developer", "projects_analyzed",
                                                                                      "complaints_total", "suo_motu_proceedings",
                                                                                      "challans", "regulatory_items_per_project"])]:
        a(f"### {title}")
        a("")
        a(_t(rk[key], cols, 15))
        a("")

    sec("Developer-by-Developer Analysis (top 30 by projects)")
    for r in d.sort_values(["projects", "units"], ascending=False).head(30).to_dict("records"):
        g = p[p["entity_id"] == r["entity_id"]]
        locs = g["locality"].value_counts().head(3)
        a(f"**{r['developer']}** ({r['entity_type'] or 'type n/a'}; promoter accounts {r['promoter_account_ids']}) — "
          f"{r['projects']} projects ({r['residential_projects']} residential / {r['mixed_projects']} mixed), "
          f"{r['completed']} completed, {r['overdue']} overdue, {r['extended']} extended; completion rate "
          f"{r['completion_rate_pct'] if r['completion_rate_pct'] is not None else '—'}%; avg progress "
          f"{r['avg_progress_pct'] if r['avg_progress_pct'] is not None else '—'}%; units {r['units']:,}, sold "
          f"{r['sold'] if r['sold'] is not None else '—'} ({r['sales_pct'] if r['sales_pct'] is not None else '—'}%); "
          f"complaints {r['complaints_total']}, suo-motu {r['suo_motu_proceedings']}, challans {r['challans']}; "
          f"main localities: {', '.join(f'{k} ({v})' for k, v in locs.items()) or '—'}.")
        a("")

    sec("Data Quality & Limitations")
    a("```json")
    a(json.dumps(q, indent=2, default=str))
    a("```")
    a("")
    for x in LIMITS:
        a(f"- {x}")

    sec("Source / Evidence Appendix")
    a(f"- Every project-level number is listed with its official source endpoint URL and last-checked timestamp in "
      f"`{district.lower()}_evidence_project_metrics.csv` (metric, value, source, project, source URL, last checked).")
    a(f"- Developer figures are sums / ratios over the project rows in `{district.lower()}_projects.csv` "
      f"(filter by `entity_id`); developer ↔ promoter-account mapping in `{district.lower()}_developer_entity_map.csv`.")
    a("- Complaint / action records: `exports/csv/complaints.csv`, `regulatory_actions.csv` (acknowledgement number, "
      "order date, outcome text, penalty, order PDF link `https://gujrera.gujarat.gov.in/vdms/download/<uid>`).")
    a("- Universe: `https://gujrera.gujarat.gov.in/dashboard/get-district-wise-projectlist/0/0/all/all/Residential` and "
      "`.../Mixed`; project pages: `https://gujrera.gujarat.gov.in/project_reg/public/getproject-details/<project_id>`.")
    a("- Method details: `docs/data_dictionary.md`, `docs/gujrera_architecture.md`, `docs/risk_methodology.md`.")
    return "\n".join(M)


def strong_execution(d: pd.DataFrame) -> pd.DataFrame:
    e = d[d["projects_analyzed"] >= MIN_PROJECTS_PCT]
    cond = (((e["completion_rate_pct"].fillna(0) >= 50) | (e["avg_progress_pct"].fillna(0) >= 75))
            & (e["delay_rate_pct"].fillna(100) <= 25) & (e["overdue"] == 0) & (e["stalled"] == 0)
            & (e["sales_pct"].isna() | (e["sales_pct"] >= 75))
            & (e["complaints_per_project"].fillna(0) <= 0.25) & (e["challans"] == 0) & (e["penalty_orders"] == 0))
    return e[cond].sort_values(["completed", "projects_analyzed"], ascending=False)


LIMITS = [
    "Sold/unsold = GujRERA booked/unbooked counts reported by promoters through QPRs; cancellations are not published separately.",
    "QPR filing stops after the project-end (Q-E) filing, so booking figures of completed projects are frozen at the last QPR; sales made after completion are not reported and unsold stock of completed projects may be overstated.",
    "Completion = Q-E project-end filing with Form 4 or portal status Completed; the portal's own status field marks very "
    "few projects Completed, so it is not used alone.",
    "Delay is measured against approved dates; extensions are approved by GujRERA and are not treated as wrongdoing.",
    "Complaints are attributed via the project's own application list and the registration serial in published "
    "judgements; matters without a registration number cannot be attributed.",
    "Developer entities merge promoter accounts only on identical PAN (hash) or company registration number; name "
    "look-alikes are listed in developer_review_flags.csv and kept separate. Group companies using separate SPVs "
    "appear as separate developers unless they share a PAN.",
    "Localities are GujRERA's moje / revenue-village field; spelling variants of the same locality may remain separate.",
    "Scope = Ahmedabad district as recorded by GujRERA (includes Ahmedabad City, Daskroi, Sanand, Bavla, Dholka, "
    "Viramgam, etc.; see the taluka table).",
]
