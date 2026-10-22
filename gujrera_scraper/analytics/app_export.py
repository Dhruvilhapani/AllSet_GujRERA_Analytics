"""Publish the read-only "app snapshot" SQLite file used by the web app (../webapp).

The web app never touches the crawler database. This module writes a small, self-describing file (replaced
atomically) with per-district developer and project read models computed with the same logic as the analytical
reports (analytics/area_report.py), so the app and the reports always agree.

    python cli.py refresh-app --district Ahmedabad [--every 30]   # copy live DB -> parse -> publish
    python cli.py publish-app --district Ahmedabad                # publish from an already-parsed DB

Conventions carried into the file:
  * NULL = not available / not crawled. 0 = reported or checked zero. Never the other way round.
  * Counts of complaints / actions are NULL for projects whose detail is not crawled yet (detail_crawled = 0).
  * developer_id = the lowest GujRERA promoter id of the developer entity (promoter accounts are merged only on
    identical PAN or a valid CIN/LLPIN).
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from analytics import area_report as ar
from database.db import RawStore, utcnow

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE developers (
    developer_id INTEGER PRIMARY KEY, name TEXT NOT NULL, name_search TEXT, entity_type TEXT,
    promoter_account_ids TEXT, merge_basis TEXT, declared_group_name TEXT, aliases TEXT, similar_names TEXT,
    projects_total INTEGER, projects_analyzed INTEGER, residential_projects INTEGER, mixed_projects INTEGER,
    commercial_projects INTEGER, plotted_projects INTEGER,
    completed INTEGER, ongoing INTEGER, delayed INTEGER, pending INTEGER, past_original INTEGER, overdue INTEGER,
    stalled INTEGER,
    near_completion INTEGER, extended_projects INTEGER, total_extensions INTEGER, repeated_extensions INTEGER,
    avg_extension_days REAL, max_extension_days REAL, avg_delay_days REAL, max_delay_days REAL,
    units INTEGER, projects_with_unit_data INTEGER, sold INTEGER, unsold INTEGER, sales_pct REAL,
    avg_progress_pct REAL, median_progress_pct REAL, projects_with_progress_data INTEGER, completion_rate_pct REAL,
    delay_rate_pct REAL, complaints_total INTEGER, complaints_pending INTEGER, complaints_resolved INTEGER,
    rera_complaints INTEGER, adjudication_complaints INTEGER, suo_motu INTEGER, penalty_orders INTEGER,
    challans INTEGER, challan_amount REAL, regulatory_actions INTEGER, localities TEXT, talukas TEXT,
    first_registration_year INTEGER, last_registration_year INTEGER,
    -- company details of the primary promoter account (business information only: no phone, e-mail, PAN, Aadhaar)
    registration_status TEXT, company_reg_number TEXT, date_of_incorporation TEXT, registered_address TEXT,
    website TEXT, declared_total_projects INTEGER, declared_completed_projects INTEGER,
    declared_ongoing_projects INTEGER, declared_experience_years TEXT, associates TEXT
);
CREATE TABLE projects (
    project_id INTEGER PRIMARY KEY, developer_id INTEGER, name TEXT, name_search TEXT, rera_number TEXT,
    category TEXT, project_type TEXT, district TEXT, taluka TEXT, locality TEXT, locality_source TEXT,
    address TEXT, pincode TEXT, latitude REAL, longitude REAL, approving_authority TEXT,
    status TEXT, status_detail TEXT, status_basis TEXT, portal_status TEXT, portal_progress_label TEXT,
    registration_date TEXT, start_date TEXT, original_completion_date TEXT, current_completion_date TEXT,
    completion_filing_date TEXT, extension_count INTEGER, total_extension_days INTEGER,
    days_beyond_original INTEGER, days_beyond_current INTEGER, progress_pct REAL, progress_quarter TEXT,
    months_since_last_qpr REAL, stalled INTEGER, financial_progress_pct REAL, total_units INTEGER,
    sold_units INTEGER, unsold_units INTEGER, sales_pct REAL, residential_units INTEGER, commercial_units INTEGER,
    total_land_area_sqm REAL, total_carpet_area_sqm REAL, total_project_cost REAL, min_unit_cost REAL,
    max_unit_cost REAL, complaints_total INTEGER, complaints_pending INTEGER, suo_motu INTEGER, challans INTEGER,
    challan_amount REAL, detail_crawled INTEGER, source_url TEXT, last_checked TEXT,
    city TEXT, unit_data_state TEXT, unit_data_note TEXT, units_row_count INTEGER, units_as_of TEXT,
    units_scraped_at TEXT, total_open_area_sqm REAL, total_covered_area_sqm REAL, parking_for_sale INTEGER,
    covered_parking INTEGER, garages_for_sale INTEGER,
    project_description TEXT, planning_authority TEXT, tp_number TEXT, tp_name TEXT, fp_number TEXT,
    survey_number TEXT, city_survey_number TEXT, village_moje TEXT,
    last_rera_update TEXT, last_rera_update_kind TEXT, portal_last_submitted_at TEXT,
    first_seen_at TEXT, last_changed_at TEXT
);
CREATE INDEX ix_projects_developer ON projects(developer_id);
CREATE INDEX ix_projects_city_category ON projects(city, category, status);
CREATE INDEX ix_projects_locality ON projects(city, locality);
CREATE TABLE meta_counts (city TEXT, category TEXT, total INTEGER, analyzed INTEGER, PRIMARY KEY (city, category));
CREATE TABLE project_progress (project_id INTEGER, seq INTEGER, quarter_label TEXT, period_date TEXT,
                               progress_pct REAL, PRIMARY KEY (project_id, seq));
-- sold_units / unsold_units = the portal's block booking chart (QPR-fed). unit_* = counted from the unit rows of the
-- latest Form 3. The two sources can disagree; both are published, never merged.
CREATE TABLE project_blocks (project_id INTEGER, block_name TEXT, planned_units INTEGER, sold_units INTEGER,
                             unsold_units INTEGER, progress_pct REAL, unit_rows INTEGER, unit_booked INTEGER,
                             unit_unbooked INTEGER, unit_other INTEGER, fetch_outcome TEXT, height REAL, fsi REAL,
                             builtup_area_sqm REAL, dev_start_date TEXT, dev_end_date TEXT, floors INTEGER, lifts INTEGER,
                             PRIMARY KEY (project_id, block_name));
CREATE TABLE project_form3 (project_id INTEGER, form_three_id INTEGER, submitted_on TEXT, number_of_units INTEGER,
                            booked_declared INTEGER, unbooked_declared INTEGER, total_residential INTEGER,
                            total_shop INTEGER, total_office INTEGER, total_others INTEGER, is_latest INTEGER,
                            PRIMARY KEY (project_id, form_three_id));
-- Facilities / amenities declared in Form 1: applicable YES/NO, % work done, planned / actual dates. A facility that is
-- not listed was not declared (not "absent").
CREATE TABLE project_amenities (project_id INTEGER, item_key TEXT, label TEXT, category TEXT, source TEXT,
                                applicable TEXT, progress_pct REAL, planned_start TEXT, planned_end TEXT,
                                actual_start TEXT, actual_end TEXT, remarks TEXT, form1_submitted_on TEXT,
                                PRIMARY KEY (project_id, item_key));
CREATE TABLE project_infrastructure (project_id INTEGER, category TEXT, name TEXT, value TEXT,
                                     PRIMARY KEY (project_id, category, name));
CREATE TABLE project_documents (project_id INTEGER, document_type TEXT, document_name TEXT, document_date TEXT,
                                url TEXT);
CREATE INDEX ix_docs_project ON project_documents(project_id);
CREATE TABLE project_polygon (project_id INTEGER, seq INTEGER, lat REAL, lng REAL, PRIMARY KEY (project_id, seq));
CREATE TABLE project_professionals (project_id INTEGER, role TEXT, name TEXT);
CREATE INDEX ix_prof_project ON project_professionals(project_id);
CREATE TABLE project_legal (project_id INTEGER, record_type TEXT, reference TEXT, category TEXT, subtype TEXT,
                            filed_date TEXT, order_date TEXT, status TEXT, outcome TEXT, penalty_amount REAL,
                            order_url TEXT);
CREATE INDEX ix_legal_project ON project_legal(project_id);
CREATE TABLE project_events (project_id INTEGER, event_date TEXT, event_type TEXT, title TEXT, detail TEXT,
                             reference TEXT);
CREATE INDEX ix_events_project ON project_events(project_id);
CREATE TABLE developer_registrations (developer_id INTEGER, year INTEGER, projects INTEGER, units INTEGER,
                                      PRIMARY KEY (developer_id, year));
CREATE TABLE project_extensions (project_id INTEGER, seq INTEGER, extension_reg_no TEXT, submitted_on TEXT,
                                 approved_on TEXT, status TEXT, previous_completion_date TEXT,
                                 extended_completion_date TEXT, extension_days INTEGER,
                                 PRIMARY KEY (project_id, seq));
-- One row per unit of the latest Form 3 (is_current = 1): the Unit Wise Booking Status. No personal data, no amounts.
CREATE TABLE unit_inventory (
    project_id INTEGER NOT NULL, block_name TEXT NOT NULL, unit_no TEXT, unit_no_norm TEXT NOT NULL,
    dup_seq INTEGER NOT NULL, unit_sort TEXT, unit_kind TEXT, usage TEXT, usage_class TEXT, carpet_area_sqm REAL,
    area_excl_balcony_terrace_sqm REAL, status TEXT, status_norm TEXT, agreement_date TEXT, encumbrance_status TEXT,
    redeveloped TEXT, form3_submitted_on TEXT, last_changed_at TEXT,
    PRIMARY KEY (project_id, block_name, unit_no_norm, dup_seq)
) WITHOUT ROWID;
CREATE INDEX ix_unit_sort ON unit_inventory(project_id, block_name, unit_sort, dup_seq);
"""

# execution_category -> normalized status shown in the app. Delayed means past the latest approved completion
# date without a completion filing; a stalled project still inside its approved date stays Ongoing (stalled flag);
# a project with no quarterly progress report yet is Pending. Extensions are a separate attribute, not a status.
STATUS_MAP = {
    "Completed": "Completed", "Overdue": "Delayed", "Stalled / No Recent Progress": "Ongoing",
    "Near Completion": "Ongoing", "Under Construction": "Ongoing", "Early Stage": "Ongoing",
    "Unknown (no progress data)": "Pending", "Cancelled/Revoked": "Cancelled",
}
DOC_URL = "https://gujrera.gujarat.gov.in/vdms/download/"
DETAIL_ONLY = ("extension_count", "total_extension_days", "days_beyond_original", "days_beyond_current")
DEV_ANALYSED_ONLY = ("completed", "ongoing", "delayed", "overdue", "stalled", "near_completion", "total_extensions",
                     "repeated_extensions", "avg_extension_days", "max_extension_days", "avg_delay_days",
                     "max_delay_days", "complaints_total", "complaints_pending", "complaints_resolved",
                     "rera_complaints", "adjudication_complaints", "penalty_orders", "challans", "challan_amount",
                     "regulatory_actions")


def _v(x):
    """numpy / NaN -> plain Python / None."""
    if x is None:
        return None
    if hasattr(x, "item"):
        x = x.item()
    if isinstance(x, float) and x != x:
        return None
    return x


def _insert(o: sqlite3.Connection, table: str, row: dict) -> None:
    o.execute(f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' * len(row))})", list(row.values()))


def _status(r) -> str:
    if not r.get("detail_crawled"):
        return "Not crawled"
    return STATUS_MAP.get(r.get("execution_category"), "Unknown")


def crawl_status(conn: sqlite3.Connection) -> dict:
    run = conn.execute("SELECT started_at, finished_at FROM crawl_runs ORDER BY started_at DESC LIMIT 1").fetchone()
    last = conn.execute("SELECT MAX(fetched_at) FROM raw_responses").fetchone()[0]
    recent = bool(last) and datetime.fromisoformat(last) > datetime.now(timezone.utc) - timedelta(minutes=30)
    if run is None:
        state = "not_started"
    elif run[1]:
        state = "completed"
    else:
        state = "running" if recent else "paused"
    return {"state": state, "last_fetch": last}


def publish(conn: sqlite3.Connection, settings: dict, district: str | list[str], out_path: str | Path,
            live_status: dict | None = None) -> dict:
    """`district`: one district name or a list (e.g. ['Ahmedabad', 'Gandhinagar'] -> one merged snapshot)."""
    districts = [district] if isinstance(district, str) else list(district)
    p, _res = ar.scope_projects(conn, settings, districts)
    p["city"] = p["district"].map(lambda x: " ".join(str(x).split()).title() if isinstance(x, str) and x.strip() else None)
    entity, devs, review = ar.resolve_entities(conn, set(p["developer_id"].dropna().astype(int)))
    p["entity_id"] = p["developer_id"].map(lambda x: entity.get(int(x)) if pd.notna(x) else None)
    d = ar.developer_table(p, devs, conn)
    p = p.merge(d[["entity_id", "developer"]], on="entity_id", how="left")
    p["app_developer_id"] = p["entity_id"].map(lambda e: int(e[1:]) if isinstance(e, str) else None)
    p["status"] = p.apply(_status, axis=1)
    p["reg_year"] = pd.to_datetime(p["registration_date"], errors="coerce").dt.year
    ids = [int(x) for x in p["project_id"]]
    idlist = ",".join(map(str, ids)) or "0"
    # legal records / timeline only for analysed projects, so lists always agree with the (checked) counts
    crawled_list = ",".join(str(int(x)) for x in p.loc[p["detail_crawled"] == 1, "project_id"]) or "0"

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp")
    if tmp.exists():
        tmp.unlink()
    o = sqlite3.connect(tmp)
    o.executescript(SCHEMA)

    cr = p[p["detail_crawled"] == 1]
    status = live_status or crawl_status(conn)
    cities = sorted(c for c in p["city"].dropna().unique())
    present = [c for c in CATEGORY_ORDER if (p["project_category"] == c).any()]
    meta = {
        "district": " + ".join(cities) if cities else ", ".join(districts),
        "districts": json.dumps(cities), "categories": json.dumps(present),
        "generated_at": utcnow(), "data_checked_at": status.get("last_fetch"),
        "crawl_state": status["state"], "projects_total": len(p), "projects_analyzed": len(cr),
        "projects_pending": len(p) - len(cr), "dataset_complete": int(len(cr) == len(p)),
        "residential_total": int((p["project_category"] == "residential").sum()),
        "mixed_total": int((p["project_category"] == "mixed").sum()),
        "commercial_total": int((p["project_category"] == "commercial").sum()),
        "plotted_total": int((p["project_category"] == "plotted").sum()),
        "developers_identified": int(cr["entity_id"].nunique()),
        "scope": "GujRERA registered projects: " + ", ".join(CATEGORY_LABELS[c] for c in present),
        "source": "https://gujrera.gujarat.gov.in/ (public project records)",
    }
    o.executemany("INSERT INTO meta VALUES (?,?)", [(k, None if v is None else str(v)) for k, v in meta.items()])
    for (city, cat), g in p.groupby(["city", "project_category"]):
        o.execute("INSERT INTO meta_counts VALUES (?,?,?,?)", (city, cat, len(g), int((g["detail_crawled"] == 1).sum())))

    _write_developers(o, conn, p, d, entity, review)
    _write_projects(o, conn, p, ids)
    for r in conn.execute(f"SELECT project_id, seq, quarter_label, period_date, physical_progress_pct "
                          f"FROM project_progress WHERE project_id IN ({idlist})"):
        o.execute("INSERT INTO project_progress VALUES (?,?,?,?,?)", tuple(r))
    outcomes = {(r[0], r[1]): r[2] for r in conn.execute(
        f"SELECT project_id, block_name, outcome FROM project_block_fetch WHERE project_id IN ({idlist})")}
    for r in conn.execute(f"""SELECT project_id, block_name, planned_units, booked_units, unbooked_units, progress_pct,
                                     unit_rows, unit_booked, unit_unbooked, unit_other, height, fsi, builtup_area_sqm,
                                     dev_start_date, dev_end_date, floors, lifts FROM project_blocks
                              WHERE project_id IN ({idlist})"""):
        o.execute("INSERT OR IGNORE INTO project_blocks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  tuple(r[:10]) + (outcomes.get((r[0], r[1])),) + tuple(r[10:]))
    _write_unit_side_tables(o, conn, p, idlist)
    o.executemany(
        "INSERT OR IGNORE INTO project_extensions VALUES (?,?,?,?,?,?,?,?,?)",
        conn.execute(f"""SELECT project_id, extension_seq, extension_reg_no, submitted_on, approved_on, status,
                                previous_completion_date, extended_completion_date, extension_days
                         FROM project_extensions WHERE project_id IN ({crawled_list})""").fetchall(),
    )
    n_legal, n_events = _write_legal_and_events(o, conn, p, crawled_list)
    n_units = _write_units(o, conn, idlist)
    today = utcnow()[:10]
    o.execute("""UPDATE projects SET
        last_rera_update = (SELECT MAX(e.event_date) FROM project_events e
                            WHERE e.project_id = projects.project_id AND e.event_date <= ?),
        last_rera_update_kind = (SELECT e.event_type FROM project_events e
                                 WHERE e.project_id = projects.project_id AND e.event_date <= ?
                                 ORDER BY e.event_date DESC, e.rowid DESC LIMIT 1)""", (today, today))
    o.commit()
    o.close()
    _replace(tmp, out_path)
    return {**meta, "file": str(out_path), "unit_rows": n_units, "developers": len(d), "legal_records": n_legal,
            "events": n_events}


CATEGORY_ORDER = ("residential", "mixed", "commercial", "plotted")
CATEGORY_LABELS = {"residential": "Residential/Group Housing", "mixed": "Mixed Development",
                   "commercial": "Commercial", "plotted": "Plotted Development"}
# document types published to the app (official certificates / filings; the portal serves them publicly)
PUBLIC_DOC_TYPES = ("registration_certificate", "extension_certificate", "completion_certificate", "qe_form_four",
                    "qe_form_five", "project_cert_upload", "declaration_form_b", "engineer_certificate_form2")


def _write_unit_side_tables(o, conn, p, idlist) -> None:
    """Form 3 headers, infrastructure, professionals (role + name only), public documents and map polygons."""
    o.executemany("INSERT OR IGNORE INTO project_form3 VALUES (?,?,?,?,?,?,?,?,?,?,?)", conn.execute(
        f"""SELECT project_id, form_three_id, submitted_on, number_of_units, booked_declared, unbooked_declared,
                   total_residential, total_shop, total_office, total_others, is_latest
            FROM project_form3 WHERE project_id IN ({idlist})""").fetchall())
    o.executemany("INSERT OR IGNORE INTO project_amenities VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", conn.execute(
        f"""SELECT project_id, item_key, label, category, source, applicable, progress_pct, planned_start, planned_end,
                   actual_start, actual_end, remarks, form1_submitted_on
            FROM project_amenities WHERE project_id IN ({idlist})""").fetchall())
    o.executemany("INSERT OR IGNORE INTO project_infrastructure VALUES (?,?,?,?)", conn.execute(
        f"SELECT project_id, category, name, value FROM project_infrastructure WHERE project_id IN ({idlist})").fetchall())
    o.executemany("INSERT INTO project_professionals VALUES (?,?,?)", conn.execute(
        f"SELECT project_id, role, name FROM project_professionals WHERE project_id IN ({idlist})").fetchall())
    types = ",".join(f"'{t}'" for t in PUBLIC_DOC_TYPES)
    o.executemany("INSERT INTO project_documents VALUES (?,?,?,?,?)", conn.execute(
        f"""SELECT project_id, document_type, document_name, document_date, download_url FROM project_documents
            WHERE project_id IN ({idlist}) AND document_type IN ({types}) AND download_url IS NOT NULL""").fetchall())
    wanted = {int(x) for x in idlist.split(",") if x}
    from parser.common import to_float
    for eid, j, _meta in RawStore(conn).iter_endpoint("location"):
        if not eid.isdigit() or int(eid) not in wanted or not isinstance(j, dict):
            continue
        pts = [(to_float(c.get("lat")), to_float(c.get("lang"))) for c in j.get("coordinates") or []]
        pts = [(a, b) for a, b in pts if a and b and 20 < a < 25 and 68 < b < 75]   # Gujarat bounding box
        o.executemany("INSERT OR IGNORE INTO project_polygon VALUES (?,?,?,?)",
                      [(int(eid), i, a, b) for i, (a, b) in enumerate(pts)])


def natural_sort_key(label: str) -> str:
    """'a-101' < 'a-1001': digit runs are zero-padded so a plain TEXT ORDER BY sorts naturally."""
    import re
    return re.sub(r"\d+", lambda m: m.group().zfill(8), label or "")


def _write_units(o: sqlite3.Connection, conn, idlist: str) -> int:
    """Current unit rows (is_current = 1) of the published projects into the same snapshot file.
    No personal data and no amounts: the pipeline never stores them."""
    n = 0
    cur = conn.execute(f"""SELECT project_id, block_name, unit_no_raw, unit_no_norm, dup_seq, unit_kind, usage_raw,
                                  usage_class, carpet_area_sqm, area_excl_balcony_terrace_sqm, status_source,
                                  status_norm, agreement_date, encumbrance_status, redeveloped_flag,
                                  form3_submitted_on, last_changed_at
                           FROM project_unit_inventory WHERE is_current=1 AND project_id IN ({idlist})""")
    while True:
        rows = cur.fetchmany(20000)
        if not rows:
            break
        o.executemany("INSERT INTO unit_inventory VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      [(r[0], r[1], r[2], r[3], r[4], natural_sort_key(r[3])) + tuple(r[5:]) for r in rows])
        n += len(rows)
    return n


def _replace(tmp: Path, out_path: Path) -> None:
    """Atomic swap. On Windows a reader holding the file open blocks the rename briefly, so retry."""
    for attempt in range(20):
        try:
            os.replace(tmp, out_path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.5)


def _write_developers(o, conn, p, d, entity, review) -> None:
    acct_to_entity = {int(k): v for k, v in entity.items()}
    alias_map: dict[str, set] = {}
    for r in conn.execute("SELECT developer_id, alias FROM developer_aliases"):
        e = acct_to_entity.get(int(r[0]))
        if e:
            alias_map.setdefault(e, set()).add(" ".join(str(r[1]).split()))
    similar: dict[str, list] = {}
    for r in review.to_dict("records") if len(review) else []:
        e1, e2 = acct_to_entity.get(int(r["developer_id_1"])), acct_to_entity.get(int(r["developer_id_2"]))
        if e1 and e2:
            similar.setdefault(e1, []).append({"developer_id": int(e2[1:]), "name": r["name_2"], "reason": r["reason"]})
            similar.setdefault(e2, []).append({"developer_id": int(e1[1:]), "name": r["name_1"], "reason": r["reason"]})
    for r in d.to_dict("records"):
        g = p[p["entity_id"] == r["entity_id"]]
        yrs = g["reg_year"].dropna()
        analysed = bool(r["projects_analyzed"])
        name = " ".join(str(r["developer"]).split())
        row = {
            "developer_id": int(r["entity_id"][1:]), "name": name,
            "name_search": " ".join(sorted(alias_map.get(r["entity_id"], set()) | {name})).lower(),
            "entity_type": r["entity_type"],
            "promoter_account_ids": json.dumps([int(x) for x in str(r["promoter_account_ids"]).split(",") if x]),
            "merge_basis": r["merge_basis"], "declared_group_name": r["declared_group_name"],
            "aliases": json.dumps(sorted(alias_map.get(r["entity_id"], set()))),
            "similar_names": json.dumps(similar.get(r["entity_id"], [])),
            "projects_total": r["projects"], "projects_analyzed": r["projects_analyzed"],
            "residential_projects": r["residential_projects"], "mixed_projects": r["mixed_projects"],
            "commercial_projects": r["commercial_projects"], "plotted_projects": r["plotted_projects"],
            "extended_projects": _v(r["extended"]) if analysed else None,
            "suo_motu": _v(r["suo_motu_proceedings"]) if analysed else None,
            "units": _v(r["units"]) if analysed else None,
            "projects_with_unit_data": r["projects_with_unit_data"], "sold": _v(r["sold"]), "unsold": _v(r["unsold"]),
            "sales_pct": _v(r["sales_pct"]), "avg_progress_pct": _v(r["avg_progress_pct"]),
            "median_progress_pct": _v(r["median_progress_pct"]),
            "projects_with_progress_data": r["projects_with_progress_data"],
            "completion_rate_pct": _v(r["completion_rate_pct"]), "delay_rate_pct": _v(r["delay_rate_pct"]),
            "localities": json.dumps([[k, int(v)] for k, v in g["locality"].value_counts().head(8).items()]),
            "talukas": json.dumps([[k, int(v)] for k, v in g["taluka"].value_counts().head(5).items()]),
            "first_registration_year": int(yrs.min()) if len(yrs) else None,
            "last_registration_year": int(yrs.max()) if len(yrs) else None,
        }
        primary = conn.execute(
            "SELECT registration_status, company_reg_number, date_of_incorporation, registered_address, website, "
            "declared_total_projects, declared_completed_projects, declared_ongoing_projects, "
            "declared_experience_years, associate_names FROM developers WHERE developer_id = ?",
            (row["developer_id"],)).fetchone()
        if primary is not None:   # entity id = lowest promoter account id = the primary account
            row.update({k: primary[k] for k in primary.keys() if k != "associate_names"})
            row["associates"] = primary["associate_names"]            # JSON list of names, no other personal data
        for k in DEV_ANALYSED_ONLY:
            row[k] = _v(r[k]) if analysed else None
        # Status counts use the app's status (same rule as projects.status); area_report's `delayed` (past the
        # ORIGINAL completion date, including projects completed late) is kept as `past_original`.
        row["past_original"] = row["delayed"]
        if analysed:
            st = g.loc[g["detail_crawled"] == 1, "status"].value_counts()
            for col, label in (("completed", "Completed"), ("ongoing", "Ongoing"), ("delayed", "Delayed"),
                               ("pending", "Pending")):
                row[col] = int(st.get(label, 0))
        else:
            row["pending"] = None
        _insert(o, "developers", row)
        for y, gg in g.groupby("reg_year"):
            units = int(gg["total_units"].dropna().sum()) if gg["total_units"].notna().any() else None
            o.execute("INSERT INTO developer_registrations VALUES (?,?,?,?)", (row["developer_id"], int(y), len(gg), units))


def _write_projects(o, conn, p, ids) -> None:
    keys = ",".join(f"'{i}'" for i in ids) or "''"
    src = {int(r[0]): (r[1], r[2]) for r in conn.execute(
        f"SELECT entity_id, url, fetched_at FROM raw_responses WHERE endpoint_key='details' AND entity_id IN ({keys})")}
    for r in p.to_dict("records"):
        pid = int(r["project_id"])
        crawled = bool(r.get("detail_crawled"))
        url, checked = src.get(pid, (None, None))
        row = {
            "project_id": pid, "developer_id": _v(r["app_developer_id"]), "name": r["project_name"],
            "name_search": f"{r['project_name'] or ''} {r['reg_no_current'] or ''}".lower(),
            "rera_number": r["reg_no_current"], "category": r["project_category"], "project_type": r["project_type"],
            "district": r["district"], "taluka": r["taluka"], "locality": r["locality"],
            "locality_source": r["locality_source"], "address": r["full_address"], "pincode": r["pincode"],
            "latitude": _v(r["latitude"]), "longitude": _v(r["longitude"]),
            "approving_authority": r["approving_authority"], "status": r["status"],
            "status_detail": r["execution_category"], "status_basis": r["status_derived_basis"],
            "portal_status": r["status_source"], "portal_progress_label": r["portal_progress_label"],
            "registration_date": r["registration_date"], "start_date": r["project_start_date"],
            "original_completion_date": r["original_completion_date"],
            "current_completion_date": r["current_completion_date"],
            "completion_filing_date": r["completion_filing_date"], "progress_pct": _v(r["completion_pct"]),
            "progress_quarter": r["latest_progress_quarter"], "months_since_last_qpr": _v(r["months_since_last_qpr"]),
            "stalled": _v(r["stalled_flag"]), "financial_progress_pct": _v(r["financial_progress_pct"]),
            "total_units": _v(r["total_units"]), "sold_units": _v(r["booked_units"]),
            "unsold_units": _v(r["unbooked_units"]), "sales_pct": _v(r["sales_pct"]),
            "residential_units": _v(r["residential_units"]), "commercial_units": _v(r["commercial_units"]),
            "total_land_area_sqm": _v(r["total_land_area_sqm"]), "total_carpet_area_sqm": _v(r["total_carpet_area_sqm"]),
            "total_project_cost": _v(r.get("total_project_cost")), "min_unit_cost": _v(r.get("min_unit_cost")),
            "max_unit_cost": _v(r.get("max_unit_cost")), "complaints_total": _v(r["complaints_total"]),
            "complaints_pending": _v(r["complaints_pending"]), "suo_motu": _v(r["suo_motu_total"]),
            "challans": _v(r["challans_total"]), "challan_amount": _v(r["challan_amount_total"]),
            "detail_crawled": int(crawled), "source_url": url, "last_checked": checked,
            "city": r["city"], "unit_data_state": r.get("unit_data_state"), "unit_data_note": r.get("unit_data_note"),
            "units_row_count": _v(r.get("units_row_count")), "units_as_of": r.get("units_form3_submitted_on"),
            "units_scraped_at": r.get("units_scraped_at"),
            "total_open_area_sqm": _v(r.get("total_open_area_sqm")),
            "total_covered_area_sqm": _v(r.get("total_covered_area_sqm")),
            "parking_for_sale": _v(r.get("parking_for_sale")), "covered_parking": _v(r.get("covered_parking")),
            "garages_for_sale": _v(r.get("garages_for_sale")), "project_description": r.get("description"),
            "planning_authority": r.get("planning_authority"), "tp_number": r.get("tp_number"),
            "tp_name": r.get("tp_name"), "fp_number": r.get("fp_number"), "survey_number": r.get("survey_number"),
            "city_survey_number": r.get("city_survey_number"), "village_moje": r.get("village_moje"),
            "portal_last_submitted_at": r.get("portal_last_submitted_at"),
            "first_seen_at": r.get("first_seen_at"), "last_changed_at": r.get("last_changed_at"),
        }
        for k, v in list(row.items()):   # NaN / numpy -> None
            row[k] = _v(v)
        for k in DETAIL_ONLY:  # detail-only fields stay NULL until the project is crawled
            row[k] = _v(r.get(k)) if crawled else None
        _insert(o, "projects", row)


# Filings and updates that show a project was updated on GujRERA (kind -> title). The date is the one GujRERA publishes
# (submission / approval / order date), never the date this app collected it.
FILING_TITLES = {
    "portal_submission": "Latest filing recorded on GujRERA",
    "qpr": "Quarterly progress report (QPR) submitted",
    "unit_booking": "Unit-wise booking status filed (Form 3)",
    "facilities": "Facilities and construction progress filed (Form 1)",
    "engineer_certificate": "Engineer's certificate filed (Form 2)",
    "details_updated": "Project details updated by the promoter",
}


def _day(v) -> str | None:
    s = str(v)[:10] if v not in (None, "") and not (isinstance(v, float) and v != v) else None
    return s if s and len(s) == 10 and s[4] == "-" and s[7] == "-" else None


def _filing_events(conn, p, idlist_all: str) -> list[tuple]:
    """(project_id, date, kind, title, detail, reference) for every filing date GujRERA publishes for a project."""
    out: dict[tuple, tuple] = {}

    def add(pid, date, kind, detail=None, reference=None):
        d = _day(date)
        if d:
            out.setdefault((pid, d, kind), (pid, d, kind, FILING_TITLES[kind], detail, reference))

    for r in p.to_dict("records"):
        pid = int(r["project_id"])
        add(pid, r.get("portal_last_submitted_at"), "portal_submission")
        add(pid, r.get("last_qpr_submitted_at"), "qpr", r.get("latest_progress_quarter"))
        add(pid, r.get("details_updated_on"), "details_updated")
        add(pid, r.get("engineer_cert_submitted_on"), "engineer_certificate")
    for pid, submitted, n in conn.execute(
            f"SELECT project_id, submitted_on, number_of_units FROM project_form3 WHERE is_latest=1 "
            f"AND project_id IN ({idlist_all})"):
        add(pid, submitted, "unit_booking", None if n is None else f"{n} units declared")
    for pid, submitted in conn.execute(
            f"SELECT project_id, MAX(form1_submitted_on) FROM project_amenities WHERE project_id IN ({idlist_all}) "
            f"GROUP BY project_id"):
        add(pid, submitted, "facilities")
    return list(out.values())


def _write_legal_and_events(o, conn, p, crawled_list) -> tuple[int, int]:
    legal, events = [], []
    for r in conn.execute(f"""SELECT project_id, complaint_key, category, filed_date, order_date, status, outcome_text,
                                     penalty_amount, order_doc_uid, order_type
                              FROM complaints WHERE project_id IN ({crawled_list})"""):
        legal.append((r[0], "complaint", r[1], r[2], r[9], r[3], r[4], r[5], r[6], r[7],
                      DOC_URL + r[8] if r[8] else None))
        if r[3]:
            events.append((r[0], r[3], "complaint_filed", "Complaint filed", r[2], r[1]))
        if r[4]:
            events.append((r[0], r[4], "order", "Order published", r[6], r[1]))
    for r in conn.execute(f"""SELECT project_id, action_key, action_category, action_subtype_label, initiated_date,
                                     order_date, status, outcome_text, penalty_amount, order_doc_uid
                              FROM regulatory_actions WHERE project_id IN ({crawled_list})"""):
        kind = "suo_motu" if r[2] == "suo_motu_proceeding" else "challan"
        legal.append((r[0], kind, r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], DOC_URL + r[9] if r[9] else None))
        if r[4]:
            title = "Suo-motu proceeding" if kind == "suo_motu" else "Penalty challan issued"
            detail = r[3] if kind == "suo_motu" else (f"Rs {r[8]:,.0f}" if r[8] else None)
            events.append((r[0], r[4], kind, title, detail, r[1]))
        if r[5] and kind == "suo_motu":
            events.append((r[0], r[5], "order", "Order published", r[7], r[1]))
    for r in conn.execute(f"""SELECT project_id, appeal_key, forum, decision_date, subject FROM appeals
                              WHERE project_id IN ({crawled_list})"""):
        legal.append((r[0], "appeal_name_matched", r[1], r[2], None, None, r[3], None, r[4], None, None))
    o.executemany("INSERT INTO project_legal VALUES (?,?,?,?,?,?,?,?,?,?,?)", legal)
    events.extend(_filing_events(conn, p, idlist_all=",".join(str(int(x)) for x in p["project_id"]) or "0"))
    for r in p.to_dict("records"):
        if r["registration_date"]:
            events.append((int(r["project_id"]), r["registration_date"], "registration", "Registered with GujRERA",
                           r["reg_no_current"], None))
        if r.get("completion_filing_date") and r["status_derived"] == "completed":
            events.append((int(r["project_id"]), r["completion_filing_date"], "completion",
                           "Project-end (completion) filing", None, None))
    for r in conn.execute(f"""SELECT project_id, approved_on, extension_reg_no, previous_completion_date,
                                     extended_completion_date FROM project_extensions
                              WHERE status='approved' AND project_id IN ({crawled_list})"""):
        if r[1]:
            events.append((r[0], r[1], "extension", "Extension approved", f"{r[3] or 'n/a'} -> {r[4] or 'n/a'}", r[2]))
    o.executemany("INSERT INTO project_events VALUES (?,?,?,?,?,?)", events)
    return len(legal), len(events)
