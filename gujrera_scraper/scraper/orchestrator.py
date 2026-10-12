"""Crawl orchestration: universe discovery, a persistent task queue, resumable workers.

Every unit of work (one endpoint for one entity) is a row in `crawl_queue`. Rows are only marked
`done` after the response has been persisted, so killing the process at any point loses at most the
in-flight requests; `crawl --resume` continues from the checkpoint.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from typing import Iterable

from database.db import RawStore, utcnow
from scraper import complaints as complaints_mod
from scraper.endpoints import (PROJECT_ENDPOINTS, PROJECT_ENDPOINTS_BY_KEY, PROMOTER_ENDPOINT, UNIVERSE,
                               Endpoint)
from scraper.network_client import FetchError, GujReraClient, Response

log = logging.getLogger(__name__)

EMPTY_STATUSES = {400, 404}   # deterministic "no data for this entity" answers: not retried
NETWORK_PAUSE_S = 60          # pause after a request could not reach the server at all
_CLASS = {"complaint_list": 0, "promoter": 1, "project": 2}


def queue_sort_key(entity_type: str, entity_id, boosted: bool = False) -> int:
    """Lists first, then promoters, then priority-district projects (analysis endpoints only), then all other
    project work in a deterministic pseudo-random order (Knuth multiplicative hash of the id). Work is
    project-major, and any prefix of a class is an unbiased sample of it."""
    try:
        n = int(entity_id)
    except (TypeError, ValueError):
        n = int(hashlib.sha256(str(entity_id).encode()).hexdigest()[:8], 16)
    cls = 15 if boosted else _CLASS.get(entity_type, 3) * 10
    return cls * 10**9 + (n * 2654435761) % 4294967296


class Crawler:
    def __init__(self, conn: sqlite3.Connection, client: GujReraClient, settings: dict):
        self.conn = conn
        self.client = client
        self.raw = RawStore(conn)
        self.settings = settings
        self.workers = settings["network"]["workers"]
        self.max_attempts = settings["network"]["max_task_attempts"]
        self._db_lock = threading.Lock()
        self._stop = threading.Event()
        self._net_pause_until = 0.0
        pr = settings.get("priority") or {}
        self.priority_endpoints = set(pr.get("endpoints") or [])
        self.priority_districts = {d.strip().lower() for d in pr.get("districts") or []}
        self._priority_ids: set[str] | None = None

    def priority_ids(self) -> set[str]:
        """Project ids in the priority districts, from the stored official lists."""
        if self._priority_ids is None:
            ids: set[str] = set()
            if self.priority_districts:
                for key in self.settings["target_project_types"]:
                    j, _ = self.raw.load(key, key)
                    for r in (j or {}).get("data") or []:
                        if (r.get("districtName") or "").strip().lower() in self.priority_districts:
                            ids.add(str(r["projectRegId"]))
            self._priority_ids = ids
        return self._priority_ids

    def _boosted(self, entity_type: str, entity_id, endpoint_key: str) -> bool:
        return entity_type == "project" and endpoint_key in self.priority_endpoints \
            and str(entity_id) in self.priority_ids()

    # ------------------------------------------------------------------ runs
    def new_run(self, mode: str, notes: str = "") -> str:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + mode + "-" + uuid.uuid4().hex[:6]
        self.conn.execute("INSERT INTO crawl_runs(run_id, mode, started_at, notes) VALUES (?,?,?,?)",
                          (run_id, mode, utcnow(), notes))
        return run_id

    def latest_unfinished_run(self) -> str | None:
        row = self.conn.execute(
            "SELECT run_id FROM crawl_runs WHERE finished_at IS NULL ORDER BY started_at DESC LIMIT 1").fetchone()
        return row["run_id"] if row else None

    def finish_run(self, run_id: str) -> None:
        self.conn.execute("UPDATE crawl_runs SET finished_at=? WHERE run_id=?", (utcnow(), run_id))

    # ------------------------------------------------------------------ universe
    def fetch_universe(self) -> tuple[list[dict], dict]:
        """Fetch dashboard counts + full Residential and Mixed lists. Returns (rows, counts)."""
        counts_ep = UNIVERSE["dashboard_counts"]
        counts = self._fetch_and_store(counts_ep.path, counts_ep.key, "universe", "dashboard").json()
        rows: list[dict] = []
        for key in self.settings["target_project_types"]:
            ep = UNIVERSE[key]
            data = self._fetch_and_store(ep.path, ep.key, "universe", key).json()
            batch = data.get("data") or []
            log.info("%s: %d projects", key, len(batch))
            rows.extend(batch)
        return rows, counts

    def _fetch_and_store(self, path: str, endpoint_key: str, entity_type: str, entity_id) -> Response:
        r = self.client.get(path, store=False)
        with self._db_lock:
            self.raw.save_raw(url=r.url, status=r.status, content_type=r.content_type, body=r.content,
                              sha256=r.sha256, size=len(r.content), endpoint_key=endpoint_key,
                              entity_type=entity_type, entity_id=entity_id)
        return r

    # ------------------------------------------------------------------ queue
    def enqueue(self, run_id: str, entity_type: str, entity_id, endpoint_key: str, path: str,
                priority: int) -> None:
        self.conn.execute(
            """INSERT OR IGNORE INTO crawl_queue(run_id, entity_type, entity_id, endpoint_key, path, priority,
               sort_key, status, updated_at) VALUES (?,?,?,?,?,?,?, 'pending', ?)""",
            (run_id, entity_type, str(entity_id), endpoint_key, path, priority,
             queue_sort_key(entity_type, entity_id, self._boosted(entity_type, entity_id, endpoint_key)), utcnow()))

    def enqueue_projects(self, run_id: str, project_ids: Iterable[int],
                         endpoint_keys: Iterable[str] | None = None) -> int:
        keys = set(endpoint_keys) if endpoint_keys else {e.key for e in PROJECT_ENDPOINTS}
        n = 0
        self.conn.execute("BEGIN")
        for pid in project_ids:
            for ep in PROJECT_ENDPOINTS:
                if ep.key in keys and ep.depends_on is None:
                    self.enqueue(run_id, "project", pid, ep.key, ep.path.format(id=pid), ep.priority)
                    n += 1
        self.conn.execute("COMMIT")
        # dependent endpoints (formOneId / formTwoId / promoter) are enqueued once `core` is known
        self._dependent_keys = keys
        return n

    def enqueue_complaint_lists(self, run_id: str) -> int:
        summary = self._fetch_and_store(complaints_mod.SUMMARY_PATH, "complaint_summary", "complaint_list",
                                        "summary").json()
        tasks = complaints_mod.list_tasks(summary)
        for key, entity_id, path in tasks:
            self.enqueue(run_id, "complaint_list", entity_id, key, path, 40)
        return len(tasks)

    def _enqueue_dependents(self, run_id: str, project_id: str, core_json: dict) -> None:
        data = (core_json or {}).get("data") or {}
        keys = getattr(self, "_dependent_keys", None) or {e.key for e in PROJECT_ENDPOINTS}
        ids = {"formOneId": data.get("formOneId"), "formTwoId": data.get("formTwoId")}
        for ep in PROJECT_ENDPOINTS:
            if ep.depends_on and ep.key in keys and ids.get(ep.depends_on):
                self.enqueue(run_id, "project", project_id, ep.key,
                             ep.path.format(**{ep.depends_on: ids[ep.depends_on]}), ep.priority)
        promoter_id = data.get("promoterId")
        if promoter_id:
            self.enqueue(run_id, "promoter", promoter_id, PROMOTER_ENDPOINT.key,
                         PROMOTER_ENDPOINT.path.format(promoterId=promoter_id), PROMOTER_ENDPOINT.priority)

    def queue_status(self, run_id: str) -> dict[str, int]:
        rows = self.conn.execute("SELECT status, COUNT(*) n FROM crawl_queue WHERE run_id=? GROUP BY status",
                                 (run_id,)).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def reset_failed(self, run_id: str) -> int:
        cur = self.conn.execute(
            "UPDATE crawl_queue SET status='pending', attempts=0 WHERE run_id=? AND status='failed'", (run_id,))
        return cur.rowcount

    # ------------------------------------------------------------------ worker loop
    def prepare_queue(self, run_id: str) -> None:
        """(Re)compute sort keys of pending tasks (priority settings may have changed) and ensure the index exists."""
        rows = self.conn.execute("SELECT id, entity_type, entity_id, endpoint_key, sort_key FROM crawl_queue "
                                 "WHERE run_id=? AND status='pending'", (run_id,)).fetchall()
        updates = []
        for r in rows:
            k = queue_sort_key(r["entity_type"], r["entity_id"],
                               self._boosted(r["entity_type"], r["entity_id"], r["endpoint_key"]))
            if k != r["sort_key"]:
                updates.append((k, r["id"]))
        if updates:
            self.conn.execute("BEGIN")
            self.conn.executemany("UPDATE crawl_queue SET sort_key=? WHERE id=?", updates)
            self.conn.execute("COMMIT")
            log.info("re-keyed %d pending tasks (priority districts: %s, %d projects)", len(updates),
                     sorted(self.priority_districts), len(self.priority_ids()))
        self.conn.execute("CREATE INDEX IF NOT EXISTS ix_queue_sort ON crawl_queue(run_id, status, sort_key, priority, id)")

    def run_queue(self, run_id: str, max_tasks: int | None = None, progress_every: int = 500) -> dict:
        self.prepare_queue(run_id)
        done = 0
        t0 = time.monotonic()
        inflight: dict[Future, sqlite3.Row] = {}
        with ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="crawl") as pool:
            while not self._stop.is_set():
                paused = time.monotonic() < self._net_pause_until
                if paused and not inflight:
                    # No connection: wait (interruptibly), then try again. Nothing is marked failed.
                    self._stop.wait(min(5.0, self._net_pause_until - time.monotonic()))
                    continue
                capacity = self.workers * 2 - len(inflight)
                if not paused and capacity > 0 and (max_tasks is None or done + len(inflight) < max_tasks):
                    taken_ids = [str(r["id"]) for r in inflight.values()]
                    q = ("SELECT * FROM crawl_queue WHERE run_id=? AND status='pending' AND attempts < ? "
                         + (f"AND id NOT IN ({','.join(taken_ids)}) " if taken_ids else "")
                         + "ORDER BY sort_key, priority, id LIMIT ?")
                    for row in self.conn.execute(q, (run_id, self.max_attempts, capacity)).fetchall():
                        inflight[pool.submit(self._fetch_task, row)] = row
                if not inflight:
                    break
                finished, _ = wait(list(inflight), timeout=5, return_when=FIRST_COMPLETED)
                for fut in finished:
                    row = inflight.pop(fut)
                    self._record_result(run_id, row, fut)
                    done += 1
                    if done % progress_every == 0:
                        rate = done / max(time.monotonic() - t0, 1e-6)
                        st = self.queue_status(run_id)
                        log.info("progress: %d tasks this session (%.2f/s) queue=%s client=%s",
                                 done, rate, st, self.client.stats)
        return {"processed": done, "queue": self.queue_status(run_id), "client": dict(self.client.stats)}

    def stop(self) -> None:
        self._stop.set()

    def _fetch_task(self, row: sqlite3.Row):
        return self.client.get(row["path"], store=False)

    def _record_result(self, run_id: str, row: sqlite3.Row, fut: Future) -> None:
        now = utcnow()
        with self._db_lock:
            try:
                r: Response = fut.result()
            except FetchError as e:
                if e.network:
                    # The request never reached GujRERA (internet down / laptop asleep / DNS). Keep the task
                    # pending without spending an attempt and pause the whole crawl for a minute.
                    if time.monotonic() >= self._net_pause_until:
                        log.warning("network unavailable (%s); pausing crawl for %ds", str(e)[:160], NETWORK_PAUSE_S)
                    self._net_pause_until = time.monotonic() + NETWORK_PAUSE_S
                    self.conn.execute("UPDATE crawl_queue SET last_error=?, updated_at=? WHERE id=?",
                                      (str(e)[:500], now, row["id"]))
                    return
                status = "empty" if e.status in EMPTY_STATUSES else None
                attempts = row["attempts"] + 1
                if status is None:
                    status = "failed" if attempts >= self.max_attempts else "pending"
                self.conn.execute("UPDATE crawl_queue SET status=?, attempts=?, last_error=?, updated_at=? "
                                  "WHERE id=?", (status, attempts, str(e)[:500], now, row["id"]))
                return
            except Exception as e:  # noqa: BLE001 - keep the crawl alive, record the error
                attempts = row["attempts"] + 1
                status = "failed" if attempts >= self.max_attempts else "pending"
                self.conn.execute("UPDATE crawl_queue SET status=?, attempts=?, last_error=?, updated_at=? "
                                  "WHERE id=?", (status, attempts, repr(e)[:500], now, row["id"]))
                return
            changed = self.raw.save_raw(url=r.url, status=r.status, content_type=r.content_type, body=r.content,
                                        sha256=r.sha256, size=len(r.content), endpoint_key=row["endpoint_key"],
                                        entity_type=row["entity_type"], entity_id=row["entity_id"])
            self.conn.execute("UPDATE crawl_queue SET status='done', attempts=attempts+1, changed=?, "
                              "last_error=NULL, updated_at=? WHERE id=?", (int(changed), now, row["id"]))
            if row["endpoint_key"] == "core":
                try:
                    self._enqueue_dependents(run_id, row["entity_id"], r.json())
                except (json.JSONDecodeError, AttributeError) as e:
                    log.warning("core response for %s not usable: %r", row["entity_id"], e)


# ---------------------------------------------------------------------- incremental helpers
def list_row_hash(row: dict) -> str:
    return hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()


def plan_incremental(conn: sqlite3.Connection, rows: list[dict], settings: dict) -> tuple[list[int], list[int]]:
    """Split the universe into (projects needing a full fetch, projects needing only volatile endpoints)."""
    known = {r["project_id"]: r for r in conn.execute(
        "SELECT project_id, last_scraped_at, list_row_hash FROM projects").fetchall()}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=settings["incremental"]["full_refresh_days"])).isoformat()
    full, volatile = [], []
    for row in rows:
        pid = int(row["projectRegId"])
        if pid not in known or known[pid]["list_row_hash"] != list_row_hash(row) \
                or (known[pid]["last_scraped_at"] or "") < cutoff:
            full.append(pid)
        else:
            volatile.append(pid)
    return full, volatile


def endpoint(key: str) -> Endpoint:
    return PROJECT_ENDPOINTS_BY_KEY[key]
