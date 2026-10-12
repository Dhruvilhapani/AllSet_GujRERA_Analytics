"""Executive report + run summary (the completion statistics requested for every run)."""
from __future__ import annotations

import html
import json
import sqlite3
from collections import Counter

import pandas as pd

from database.db import RawStore

KEY_FIELDS = ["current_completion_date", "original_completion_date", "registration_date", "latest_progress_pct",
              "total_units", "booked_units", "unbooked_units", "developer_id", "district", "completion_filing_status",
              "financial_progress_pct", "total_land_area_sqm"]

LIMITATIONS = [
    "Sold/unsold = the portal's booked/unbooked counts per block (QPR-fed); no sale-deed or price data is published.",
    "Portal `project_status` is unreliable for completion (only a few dozen projects are marked Completed); "
    "completion is derived from the Q-E project-end filing with Form 4 (architect completion certificate).",
    "Extension reasons are not published in a structured form; `reason` is NULL unless a source states it.",
    "Old-format extension numbers (EXn/DDMMYY) carry no 'valid till' date; such extensions get an end date only "
    "from the extension certificate PDF or the project's current completion date (latest extension only).",
    "Complaint orders are scanned images; their text needs OCR (Tesseract not installed => flagged ocr_needed). "
    "Structured order fields (date, outcome remark, penalty flag/amount) come from the judgement API.",
    "Complaints filed before online numbering and some offline matters cannot be linked to a project when the "
    "published record has no registration number (project_link_method = unlinked).",
    "Appellate Tribunal orders publish party names only; project links are verbatim name matches (low confidence). "
    "High Court / Supreme Court lists on the portal are currently empty.",
    "Suo-motu sub-type labels (NCQR, NCAR, ...) are expansions inferred from GujRERA's codes.",
    "Promoter accounts are GujRERA logins; one legal entity may hold several accounts. Only PAN-identical accounts are "
    "rolled up (developer_group_metrics); name similarity is review-only.",
    "The Analytical Risk Score is a transparent heuristic over public data, not an official rating or legal finding.",
]


def _q(conn, sql, *a):
    return conn.execute(sql, a).fetchone()[0]


def run_summary(conn: sqlite3.Connection, res: dict, settings: dict) -> dict:
    proj: pd.DataFrame = res["projects"]
    raw = RawStore(conn)
    # 1 / 11: universe + duplicates in the official lists
    list_ids, list_regs = Counter(), Counter()
    for key in settings["target_project_types"]:
        j, _ = raw.load(key, key)
        for r in (j or {}).get("data") or []:
            list_ids[int(r["projectRegId"])] += 1
            list_regs[r.get("regNo")] += 1
    counts, _ = raw.load("dashboard_counts", "dashboard")
    grid = {r.get("type"): r.get("total") for r in (counts or {}).get("data") or []}
    complaint_keys = Counter()
    for _eid, j, _m in raw.iter_endpoint("complaint_list"):
        for r in (j.get("data") if isinstance(j, dict) else None) or []:
            complaint_keys[r.get("aknowledgement_no") or r.get("comp_id")] += 1
    crawled = proj["detail_crawled"] == 1
    failed_projects = conn.execute("""SELECT COUNT(DISTINCT entity_id) FROM crawl_queue WHERE entity_type='project'
                                      AND status='failed' AND run_id=(SELECT run_id FROM crawl_runs
                                      ORDER BY started_at DESC LIMIT 1)""").fetchone()[0]
    missing = {f: int(proj.loc[crawled, f].isna().sum()) for f in KEY_FIELDS if f in proj}
    docs = dict(conn.execute("""SELECT 'total', COUNT(*) FROM project_documents UNION ALL
                                SELECT 'critical', COUNT(*) FROM project_documents WHERE download_priority='critical' UNION ALL
                                SELECT 'downloaded', COUNT(*) FROM project_documents WHERE download_status='downloaded' UNION ALL
                                SELECT 'download_failed', COUNT(*) FROM project_documents WHERE download_status='failed' UNION ALL
                                SELECT 'text_extracted', COUNT(*) FROM project_documents WHERE text_status IN ('extracted','ocr') UNION ALL
                                SELECT 'ocr_needed', COUNT(*) FROM project_documents WHERE text_status='ocr_needed'""").fetchall())
    status_counts = proj["status_derived"].value_counts(dropna=False).to_dict()
    summary = {
        "run_date": res["run_date"],
        "1_total_projects_discovered": len(list_ids),
        "1a_dashboard_totals": {"Residential": grid.get("Residential/Group Housing"), "Mixed": grid.get("Mixed Development")},
        "2_projects_successfully_scraped": int(crawled.sum()),
        "3_failed_projects": int(failed_projects),
        "3a_projects_not_yet_crawled": int((~crawled).sum()),
        "4_missing_fields_among_scraped_projects": missing,
        "5_total_developers": _q(conn, "SELECT COUNT(*) FROM developers"),
        "5a_developers_with_projects_in_scope": int(proj["developer_id"].nunique()),
        "6_total_complaints": _q(conn, "SELECT COUNT(*) FROM complaints"),
        "6a_complaints_linked_to_in_scope_projects": _q(conn, "SELECT COUNT(*) FROM complaints WHERE project_id IS NOT NULL"),
        "7_total_regulatory_actions": _q(conn, "SELECT COUNT(*) FROM regulatory_actions"),
        "7a_regulatory_actions_linked_to_in_scope_projects": _q(conn, "SELECT COUNT(*) FROM regulatory_actions WHERE project_id IS NOT NULL"),
        "7b_appeals": _q(conn, "SELECT COUNT(*) FROM appeals"),
        "8_total_extensions": _q(conn, "SELECT COUNT(*) FROM project_extensions WHERE status='approved'"),
        "8a_projects_with_extensions": int((proj["extension_count"].fillna(0) > 0).sum()),
        "9_total_documents": docs,
        "10_data_coverage_pct": {
            "projects_detail_crawled": round(crawled.mean() * 100, 2) if len(proj) else None,
            **{f"{f}": round(100 - missing[f] / max(int(crawled.sum()), 1) * 100, 2) for f in missing},
        },
        "11_duplicates": {
            "duplicate_project_ids_in_lists": sum(c - 1 for c in list_ids.values() if c > 1),
            "duplicate_reg_numbers_in_lists": sum(c - 1 for c in list_regs.values() if c > 1),
            "complaint_list_rows_merged_as_duplicates": sum(c - 1 for c in complaint_keys.values() if c > 1),
        },
        "12_conflicting_records": {
            "conflict_rows": _q(conn, "SELECT COUNT(*) FROM field_conflicts"),
            "projects_with_conflicts": _q(conn, "SELECT COUNT(DISTINCT entity_id) FROM field_conflicts"),
            "by_field": dict(conn.execute("SELECT field, COUNT(*) FROM field_conflicts GROUP BY field ORDER BY 2 DESC LIMIT 15").fetchall()),
        },
        "13_known_limitations": LIMITATIONS,
        "status_derived_counts": {str(k): int(v) for k, v in status_counts.items()},
        "data_quality_notes": [r[0] for r in conn.execute("SELECT note FROM data_quality_notes WHERE scope='universe'")],
    }
    summary["14_example_developer_analysis"] = example_developer(res)
    summary["15_example_project_analysis"] = example_project(res)
    return summary


def example_developer(res: dict) -> dict | None:
    d = res["developers"]
    if d is None or not len(d):
        return None
    d = d.sort_values(["total_projects", "total_units"], ascending=False)
    r = d.iloc[0].to_dict()
    keys = ["developer_id", "canonical_name", "entity_type", "total_projects", "residential_projects", "mixed_projects",
            "completed_projects", "ongoing_projects", "extended_projects", "overdue_projects", "completion_rate_pct",
            "avg_completion_pct", "units_weighted_completion_pct", "total_units", "booked_units", "unbooked_units",
            "sold_pct", "projects_with_extensions", "total_extensions", "complaints_total", "complaints_pending",
            "suo_motu_total", "challans_total", "challan_amount_total", "risk_score", "score_coverage", "flag_counts"]
    return {k: _plain(r.get(k)) for k in keys if k in r}


def example_project(res: dict) -> dict | None:
    p = res["projects"]
    p = p[p["risk_score"].notna() & (p["detail_crawled"] == 1)]
    if not len(p):
        return None
    r = p.sort_values("risk_score", ascending=False).iloc[0].to_dict()
    keys = ["project_id", "reg_no_current", "project_name", "developer_name", "district", "status_source",
            "status_derived", "status_derived_basis", "original_completion_date", "current_completion_date",
            "extension_count", "days_beyond_current", "latest_progress_pct", "latest_progress_quarter",
            "timeline_elapsed_pct", "months_since_last_qpr", "total_units", "booked_units", "unbooked_units", "sales_pct",
            "complaints_total", "complaints_pending", "suo_motu_total", "challans_total", "risk_score", "risk_delay",
            "risk_regulatory", "risk_complaints", "risk_completion", "risk_extensions", "risk_stagnation", "risk_flags"]
    return {k: _plain(r.get(k)) for k in keys if k in r}


def _plain(v):
    if isinstance(v, float) and v != v:
        return None
    if hasattr(v, "item"):
        return v.item()
    return v


# ---------------------------------------------------------------------------------------------- rendering
def _table(df: pd.DataFrame, cols: list[str], headers: list[str] | None = None) -> str:
    headers = headers or cols
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for r in df[cols].itertuples(index=False):
        cells = []
        for v in r:
            if v is None or (isinstance(v, float) and v != v):
                cells.append("—")
            elif isinstance(v, float):
                cells.append(f"{v:,.1f}")
            elif isinstance(v, int):
                cells.append(f"{v:,}")
            else:
                cells.append(str(v).replace("|", "/"))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def render_markdown(conn: sqlite3.Connection, res: dict, s: dict) -> str:
    p: pd.DataFrame = res["projects"]
    d: pd.DataFrame = res["developers"]
    crawled = p[p["detail_crawled"] == 1]
    inv = crawled[crawled["booked_units"].notna() & crawled["unbooked_units"].notna()]
    booked, unbooked = int(inv["booked_units"].sum()), int(inv["unbooked_units"].sum())
    st = crawled["status_derived"].value_counts()
    out = [f"# GujRERA Residential & Mixed-use Projects — Executive Report",
           f"*Run date {res['run_date']} · source: public GujRERA portal APIs · "
           f"{s['2_projects_successfully_scraped']:,} of {s['1_total_projects_discovered']:,} projects with full detail.*",
           "",
           "> Risk figures are an **Analytical Risk Score computed by this pipeline — not an official RERA rating** "
           "and not a legal finding. Every flag below lists its evidence.",
           "", "## 1. Gujarat overview", ""]
    ov = pd.DataFrame([
        ("Projects discovered (Residential + Mixed)", f"{s['1_total_projects_discovered']:,}"),
        ("Residential / Mixed", f"{int((p['project_category']=='residential').sum()):,} / {int((p['project_category']=='mixed').sum()):,}"),
        ("Projects with full detail crawled", f"{len(crawled):,}"),
        ("Completed (Q-E/Form 4 filed or portal Completed)", f"{int(st.get('completed', 0)):,}"),
        ("Ongoing within original timeline", f"{int(st.get('ongoing', 0)):,}"),
        ("Extended and within extended timeline", f"{int(st.get('extended', 0)):,}"),
        ("Overdue (past latest approved date, no completion filing)", f"{int(st.get('overdue', 0)):,}"),
        ("Projects with ≥1 extension", f"{int((crawled['extension_count'].fillna(0) > 0).sum()):,}"),
        ("Stalled (progress flat / no QPR)", f"{int((crawled['stalled_flag'] == 1).sum()):,}"),
        ("Total units (declared)", f"{int(crawled['total_units'].dropna().sum()):,}"),
        ("Booked (sold) / unbooked (unsold) units", f"{booked:,} / {unbooked:,}"),
        ("Sold % / unsold %", f"{booked / max(booked + unbooked, 1) * 100:.1f}% / {unbooked / max(booked + unbooked, 1) * 100:.1f}%"),
        ("Complaints linked to these projects", f"{int(crawled['complaints_total'].fillna(0).sum()):,}"),
        ("Suo-motu proceedings linked", f"{int(crawled['suo_motu_total'].fillna(0).sum()):,}"),
        ("Challans (penalties) / amount", f"{int(crawled['challans_total'].fillna(0).sum()):,} / Rs {crawled['challan_amount_total'].fillna(0).sum():,.0f}"),
    ], columns=["Metric", "Value"])
    out += [_table(ov, ["Metric", "Value"]), ""]

    if len(d):
        out += ["## 2. Top developers", "",
                "Developer = GujRERA promoter account. Rankings include developers with ≥ 3 projects where relevant.", ""]
        dd = d.copy()
        base = ["canonical_name", "total_projects"]
        rankings = [
            ("By total projects", "total_projects", base + ["completed_projects", "overdue_projects", "total_units"]),
            ("By completed projects", "completed_projects", base + ["completed_projects", "completion_rate_pct"]),
            ("By active (ongoing/extended/overdue) projects", "active_projects", base + ["active_projects", "overdue_projects"]),
            ("By overdue projects", "overdue_projects", base + ["overdue_projects", "avg_delay_days_of_late_projects"]),
            ("By total units", "total_units", base + ["total_units", "booked_units", "unbooked_units", "sold_pct"]),
            ("By unsold units", "unbooked_units", base + ["unbooked_units", "unsold_pct"]),
            ("By extensions", "total_extensions", base + ["total_extensions", "projects_with_repeated_extensions"]),
            ("By complaints", "complaints_total", base + ["complaints_total", "complaints_pending", "complaint_to_project_ratio"]),
            ("By regulatory actions (suo-motu)", "suo_motu_total", base + ["suo_motu_total", "challans_total", "challan_amount_total"]),
        ]
        for title, key, cols in rankings:
            t = dd.sort_values([key, "total_projects"], ascending=False).head(10)
            out += [f"### {title}", "", _table(t, cols), ""]
        rated = dd[(dd["total_projects"] >= 3) & dd["completion_rate_pct"].notna()]
        if len(rated):
            out += ["### Highest completion rate (≥ 3 projects)", "",
                    _table(rated.sort_values(["completion_rate_pct", "total_projects"], ascending=False).head(10),
                           base + ["completed_projects", "completion_rate_pct", "avg_completion_pct"]), ""]
        risky = dd[(dd["total_projects"] >= 3) & dd["risk_score"].notna()].sort_values("risk_score", ascending=False).head(15)
        if len(risky):
            out += ["### Highest Analytical Risk Score (≥ 3 projects)", "",
                    _table(risky, base + ["risk_score", "risk_delay", "risk_regulatory", "risk_complaints",
                                          "risk_completion", "risk_extensions", "risk_stagnation", "score_coverage"]), ""]

    hr = crawled[crawled["risk_flags"].apply(lambda f: isinstance(f, list) and len(f) > 0)]
    hr = hr.sort_values("risk_score", ascending=False).head(25)
    out += ["## 3. High-risk projects (evidence-backed flags)", ""]
    for r in hr.itertuples(index=False):
        out.append(f"**{r.project_name}** — `{r.reg_no_current}` · {r.developer_name} · {r.district} · "
                   f"status: {r.status_derived} · risk score {r.risk_score}")
        for f in r.risk_flags:
            out.append(f"- *{f['flag']}*: {f['evidence']}")
        out.append("")

    out += ["## 4. Data coverage & quality", "",
            "```json", json.dumps({k: s[k] for k in s if k[0].isdigit() and not k.startswith(("13", "14", "15"))},
                                  indent=2, default=str), "```", "",
            "### Known limitations", ""] + [f"- {x}" for x in s["13_known_limitations"]] + [
            "", "## 5. How the metrics are calculated", "",
            "See `docs/data_dictionary.md` (fields & formulas) and `docs/risk_methodology.md` (score). Summary:", "",
            "- **Sales %** = booked ÷ (booked + unbooked) × 100 from the GujRERA block booking chart.",
            "- **Completion %** = latest physical progress reported in the QPR history (quarter/get-project-progress).",
            "- **Completed** = Q-E (project end) filing with Form 4 submitted, or portal status Completed.",
            "- **Overdue** = run date past the latest approved completion date with no completion filing.",
            "- **Extensions** = approved 'Extension' applications (and EXn markers in the registration number).",
            "- **Delay days** = days past the latest approved (or original) completion date — at Q-E filing for "
            "completed projects, at run date otherwise.",
            "- **Complaints** = complaint applications on the project + published judgements joined by acknowledgement "
            "number / registration serial; **suo-motu** and **challans** are authority actions.",
            ]
    return "\n".join(out)


def render_html(md: str) -> str:
    """Minimal Markdown -> HTML (headings, tables, lists, code blocks) without external dependencies."""
    lines, out, in_table, in_code, in_list = md.split("\n"), [], False, False, False
    for ln in lines:
        if ln.startswith("```"):
            out.append("</pre>" if in_code else "<pre>")
            in_code = not in_code
            continue
        if in_code:
            out.append(html.escape(ln))
            continue
        if ln.startswith("|"):
            cells = [c.strip() for c in ln.strip("|").split("|")]
            if all(set(c) <= {"-"} for c in cells):
                continue
            if not in_table:
                out.append("<table>")
                out.append("<tr>" + "".join(f"<th>{html.escape(c)}</th>" for c in cells) + "</tr>")
                in_table = True
            else:
                out.append("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in cells) + "</tr>")
            continue
        if in_table:
            out.append("</table>")
            in_table = False
        if ln.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(ln[2:])}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if ln.startswith("#"):
            n = len(ln) - len(ln.lstrip("#"))
            out.append(f"<h{n}>{_inline(ln[n:].strip())}</h{n}>")
        elif ln.startswith("> "):
            out.append(f"<blockquote>{_inline(ln[2:])}</blockquote>")
        elif ln.strip():
            out.append(f"<p>{_inline(ln)}</p>")
    css = ("body{font-family:Segoe UI,Arial,sans-serif;max-width:1200px;margin:24px auto;padding:0 16px;color:#1d2433}"
           "table{border-collapse:collapse;margin:8px 0 20px;font-size:13px}td,th{border:1px solid #d5d9e0;"
           "padding:4px 8px;text-align:left}th{background:#f2f4f7}blockquote{border-left:4px solid #c77700;"
           "background:#fff7e6;margin:12px 0;padding:8px 12px}pre{background:#f6f8fa;padding:12px;overflow:auto;"
           "font-size:12px}code{background:#f2f4f7;padding:1px 4px}")
    return f"<!doctype html><html><head><meta charset='utf-8'><title>GujRERA Executive Report</title><style>{css}</style>" \
           f"</head><body>{''.join(out)}</body></html>"


def _inline(s: str) -> str:
    import re
    s = html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"\*(.+?)\*", r"<i>\1</i>", s)
    return re.sub(r"`(.+?)`", r"<code>\1</code>", s)
