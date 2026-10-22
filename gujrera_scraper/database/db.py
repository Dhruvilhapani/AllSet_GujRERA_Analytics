"""SQLite connection management and the raw-response store."""
from __future__ import annotations

import gzip
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(db_path: str | Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=60, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=OFF")  # enforced logically; raw parse order varies
    conn.execute("PRAGMA busy_timeout=60000")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create tables and add any columns introduced since the database was created (additive migration)."""
    ddl = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(ddl)
    ref = sqlite3.connect(":memory:")
    ref.executescript(ddl)
    for (table,) in ref.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for cid, name, ctype, *_ in ref.execute(f"PRAGMA table_info({table})"):
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ctype}")
    ref.close()
    conn.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at, description) VALUES (?,?,?)",
                 ("2026-10-unit-layer", utcnow(),
                  "Commercial/Plotted categories, unit-wise booking tables, open-terrace area, infrastructure"))


class RawStore:
    """Persists every API response (gzip) keyed by URL and tracks content changes."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._lock = threading.Lock()
        self._context = threading.local()

    def set_context(self, endpoint_key: str | None, entity_type: str | None, entity_id: Any) -> None:
        self._context.value = (endpoint_key, entity_type, None if entity_id is None else str(entity_id))

    def save_raw(self, *, url: str, status: int, content_type: str, body: bytes | None,
                 sha256: str, size: int, endpoint_key: str | None = None,
                 entity_type: str | None = None, entity_id: Any = None) -> bool:
        if endpoint_key is None:
            endpoint_key, entity_type, entity_id = getattr(self._context, "value", (None, None, None))
        entity_id = None if entity_id is None else str(entity_id)
        now = utcnow()
        with self._lock:
            row = self.conn.execute("SELECT id, sha256 FROM raw_responses WHERE url=?", (url,)).fetchone()
            body_gz = gzip.compress(body, 6) if body is not None else None
            if row is None:
                self.conn.execute(
                    """INSERT INTO raw_responses(url, endpoint_key, entity_type, entity_id, http_status,
                       content_type, size_bytes, sha256, body_gz, first_fetched_at, fetched_at, changed_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (url, endpoint_key, entity_type, entity_id, status, content_type, size, sha256,
                     body_gz, now, now, now))
                return True
            changed = row["sha256"] != sha256
            if changed:
                self.conn.execute(
                    """UPDATE raw_responses SET http_status=?, content_type=?, size_bytes=?, sha256=?,
                       body_gz=?, fetched_at=?, changed_at=?, endpoint_key=COALESCE(?, endpoint_key),
                       entity_type=COALESCE(?, entity_type), entity_id=COALESCE(?, entity_id) WHERE id=?""",
                    (status, content_type, size, sha256, body_gz, now, now, endpoint_key, entity_type,
                     entity_id, row["id"]))
            else:
                self.conn.execute("UPDATE raw_responses SET fetched_at=? WHERE id=?", (now, row["id"]))
            return changed

    # ---- reading ----
    def load(self, endpoint_key: str, entity_id: Any) -> tuple[Any, dict] | tuple[None, None]:
        row = self.conn.execute(
            """SELECT id, url, body_gz, fetched_at FROM raw_responses
               WHERE endpoint_key=? AND entity_id=? ORDER BY fetched_at DESC LIMIT 1""",
            (endpoint_key, str(entity_id))).fetchone()
        if row is None or row["body_gz"] is None:
            return None, None
        try:
            data = json.loads(gzip.decompress(row["body_gz"]))
        except (json.JSONDecodeError, OSError):
            return None, {"id": row["id"], "url": row["url"], "fetched_at": row["fetched_at"], "invalid": True}
        return data, {"id": row["id"], "url": row["url"], "fetched_at": row["fetched_at"]}

    def load_by_prefix(self, entity_type: str, endpoint_key: str, prefix: str) -> dict[str, tuple[Any, dict]]:
        """Responses of fan-out entities whose id is '<prefix><suffix>' (e.g. unit_list of '12|A'), latest per entity.
        Uses a range scan on ix_raw_entity: '}' is the character after '|'."""
        out: dict[str, tuple[Any, dict]] = {}
        hi = prefix[:-1] + chr(ord(prefix[-1]) + 1)
        for row in self.conn.execute(
                """SELECT id, url, entity_id, body_gz, fetched_at FROM raw_responses
                   WHERE entity_type=? AND entity_id>=? AND entity_id<? AND endpoint_key=?
                   ORDER BY fetched_at""", (entity_type, prefix, hi, endpoint_key)):
            if row["body_gz"] is None:
                continue
            try:
                data = json.loads(gzip.decompress(row["body_gz"]))
            except (json.JSONDecodeError, OSError):
                continue
            out[row["entity_id"]] = (data, {"id": row["id"], "url": row["url"], "fetched_at": row["fetched_at"]})
        return out

    def load_all_for_entity(self, entity_type: str, entity_id: Any) -> dict[str, tuple[Any, dict]]:
        out: dict[str, tuple[Any, dict]] = {}
        for row in self.conn.execute(
                """SELECT id, url, endpoint_key, body_gz, fetched_at FROM raw_responses
                   WHERE entity_type=? AND entity_id=? ORDER BY fetched_at""", (entity_type, str(entity_id))):
            if row["body_gz"] is None:
                continue
            try:
                data = json.loads(gzip.decompress(row["body_gz"]))
            except (json.JSONDecodeError, OSError):
                continue
            out[row["endpoint_key"]] = (data, {"id": row["id"], "url": row["url"],
                                               "fetched_at": row["fetched_at"]})
        return out

    def iter_endpoint(self, endpoint_key: str) -> Iterator[tuple[str, Any, dict]]:
        for row in self.conn.execute(
                "SELECT id, url, entity_id, body_gz, fetched_at FROM raw_responses WHERE endpoint_key=?",
                (endpoint_key,)):
            if row["body_gz"] is None:
                continue
            try:
                data = json.loads(gzip.decompress(row["body_gz"]))
            except (json.JSONDecodeError, OSError):
                continue
            yield row["entity_id"], data, {"id": row["id"], "url": row["url"], "fetched_at": row["fetched_at"]}
