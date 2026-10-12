"""`cli.py export`: CSVs, Excel workbook, executive report (Markdown + HTML), run summary JSON."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

from analytics.run import build
from exports import report as report_mod

CSV_QUERIES = {
    # 3. project-developer relationship
    "project_developers": """SELECT pd.project_id, p.reg_no_current, p.project_name, pd.developer_id,
                                    d.canonical_name AS developer_name, pd.role, pd.source_endpoint
                             FROM project_developers pd LEFT JOIN projects p USING(project_id)
                             LEFT JOIN developers d USING(developer_id)""",
    # 4. inventory: block level + unit-type level
    "inventory_blocks": """SELECT b.*, p.reg_no_current, p.project_name FROM project_blocks b
                           LEFT JOIN projects p USING(project_id)""",
    "inventory_unit_types": """SELECT u.*, p.reg_no_current, p.project_name FROM project_units u
                               LEFT JOIN projects p USING(project_id)""",
    # 5. progress history + QPR filing calendar
    "progress_history": """SELECT g.*, p.reg_no_current, p.project_name FROM project_progress g
                           LEFT JOIN projects p USING(project_id) ORDER BY g.project_id, g.seq""",
    "qpr_filings": """SELECT q.*, p.project_name FROM project_quarters q LEFT JOIN projects p USING(project_id)
                      ORDER BY q.project_id, q.period_start""",
    # 6-7. complaints, regulatory actions, appeals
    "complaints": """SELECT c.*, p.reg_no_current, p.project_name AS project_name_matched FROM complaints c
                     LEFT JOIN projects p USING(project_id)""",
    "regulatory_actions": """SELECT a.*, p.reg_no_current, p.project_name FROM regulatory_actions a
                             LEFT JOIN projects p USING(project_id)""",
    "appeals": "SELECT * FROM appeals",
    # 8. extensions
    "extensions": """SELECT e.*, p.reg_no_current, p.project_name, p.original_completion_date,
                            p.current_completion_date FROM project_extensions e LEFT JOIN projects p USING(project_id)
                     ORDER BY e.project_id, e.extension_seq""",
    # 9. documents
    "documents": """SELECT document_uid AS document_id, document_id AS vdms_numeric_id, project_id, related_key,
                           document_type, document_name, document_date, source_endpoint, download_url, file_type,
                           download_priority, download_status, local_path, size_bytes, sha256, text_status, text_path,
                           parsed_fields, downloaded_at FROM project_documents""",
    "applications": "SELECT * FROM project_applications",
    "annual_compliance": "SELECT * FROM annual_compliance",
    "developer_aliases": "SELECT * FROM developer_aliases",
    "developer_groups": """SELECT g.*, d.canonical_name FROM developer_groups g LEFT JOIN developers d USING(developer_id)
                           ORDER BY group_id""",
    "field_conflicts": "SELECT * FROM field_conflicts",
    "field_provenance": "SELECT * FROM field_provenance",
    "data_quality_notes": "SELECT * FROM data_quality_notes",
}

PROJECT_COLUMNS = [
    "project_id", "reg_no_current", "reg_no_original", "reg_serial", "ack_no", "project_name", "project_type",
    "project_category", "affordable_housing", "status_source", "status_derived", "status_derived_basis",
    "portal_progress_label", "developer_id", "developer_name", "district", "taluka", "registration_date",
    "project_start_date", "original_completion_date", "current_completion_date", "completion_filing_date",
    "completion_filing_status", "extension_count", "alteration_count", "total_extension_days",
    "days_beyond_original", "days_beyond_current", "latest_progress_pct", "completion_pct", "previous_progress_pct",
    "latest_progress_quarter", "progress_velocity_pp_per_qtr", "months_since_last_qpr", "stalled_flag",
    "construction_complete_per_qpr", "financial_progress_pct", "timeline_elapsed_pct", "total_units",
    "total_units_state", "booked_units", "unbooked_units", "inventory_state", "sales_pct", "unsold_pct",
    "residential_units", "commercial_units", "other_units", "total_blocks", "parking_for_sale", "total_land_area_sqm",
    "total_carpet_area_sqm", "min_unit_cost", "max_unit_cost", "total_project_cost", "approving_authority",
    "complaints_total", "complaints_pending", "complaints_resolved", "suo_motu_total", "suo_motu_penalty_orders",
    "complaint_penalty_orders", "challans_total", "challan_amount_total", "qpr_defaults", "detail_crawled",
    "risk_score", "score_coverage", "risk_delay", "risk_regulatory", "risk_complaints", "risk_completion",
    "risk_extensions", "risk_stagnation", "risk_inventory", "risk_flags", "first_seen_at", "last_seen_at",
    "last_changed_at", "last_scraped_at",
]


def export_all(conn: sqlite3.Connection, settings: dict, out_dir: str | None = None) -> dict:
    out = Path(out_dir or settings["exports_dir"])
    csv_dir, xl_dir, rep_dir = out / "csv", out / "excel", out / "reports"
    for d in (csv_dir, xl_dir, rep_dir):
        d.mkdir(parents=True, exist_ok=True)
    res = build(conn, settings)
    proj = res["projects"].copy()
    proj["risk_flags"] = proj["risk_flags"].apply(lambda f: "; ".join(x["flag"] for x in f) if isinstance(f, list) else None)
    proj_out = proj[[c for c in PROJECT_COLUMNS if c in proj.columns]]
    devs = res["developers"].copy()
    if "flag_counts" in devs:
        devs["flag_counts"] = devs["flag_counts"].apply(lambda d: json.dumps(d) if isinstance(d, dict) else None)
    aliases = pd.read_sql_query("SELECT developer_id, GROUP_CONCAT(DISTINCT alias) AS aliases FROM developer_aliases "
                                "GROUP BY developer_id", conn)
    dev_master = pd.read_sql_query("""SELECT developer_id, legal_name, entity_type, registration_status,
                                      company_reg_number, registered_address, city, district, state, pincode, email,
                                      phone, website, declared_group_name, declared_total_projects,
                                      declared_completed_projects, associates_count, associate_names, source_endpoint
                                      FROM developers""", conn)
    devs = devs.drop(columns=[c for c in ("entity_type", "declared_group_name") if c in devs])
    dev_out = dev_master.merge(devs, on="developer_id", how="left").merge(aliases, on="developer_id", how="left")
    files = {}
    # 1. project master, 2. developer master
    files["projects_master"] = _csv(proj_out, csv_dir / "projects_master.csv")
    files["developers_master"] = _csv(dev_out, csv_dir / "developers_master.csv")
    if len(res["developer_groups"]):
        files["developer_group_metrics"] = _csv(res["developer_groups"], csv_dir / "developer_group_metrics.csv")
    frames = {"projects_master": proj_out, "developers_master": dev_out}
    for name, q in CSV_QUERIES.items():
        df = pd.read_sql_query(q, conn)
        files[name] = _csv(df, csv_dir / f"{name}.csv")
        frames[name] = df
    # Excel (large provenance / document tables are CSV-only)
    xl_path = xl_dir / "gujrera_dataset.xlsx"
    with pd.ExcelWriter(xl_path, engine="openpyxl") as xw:
        _readme_sheet().to_excel(xw, sheet_name="README", index=False)
        for name in ["projects_master", "developers_master", "project_developers", "inventory_blocks",
                     "inventory_unit_types", "progress_history", "extensions", "complaints", "regulatory_actions",
                     "appeals", "developer_groups", "field_conflicts", "data_quality_notes"]:
            df = frames[name]
            if len(df) < 1_000_000:
                df.to_excel(xw, sheet_name=name[:31], index=False)
    files["excel"] = str(xl_path)
    summary = report_mod.run_summary(conn, res, settings)
    (rep_dir / "run_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    md = report_mod.render_markdown(conn, res, summary)
    (rep_dir / "executive_report.md").write_text(md, encoding="utf-8")
    (rep_dir / "executive_report.html").write_text(report_mod.render_html(md), encoding="utf-8")
    files["report_md"] = str(rep_dir / "executive_report.md")
    files["report_html"] = str(rep_dir / "executive_report.html")
    files["run_summary"] = str(rep_dir / "run_summary.json")
    return {"files": files, "summary": summary}


def _csv(df: pd.DataFrame, path: Path) -> str:
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return f"{path} ({len(df)} rows)"


def _readme_sheet() -> pd.DataFrame:
    return pd.DataFrame({"note": [
        "GujRERA Residential + Mixed-use dataset — built from public GujRERA endpoints (see docs/).",
        "booked_units / unbooked_units are the portal's booking figures (used as sold / unsold).",
        "Empty cell = data not available from the source (NULL). 0 = explicitly reported zero.",
        "Counts of complaints/actions are empty for projects whose detail was not crawled (detail_crawled = 0).",
        "risk_* columns: Analytical Risk Score computed by this pipeline — NOT an official RERA rating.",
        "status_derived rules: docs/data_dictionary.md. Conflicting source values: sheet field_conflicts.",
        "Complainant names are not stored (salted hash only).",
    ]})
