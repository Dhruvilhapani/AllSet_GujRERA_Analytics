"""Download + text-extract the analytics-critical documents and cross-validate them against API data.

Only document types listed in settings.documents.download_types are downloaded. Every other document stays
in project_documents with its download URL (download_status='not_downloaded', priority 'metadata_only').
Download state lives in project_documents itself, so this stage is naturally resumable.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from database import repositories as repo
from database.db import utcnow
from parser.common import parse_reg_no
from parser.document_parser import extract_text, parse_certificate
from scraper.endpoints import DOC_DOWNLOAD
from scraper.network_client import FetchError, GujReraClient

log = logging.getLogger(__name__)

CERT_TYPES = {"registration_certificate", "extension_certificate"}


class DocumentDownloader:
    def __init__(self, conn: sqlite3.Connection, client: GujReraClient, settings: dict):
        self.conn = conn
        self.client = client
        self.s = settings
        self.doc_dir = Path(settings["documents_dir"])
        self.text_dir = Path(settings["document_text_dir"])
        self.types = settings["documents"]["download_types"]
        self.ocr = settings["documents"].get("ocr_command")

    def pending(self, limit: int | None, types: list[str] | None, project_ids: list[int] | None) -> list[sqlite3.Row]:
        types = types or self.types
        q = (f"SELECT * FROM project_documents WHERE download_priority='critical' AND document_type IN "
             f"({','.join('?' * len(types))}) AND download_status IN ('not_downloaded','failed')")
        params: list = list(types)
        if project_ids:
            q += f" AND project_id IN ({','.join('?' * len(project_ids))})"
            params += project_ids
        q += " ORDER BY CASE document_type WHEN 'registration_certificate' THEN 0 WHEN 'extension_certificate' THEN 1 " \
             "ELSE 2 END, project_id"
        if limit:
            q += f" LIMIT {int(limit)}"
        return self.conn.execute(q, params).fetchall()

    def run(self, limit: int | None = None, types: list[str] | None = None, project_ids: list[int] | None = None,
            workers: int | None = None) -> dict:
        rows = self.pending(limit, types, project_ids)
        stats = {"queued": len(rows), "downloaded": 0, "failed": 0, "ocr_needed": 0, "parsed": 0}
        with ThreadPoolExecutor(max_workers=workers or self.s["network"]["workers"]) as pool:
            futs = {pool.submit(self._download, r): r for r in rows}
            for i, fut in enumerate(as_completed(futs), start=1):
                row = futs[fut]
                try:
                    content = fut.result()
                except FetchError as e:
                    self.conn.execute("UPDATE project_documents SET download_status='failed', parsed_fields=? "
                                      "WHERE document_uid=?", (json.dumps({"error": str(e)[:300]}), row["document_uid"]))
                    stats["failed"] += 1
                    continue
                self._store(row, content, stats)
                if i % 200 == 0:
                    log.info("documents: %d/%d %s", i, len(rows), stats)
        return stats

    def _download(self, row: sqlite3.Row) -> tuple[bytes, str]:
        r = self.client.get(DOC_DOWNLOAD.format(uid=row["document_uid"]), store=False, binary=True)
        ctype = r.content_type.lower()
        if r.content.startswith(b"%PDF") or "pdf" in ctype:
            return r.content, "pdf"
        for sig, ext in ((b"\xff\xd8", "jpg"), (b"\x89PNG", "png")):
            if r.content.startswith(sig):
                return r.content, ext
        raise FetchError(r.url, r.status, f"unsupported document ({r.content_type}, {len(r.content)} bytes)")

    def _store(self, row: sqlite3.Row, result: tuple[bytes, str], stats: dict) -> None:
        content, ext = result
        uid, dtype = row["document_uid"], row["document_type"]
        path = self.doc_dir / dtype / f"{uid}.{ext}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        if ext == "pdf":
            text, tstatus = extract_text(content, self.ocr)
        else:
            text, tstatus = "", "ocr_needed"   # scanned image upload
        text_path = None
        if text.strip():
            text_path = self.text_dir / dtype / f"{uid}.txt"
            text_path.parent.mkdir(parents=True, exist_ok=True)
            text_path.write_text(text, encoding="utf-8")
        parsed = parse_certificate(text) if dtype in CERT_TYPES and text else {}
        # correct the document type from its content (FORM F = extension certificate)
        reg = parse_reg_no(parsed.get("reg_no"))
        if (parsed.get("certificate_form", "").startswith("FORM F") or (reg and reg.ex_count > 0)) \
                and dtype == "registration_certificate":
            dtype = "extension_certificate"   # FORM F, or a FORM C re-issued with an /EXn/ number
        if tstatus == "ocr_needed":
            stats["ocr_needed"] += 1
        self.conn.execute(
            """UPDATE project_documents SET download_status='downloaded', local_path=?, size_bytes=?, sha256=?,
               text_status=?, text_path=?, parsed_fields=?, downloaded_at=?, file_type=?, document_type=?
               WHERE document_uid=?""",
            (str(path), len(content), hashlib.sha256(content).hexdigest(), tstatus,
             str(text_path) if text_path else None, json.dumps(parsed, ensure_ascii=False) if parsed else None,
             utcnow(), ext, dtype, uid))
        stats["downloaded"] += 1
        if parsed and row["project_id"]:
            stats["parsed"] += 1
            cross_validate_certificate(self.conn, row["project_id"], uid, dtype, parsed)


def cross_validate_certificate(conn: sqlite3.Connection, project_id: int, uid: str, dtype: str, f: dict) -> None:
    """Compare certificate fields with API-derived values. Never overwrites a non-null API value;
    fills NULL fields (marked with document provenance) and records every disagreement in field_conflicts."""
    now = utcnow()
    src = f"document:{dtype}:{uid}"
    p = conn.execute("SELECT * FROM projects WHERE project_id=?", (project_id,)).fetchone()
    if p is None:
        return
    conflicts = []

    def conflict(field, api_value, api_src, doc_value, interpreted, rule, status="unresolved"):
        if api_value is None or doc_value is None or str(api_value) == str(doc_value):
            return
        conflicts.append({"entity_type": "project", "entity_id": str(project_id), "field": field,
                          "value_1": str(api_value), "source_1": api_src, "value_2": str(doc_value), "source_2": src,
                          "conflict_status": status, "interpreted_value": None if interpreted is None else str(interpreted),
                          "rule": rule, "detected_at": now})

    def prov(field, value):
        repo.upsert_provenance(conn, [{"entity_type": "project", "entity_id": str(project_id), "field": field,
                                       "value": str(value), "value_state": "REPORTED", "source": "GujRERA certificate PDF",
                                       "source_url": f"https://gujrera.gujarat.gov.in/vdms/download/{uid}",
                                       "source_document": uid, "extracted_at": now, "last_verified_at": now,
                                       "confidence": "high"}])

    reg = parse_reg_no(f.get("reg_no"))
    if reg and reg.serial and p["reg_serial"] and reg.serial != p["reg_serial"]:
        conflict("certificate_reg_serial", p["reg_serial"], "api:projects.reg_serial", reg.serial, None,
                 "certificate belongs to a different registration serial; not used for this project")
        repo.insert_rows(conn, "field_conflicts", conflicts)
        return
    # Older extensions re-issued a FORM C carrying the /EXn/ number: treat any certificate whose number has EX
    # markers (or FORM F) as an extension-era certificate.
    is_extension = dtype == "extension_certificate" or bool(reg and reg.ex_count > 0)
    loc = conn.execute("SELECT * FROM project_locations WHERE project_id=?", (project_id,)).fetchone()
    if loc is not None:
        for col, key in (("tp_number", "tp_revenue_village"), ("fp_number", "fp_survey_no")):
            if loc[col] is None and f.get(key):
                conn.execute(f"UPDATE project_locations SET {col}=? WHERE project_id=?", (f[key], project_id))
                prov(col, f[key])
    if not is_extension and reg and reg.serial:
        if p["reg_no_original"] is None:
            conn.execute("UPDATE projects SET reg_no_original=? WHERE project_id=?", (f["reg_no"], project_id))
            prov("reg_no_original", f["reg_no"])
        conflict("original_completion_date", p["original_completion_date"], "api:projects.original_completion_date",
                 f.get("valid_till"), p["original_completion_date"],
                 "API value kept; Form C 'ending with' date recorded as conflict")
        conflict("registration_date", p["registration_date"], "api:list.approvedOn", f.get("issue_date"),
                 p["registration_date"], "API value kept")
    if is_extension:
        if f.get("previous_reg_no"):
            prev = parse_reg_no(f["previous_reg_no"])
            if prev and prev.ex_count == 0 and p["reg_no_original"] is None:
                conn.execute("UPDATE projects SET reg_no_original=? WHERE project_id=?", (f["previous_reg_no"], project_id))
                prov("reg_no_original", f["previous_reg_no"])
        ext = None
        if f.get("reg_no"):
            ext = conn.execute("SELECT * FROM project_extensions WHERE project_id=? AND extension_reg_no=?",
                               (project_id, f["reg_no"])).fetchone()
        if ext is None:
            ext = conn.execute("SELECT * FROM project_extensions WHERE project_id=? AND certificate_uid=?",
                               (project_id, uid)).fetchone()
        if ext is not None and f.get("valid_till"):
            if ext["extended_completion_date"] is None:
                conn.execute("""UPDATE project_extensions SET extended_completion_date=?, extended_completion_date_source=?
                                WHERE project_id=? AND extension_seq=?""",
                             (f["valid_till"], "certificate", project_id, ext["extension_seq"]))
                prov(f"extension[{ext['extension_seq']}].extended_completion_date", f["valid_till"])
            else:
                conflict(f"extension[{ext['extension_seq']}].extended_completion_date", ext["extended_completion_date"],
                         f"api:{ext['extended_completion_date_source']}", f["valid_till"], ext["extended_completion_date"],
                         "API/reg-no value kept")
        if f.get("reg_no") and f["reg_no"] == p["reg_no_current"]:
            conflict("current_completion_date", p["current_completion_date"], "api:projects.current_completion_date",
                     f.get("valid_till"), p["current_completion_date"], "API value kept; latest extension certificate differs")
    if conflicts:
        repo.insert_rows(conn, "field_conflicts", conflicts)
