"""Write-side repository: idempotent upserts with change tracking.

Child tables of a project are rebuilt from the latest raw responses on every parse (delete + insert per
project), so re-parsing never duplicates rows. Projects, developers, documents, complaints and actions are
upserted so first_seen_at / download state survive re-parses.
"""
from __future__ import annotations

import sqlite3
from typing import Iterable

from database.db import utcnow

PROJECT_CHILD_TABLES = ["project_locations", "project_developers", "project_professionals", "project_blocks",
                        "project_units", "project_progress", "project_quarters", "project_extensions",
                        "project_applications", "annual_compliance"]

# fields whose changes are logged in project_status_history (incremental change detection)
TRACKED_PROJECT_FIELDS = ["status_source", "status_derived", "current_completion_date", "extension_count",
                          "latest_progress_pct", "total_units", "booked_units", "unbooked_units",
                          "completion_filing_status", "reg_no_current", "portal_progress_label"]

_columns_cache: dict[str, list[str]] = {}


def columns(conn: sqlite3.Connection, table: str) -> list[str]:
    if table not in _columns_cache:
        _columns_cache[table] = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
    return _columns_cache[table]


def insert_rows(conn: sqlite3.Connection, table: str, rows: Iterable[dict], mode: str = "INSERT OR REPLACE") -> int:
    cols = columns(conn, table)
    n = 0
    for row in rows:
        keys = [k for k in row if k in cols]
        conn.execute(f"{mode} INTO {table} ({','.join(keys)}) VALUES ({','.join('?' * len(keys))})",
                     [row[k] for k in keys])
        n += 1
    return n


def upsert_project(conn: sqlite3.Connection, row: dict, run_id: str | None, now: str) -> list[tuple]:
    """Upsert one project row; returns list of (field, old, new) changes."""
    old = conn.execute("SELECT * FROM projects WHERE project_id=?", (row["project_id"],)).fetchone()
    row = dict(row)
    row["last_seen_at"] = now
    row["last_scraped_at"] = now
    changes = []
    if old is None:
        row["first_seen_at"] = now
        row["last_changed_at"] = now
    else:
        row["first_seen_at"] = old["first_seen_at"]
        for f in TRACKED_PROJECT_FIELDS:
            ov, nv = old[f], row.get(f)
            if (ov is None) != (nv is None) or (ov is not None and str(ov) != str(nv)):
                changes.append((f, ov, nv))
        row["last_changed_at"] = now if changes else old["last_changed_at"]
        for f, ov, nv in changes:
            conn.execute("INSERT INTO project_status_history(project_id, field, old_value, new_value, changed_at, run_id)"
                         " VALUES (?,?,?,?,?,?)", (row["project_id"], f, None if ov is None else str(ov),
                                                    None if nv is None else str(nv), now, run_id))
    insert_rows(conn, "projects", [row])
    return changes


def replace_project_children(conn: sqlite3.Connection, project_id: int, rows_by_table: dict[str, list[dict]]) -> None:
    for t in PROJECT_CHILD_TABLES:
        conn.execute(f"DELETE FROM {t} WHERE project_id=?", (project_id,))
        if rows_by_table.get(t):
            insert_rows(conn, t, _dedupe(conn, t, rows_by_table[t]))


def _dedupe(conn, table: str, rows: list[dict]) -> list[dict]:
    pk = [r["name"] for r in sorted(conn.execute(f"PRAGMA table_info({table})"), key=lambda r: r["pk"]) if r["pk"]]
    seen, out = set(), []
    for r in rows:
        k = tuple(r.get(c) for c in pk)
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def upsert_documents(conn: sqlite3.Connection, docs: Iterable[dict]) -> None:
    """Insert new documents; for known ones refresh metadata but keep download state."""
    for d in docs:
        old = conn.execute("SELECT document_uid, document_type, download_priority FROM project_documents "
                           "WHERE document_uid=?", (d["document_uid"],)).fetchone()
        if old is None:
            insert_rows(conn, "project_documents", [d], mode="INSERT OR IGNORE")
            continue
        # a document referenced from several places keeps the most specific (critical) classification
        prio = "critical" if "critical" in (old["download_priority"], d.get("download_priority")) else "metadata_only"
        conn.execute(
            """UPDATE project_documents SET project_id=COALESCE(project_id, ?), related_key=COALESCE(related_key, ?),
               document_name=COALESCE(document_name, ?), document_date=COALESCE(document_date, ?),
               document_type=CASE WHEN ?='critical' AND download_priority!='critical' THEN ? ELSE document_type END,
               download_priority=? WHERE document_uid=?""",
            (d.get("project_id"), d.get("related_key"), d.get("document_name"), d.get("document_date"),
             d.get("download_priority"), d.get("document_type"), prio, d["document_uid"]))


def upsert_provenance(conn: sqlite3.Connection, recs: Iterable[dict]) -> None:
    for p in recs:
        old = conn.execute("SELECT value, extracted_at FROM field_provenance WHERE entity_type=? AND entity_id=? "
                           "AND field=?", (p["entity_type"], p["entity_id"], p["field"])).fetchone()
        if old is not None and old["value"] == p["value"]:
            conn.execute("UPDATE field_provenance SET last_verified_at=?, source=?, source_url=?, confidence=? "
                         "WHERE entity_type=? AND entity_id=? AND field=?",
                         (p["last_verified_at"], p["source"], p["source_url"], p["confidence"], p["entity_type"],
                          p["entity_id"], p["field"]))
        else:
            insert_rows(conn, "field_provenance", [p])


def replace_conflicts(conn: sqlite3.Connection, entity_type: str, entity_id: str, recs: list[dict],
                      fields: Iterable[str] | None = None) -> None:
    if fields is None:
        conn.execute("DELETE FROM field_conflicts WHERE entity_type=? AND entity_id=? AND source_1 NOT LIKE 'document:%' "
                     "AND source_2 NOT LIKE 'document:%'", (entity_type, entity_id))
    else:
        for f in fields:
            conn.execute("DELETE FROM field_conflicts WHERE entity_type=? AND entity_id=? AND field=?",
                         (entity_type, entity_id, f))
    insert_rows(conn, "field_conflicts", recs)


def upsert_keyed(conn: sqlite3.Connection, table: str, key_col: str, rows: Iterable[dict], now: str) -> None:
    """Upsert complaints / regulatory_actions / appeals preserving first_seen_at."""
    has_seen = "first_seen_at" in columns(conn, table)
    for r in rows:
        r = dict(r)
        if has_seen:
            old = conn.execute(f"SELECT first_seen_at FROM {table} WHERE {key_col}=?", (r[key_col],)).fetchone()
            r["first_seen_at"] = old["first_seen_at"] if old and old["first_seen_at"] else (r.get("first_seen_at") or now)
            r["last_seen_at"] = now
        insert_rows(conn, table, [r])


def add_quality_note(conn: sqlite3.Connection, scope: str, note: str) -> None:
    exists = conn.execute("SELECT 1 FROM data_quality_notes WHERE scope=? AND note=?", (scope, note)).fetchone()
    if not exists:
        conn.execute("INSERT INTO data_quality_notes(scope, note, detected_at) VALUES (?,?,?)", (scope, note, utcnow()))
