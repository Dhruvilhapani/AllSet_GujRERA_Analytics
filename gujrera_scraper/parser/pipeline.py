"""Parse stage: rebuild the normalized tables from raw_responses (no network access)."""
from __future__ import annotations

import json
import logging
import sqlite3
from collections import defaultdict
from datetime import date

from database import repositories as repo
from database.db import RawStore, utcnow
from parser import complaint_parser, facility_parser, unit_parser
from parser.common import clean_str, normalize_name
from parser.project_parser import ProjectParser
from parser.promoter_parser import parse_promoter
from scraper.endpoints import TYPE_LABEL
from scraper.orchestrator import list_row_hash
from scraper.project_listing import in_scope, in_unit_scope

log = logging.getLogger(__name__)


def load_universe(raw: RawStore, list_keys: list[str], settings: dict | None = None) -> tuple[dict[int, dict], dict]:
    """Project list rows of the target types. With `settings`, district-scoped types (commercial / plotted) are
    limited to `target_districts`, like the crawl."""
    rows: dict[int, dict] = {}
    for key in list_keys:
        j, _ = raw.load(key, key)
        for r in (j or {}).get("data") or []:
            if settings is not None and not in_scope(r, key, settings):
                continue
            pid = int(r["projectRegId"])
            rows.setdefault(pid, r)
    counts, _ = raw.load("dashboard_counts", "dashboard")
    return rows, counts or {}


def failed_unit_blocks(conn: sqlite3.Connection) -> dict[int, set[str]]:
    """{project_id: block names} whose unit_list task ended 'failed' (latest status per task across runs)."""
    latest: dict[str, str] = {}
    for r in conn.execute("SELECT entity_id, status FROM crawl_queue WHERE endpoint_key='unit_list' ORDER BY id"):
        latest[r["entity_id"]] = r["status"]
    out: dict[int, set[str]] = defaultdict(set)
    for eid, status in latest.items():
        if status == "failed":
            pid, _, block = eid.partition("|")
            out[int(pid)].add(block)
    return out


def parse_all(conn: sqlite3.Connection, settings: dict, run_id: str | None = None,
              project_ids: list[int] | None = None) -> dict:
    raw = RawStore(conn)
    now = utcnow()
    run_date = date.today().isoformat()
    salt = settings["privacy"]["hash_salt"]
    universe, counts = load_universe(raw, settings["target_project_types"], settings)
    ids = sorted(universe) if project_ids is None else [p for p in project_ids if p in universe]
    stats = defaultdict(int)
    app_complaints: list[dict] = []
    app_actions: list[dict] = []
    failed_blocks = failed_unit_blocks(conn)

    conn.execute("BEGIN")
    for i, pid in enumerate(ids, start=1):
        endpoints = raw.load_all_for_entity("project", pid)
        if not endpoints:
            stats["projects_list_only"] += 1
        parser = ProjectParser(universe[pid], endpoints, run_date, now)
        try:
            res = parser.parse()
            prow = res.rows["projects"][0]
            up = None
            if in_unit_scope(universe[pid], settings):
                up = unit_parser.parse_units(pid, prow["project_category"], endpoints,
                                             raw.load_by_prefix("project_block", "unit_list", f"{pid}|"),
                                             failed_blocks.get(pid, set()), now)
                res.rows["project_blocks"] = unit_parser.merge_into_blocks(
                    pid, res.rows.get("project_blocks", []), up, unit_parser.parse_block_master(endpoints),
                    facility_parser.parse_block_floors(endpoints))
                res.rows["project_amenities"] = facility_parser.parse_facilities(pid, endpoints)
                stats["amenity_rows"] += len(res.rows["project_amenities"])
                res.rows["project_block_fetch"] = up.block_fetch
                prow.update(up.project_fields())
        except Exception as e:  # noqa: BLE001 - one malformed project must not stop the batch
            log.exception("parse failed for project %s", pid)
            stats["parse_errors"] += 1
            repo.add_quality_note(conn, f"project:{pid}", f"parse error: {e!r}"[:300])
            continue
        prow["list_row_hash"] = list_row_hash(universe[pid])
        repo.upsert_project(conn, prow, run_id, now)
        repo.replace_project_children(conn, pid, res.rows)
        if up is not None:
            stats[f"unit_state_{up.state}"] += 1
            if up.form3:
                repo.upsert_form3(conn, up.form3, now)
            if up.units or up.blocks_ok:
                for k, v in repo.upsert_units(conn, pid, up.units, up.blocks_ok, now).items():
                    stats[f"units_{k}"] += v
            _note_unit_reconciliation(conn, pid, prow, up)
        repo.insert_rows(conn, "developer_aliases", res.rows.get("developer_aliases", []), mode="INSERT OR IGNORE")
        repo.upsert_documents(conn, res.rows.get("project_documents", []))
        repo.upsert_provenance(conn, res.provenance)
        repo.replace_conflicts(conn, "project", str(pid), res.conflicts)
        apps = (endpoints.get("applications") or (None,))[0]
        apps = apps.get("data") if isinstance(apps, dict) else None
        c, a = complaint_parser.from_applications(pid, apps if isinstance(apps, list) else [], now)
        app_complaints.extend(c)
        app_actions.extend(a)
        stats["projects_parsed"] += 1
        stats["conflicts"] += len(res.conflicts)
        for issue in res.issues:
            repo.add_quality_note(conn, f"project:{pid}", issue)
        if i % 1000 == 0:
            conn.execute("COMMIT")
            conn.execute("BEGIN")
            log.info("parsed %d/%d projects", i, len(ids))
    conn.execute("COMMIT")

    conn.execute("BEGIN")
    stats["documents_reapplied"] = reapply_document_fields(conn, ids if project_ids is not None else None)
    stats.update(parse_promoters(conn, raw, universe, salt, now))
    stats.update(parse_complaints(conn, raw, app_complaints, app_actions, salt, now, project_ids))
    suggest_developer_groups(conn)
    record_universe_quality(conn, universe, counts)
    if run_id:
        record_scrape_history(conn, run_id, now)
    conn.execute("COMMIT")
    return dict(stats)


def _note_unit_reconciliation(conn: sqlite3.Connection, pid: int, prow: dict, up) -> None:
    """Record (never "fix") disagreements between the unit rows and the other booking sources: the portal's block
    chart (QPR-fed) and the Form 3 header can be submitted on different dates and can contain source bugs."""
    if up.state != "complete":
        return
    booked = sum(u["status_norm"] == "booked" for u in up.units)
    unbooked = sum(u["status_norm"] == "unbooked" for u in up.units)
    chart_b, chart_u = prow.get("booked_units"), prow.get("unbooked_units")
    if chart_b is not None and chart_u is not None and (chart_b, chart_u) != (booked, unbooked):
        repo.add_quality_note(conn, f"project:{pid}", f"unit rows booked/unbooked {booked}/{unbooked} differ from the "
                                                      f"portal block chart {chart_b}/{chart_u}")
    declared = (up.form3 or {}).get("number_of_units")
    if declared is not None and declared != len(up.units):
        repo.add_quality_note(conn, f"project:{pid}", f"unit rows ({len(up.units)}) differ from the Form 3 header "
                                                      f"number of units ({declared})")


def reapply_document_fields(conn: sqlite3.Connection, project_ids: list[int] | None) -> int:
    """Child tables are rebuilt from API data on every parse; re-apply certificate-derived fills/conflicts."""
    from scraper.documents import cross_validate_certificate
    q = "SELECT * FROM project_documents WHERE parsed_fields IS NOT NULL AND project_id IS NOT NULL"
    n = 0
    wanted = set(project_ids) if project_ids is not None else None
    for d in conn.execute(q).fetchall():
        if wanted is not None and d["project_id"] not in wanted:
            continue
        try:
            fields = json.loads(d["parsed_fields"])
        except (TypeError, json.JSONDecodeError):
            continue
        if "error" in fields:
            continue
        cross_validate_certificate(conn, d["project_id"], d["document_uid"], d["document_type"], fields)
        n += 1
    return n


def parse_promoters(conn: sqlite3.Connection, raw: RawStore, universe: dict, salt: str, now: str) -> dict:
    n = 0
    for _eid, j, _meta in raw.iter_endpoint("promoter"):
        row = parse_promoter(j, salt)
        if not row:
            continue
        old = conn.execute("SELECT first_seen_at FROM developers WHERE developer_id=?", (row["developer_id"],)).fetchone()
        row.update({"first_seen_at": old["first_seen_at"] if old else now, "last_seen_at": now,
                    "last_scraped_at": now, "raw_response_id": _meta["id"]})
        repo.insert_rows(conn, "developers", [row])
        repo.insert_rows(conn, "developer_aliases", [{"developer_id": row["developer_id"], "alias": row["canonical_name"],
                                                     "source": "promoter_master"}], mode="INSERT OR IGNORE")
        n += 1
    # promoters referenced by projects whose master record could not be fetched: minimal row from list name
    missing = conn.execute("""SELECT pd.developer_id, MIN(da.alias) alias FROM project_developers pd
                              LEFT JOIN developers d ON d.developer_id = pd.developer_id
                              LEFT JOIN developer_aliases da ON da.developer_id = pd.developer_id
                              WHERE d.developer_id IS NULL GROUP BY pd.developer_id""").fetchall()
    for m in missing:
        repo.insert_rows(conn, "developers", [{
            "developer_id": m["developer_id"], "canonical_name": m["alias"] or f"PROMOTER {m['developer_id']}",
            "name_normalized": normalize_name(m["alias"]), "source_endpoint": "project_list (promoter master unavailable)",
            "first_seen_at": now, "last_seen_at": now}], mode="INSERT OR IGNORE")
    conn.execute("UPDATE developer_aliases SET alias_normalized = alias WHERE alias_normalized IS NULL")
    for r in conn.execute("SELECT developer_id, alias, source FROM developer_aliases").fetchall():
        conn.execute("UPDATE developer_aliases SET alias_normalized=? WHERE developer_id=? AND alias=? AND source=?",
                     (normalize_name(r["alias"]), r["developer_id"], r["alias"], r["source"]))
    return {"promoters_parsed": n, "promoters_missing_master": len(missing)}


def parse_complaints(conn: sqlite3.Connection, raw: RawStore, app_complaints: list[dict], app_actions: list[dict],
                     salt: str, now: str, project_ids: list[int] | None = None) -> dict:
    serial_to_project = {r["reg_serial"]: r["project_id"] for r in
                         conn.execute("SELECT reg_serial, project_id FROM projects WHERE reg_serial IS NOT NULL")}
    complaints: dict[str, dict] = {}
    actions: dict[str, dict] = {}
    docs: list[dict] = []
    for c in app_complaints:
        complaints[c["complaint_key"]] = complaint_parser.merge_records(complaints.get(c["complaint_key"], {}), c)
    for a in app_actions:
        actions[a["action_key"]] = complaint_parser.merge_records(actions.get(a["action_key"], {}), a)
    list_rows = 0
    for eid, j, _meta in raw.iter_endpoint("complaint_list"):
        rows = j.get("data") if isinstance(j, dict) else None
        if not isinstance(rows, list):
            continue
        list_rows += len(rows)
        c, a, d = complaint_parser.from_judgment_rows(rows, eid, serial_to_project, salt, now)
        for x in c:
            complaints[x["complaint_key"]] = complaint_parser.merge_records(complaints.get(x["complaint_key"], {}), x)
        for x in a:
            actions[x["action_key"]] = complaint_parser.merge_records(actions.get(x["action_key"], {}), x)
        docs.extend(d)
    # challans: attach district/respondent from the proceeding they relate to (by complaint number)
    by_cnum = {v.get("complaint_number") or v.get("related_complaint_number"): v
               for v in list(complaints.values()) + list(actions.values())
               if (v.get("complaint_number") or v.get("related_complaint_number"))}
    for a in actions.values():
        if a["action_category"] == "penalty_challan" and a.get("related_complaint_number") in by_cnum:
            src = by_cnum[a["related_complaint_number"]]
            if not a.get("district"):
                a["district"] = src.get("district")
            if not a.get("respondent_name"):
                a["respondent_name"] = src.get("respondent_name")
    repo.upsert_keyed(conn, "complaints", "complaint_key", complaints.values(), now)
    repo.upsert_keyed(conn, "regulatory_actions", "action_key", actions.values(), now)
    _prune_stale(conn, "complaints", "complaint_key", set(complaints), project_ids)
    _prune_stale(conn, "regulatory_actions", "action_key", set(actions), project_ids)

    # appeals
    names: dict[str, list[int]] = defaultdict(list)
    for r in conn.execute("SELECT project_id, project_name FROM projects"):
        n = normalize_name(r["project_name"])
        if n:
            names[n].append(r["project_id"])
    appeals = []
    j, _ = raw.load("appellate_tribunal", "appellate_tribunal")
    at_rows = j.get("data") if isinstance(j, dict) else j
    if isinstance(at_rows, list):
        a, d = complaint_parser.from_appellate(at_rows, names)
        appeals.extend(a)
        docs.extend(d)
    for key, forum in (("court_hc", "high_court"), ("court_sc", "supreme_court"), ("court_at", "appellate_tribunal_list")):
        j, _ = raw.load(key, key)
        rows = j.get("data") if isinstance(j, dict) else None
        appeals.extend(complaint_parser.from_court_list(rows if isinstance(rows, list) else [], forum))
    repo.upsert_keyed(conn, "appeals", "appeal_key", appeals, now)
    repo.upsert_documents(conn, docs)
    return {"complaints": len(complaints), "regulatory_actions": len(actions), "appeals": len(appeals),
            "judgement_list_rows": list_rows}


def _prune_stale(conn: sqlite3.Connection, table: str, key_col: str, keep: set[str],
                 project_ids: list[int] | None) -> None:
    """Remove records no longer produced by the sources (e.g. reclassified keys). On a partial parse only
    records that the partial parse was responsible for are eligible."""
    rows = conn.execute(f"SELECT {key_col} k, project_id, source_endpoint FROM {table}").fetchall()
    scope = set(project_ids) if project_ids is not None else None
    for r in rows:
        if r["k"] in keep:
            continue
        from_lists = "complaint_list" in (r["source_endpoint"] or "")
        if scope is None or from_lists or r["project_id"] in scope:
            conn.execute(f"DELETE FROM {table} WHERE {key_col}=?", (r["k"],))


def suggest_developer_groups(conn: sqlite3.Connection) -> None:
    """Group promoter accounts sharing a PAN hash / company registration no. (confirmed) or a declared group /
    identical normalised name (needs review). Never merges developer rows."""
    from parser.promoter_parser import valid_entity_reg_no
    conn.execute("DELETE FROM developer_groups")
    # only well-formed CIN / LLPIN values identify an entity (placeholders like NOTAPPLICABLE must not group)
    conn.create_function("valid_reg", 1, lambda v: valid_entity_reg_no(v))
    for basis, col, status in (("pan_hash", "pan_hash", "auto_confirmed"),
                               ("company_reg_number", "valid_reg(company_reg_number)", "auto_confirmed"),
                               ("declared_group_name", "declared_group_name", "needs_review"),
                               ("name_normalized", "name_normalized", "needs_review")):
        rows = conn.execute(f"""SELECT {col} k, GROUP_CONCAT(developer_id) ids FROM developers
                                WHERE {col} IS NOT NULL AND TRIM({col}) != '' GROUP BY {col}
                                HAVING COUNT(*) > 1""").fetchall()
        for r in rows:
            if basis == "declared_group_name" and (normalize_name(r["k"]) or "") in ("na", "none", "no", "nil", ""):
                continue
            gid = f"{basis}:{r['k'][:40]}"
            for did in r["ids"].split(","):
                conn.execute("INSERT OR IGNORE INTO developer_groups(group_id, developer_id, match_basis, match_score, "
                             "review_status) VALUES (?,?,?,?,?)", (gid, int(did), basis, 1.0, status))


def record_universe_quality(conn: sqlite3.Connection, universe: dict, counts: dict) -> None:
    rows = counts.get("data") if isinstance(counts, dict) else None
    if not isinstance(rows, list):
        return
    by_type = {r.get("type"): r for r in rows}
    res, mix = by_type.get("Residential/Group Housing"), by_type.get("Mixed Development")
    if res and mix and res.get("ahmedabad") == mix.get("ahmedabad"):
        repo.add_quality_note(conn, "universe", f"Dashboard grid shows identical Ahmedabad counts for Residential and "
                                                f"Mixed ({res.get('ahmedabad')}); list endpoint used as ground truth.")
    # Residential and Mixed are listed for all of Gujarat, so their dashboard total is comparable with the list rows.
    # Commercial / Plotted are listed for the target districts only (see in_scope): compare per district instead.
    for name, label, short in (("Residential", "Residential/Group Housing", "resid"), ("Mixed", "Mixed Development", "mixed")):
        row = by_type.get(label)
        n = sum(1 for r in universe.values() if short in (r.get("projectType") or "").lower())
        if row and int(row.get("total") or 0) != n:
            repo.add_quality_note(conn, "universe", f"{name}: dashboard total {row.get('total')} vs list rows {n}")
    grid_district = {"ahmedabad": "ahmedabad", "gandhinagar": "gandhi"}
    for label in TYPE_LABEL.values():
        row = by_type.get(label)
        for district, col in grid_district.items():
            n = sum(1 for r in universe.values() if (r.get("projectType") or "") == label
                    and (r.get("districtName") or "").strip().lower() == district)
            if row and row.get(col) is not None and int(row[col]) != n:
                repo.add_quality_note(conn, "universe", f"{label} / {district.title()}: dashboard grid {row[col]} vs "
                                                        f"list rows {n}")


def record_scrape_history(conn: sqlite3.Connection, run_id: str, now: str) -> None:
    conn.execute("""INSERT OR REPLACE INTO project_scrape_history(project_id, run_id, scraped_at, endpoints_ok,
                    endpoints_failed, changed_endpoints)
                    SELECT CAST(entity_id AS INTEGER), run_id, ?, SUM(status IN ('done','empty')),
                           SUM(status='failed'), SUM(COALESCE(changed,0))
                    FROM crawl_queue WHERE run_id=? AND entity_type='project' GROUP BY entity_id""", (now, run_id))


def json_dumps(o) -> str:
    return json.dumps(o, ensure_ascii=False, default=str)
