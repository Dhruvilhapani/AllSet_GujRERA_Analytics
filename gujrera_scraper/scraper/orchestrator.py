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
from scraper.endpoints import (PROJECT_ENDPOINTS, PROJECT_ENDPOINTS_BY_KEY, PROMOTER_ENDPOINT, UNIT_LIST, UNIVERSE,
                               Endpoint)
from scraper.network_client import BASE_URL, FetchError, GujReraClient, Response
from scraper.project_listing import in_scope
from scraper.units import SOURCES, block_candidates

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
        self.unit_districts = {d.strip().lower() for d in (settings.get("units") or {}).get("districts") or []}
        self.unit_refetch_days = (settings.get("units") or {}).get("refetch_days", 30)
        self._unit_ids: set[str] | None = None
        # skip_fetched: do not queue a task whose URL already has a stored successful response (resume / gap filling)
        self.skip_fetched = False

    def _ids_in_districts(self, districts: set[str]) -> set[str]:
        ids: set[str] = set()
        if districts:
            for key in self.settings["target_project_types"]:
                j, _ = self.raw.load(key, key)
                for r in (j or {}).get("data") or []:
                    if in_scope(r, key, self.settings) \
                            and (r.get("districtName") or "").strip().lower() in districts:
                        ids.add(str(r["projectRegId"]))
        return ids

    def priority_ids(self) -> set[str]:
        """Project ids in the priority districts, from the stored official lists."""
        if self._priority_ids is None:
            self._priority_ids = self._ids_in_districts(self.priority_districts)
        return self._priority_ids

    def unit_scope_ids(self) -> set[str]:
        """Project ids for which the unit layer (block names, Form 3, per-block unit lists) is crawled."""
        if self._unit_ids is None:
            self._unit_ids = self._ids_in_districts(self.unit_districts)
        return self._unit_ids

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
        """Fetch dashboard counts + the full list of every target project type. Returns (rows, counts); rows are
        limited to the crawl scope (see project_listing.in_scope)."""
        counts_ep = UNIVERSE["dashboard_counts"]
        counts = self._fetch_and_store(counts_ep.path, counts_ep.key, "universe", "dashboard").json()
        rows: list[dict] = []
        for key in self.settings["target_project_types"]:
            ep = UNIVERSE[key]
            data = self._fetch_and_store(ep.path, ep.key, "universe", key).json()
            full = data.get("data") or []
            batch = [r for r in full if in_scope(r, key, self.settings)]
            log.info("%s: %d projects listed, %d in scope", key, len(full), len(batch))
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
                priority: int, body: str | None = None) -> bool:
        """Queue one task; returns False when the (run, endpoint, entity) task already exists (or, with skip_fetched,
        when its URL was already fetched successfully)."""
        if self.skip_fetched and body is None and self.conn.execute(
                "SELECT 1 FROM raw_responses WHERE url=? AND http_status=200", (BASE_URL + path.lstrip("/"),)).fetchone():
            return False
        cur = self.conn.execute(
            """INSERT OR IGNORE INTO crawl_queue(run_id, entity_type, entity_id, endpoint_key, path, priority,
               sort_key, status, updated_at, body) VALUES (?,?,?,?,?,?,?, 'pending', ?, ?)""",
            (run_id, entity_type, str(entity_id), endpoint_key, path, priority,
             queue_sort_key(entity_type, entity_id, self._boosted(entity_type, entity_id, endpoint_key)), utcnow(),
             body))
        return cur.rowcount > 0

    def enqueue_projects(self, run_id: str, project_ids: Iterable[int],
                         endpoint_keys: Iterable[str] | None = None) -> int:
        keys = set(endpoint_keys) if endpoint_keys else {e.key for e in PROJECT_ENDPOINTS}
        unit_ids = self.unit_scope_ids()
        n = 0
        self.conn.execute("BEGIN")
        for pid in project_ids:
            for ep in PROJECT_ENDPOINTS:
                if ep.key not in keys or ep.depends_on is not None:
                    continue
                if ep.family in ("units", "updates") and str(pid) not in unit_ids:
                    continue   # the unit layer is limited to the configured districts
                self.enqueue(run_id, "project", pid, ep.key, ep.path.format(id=pid), ep.priority)
                n += 1
        self.conn.execute("COMMIT")
        # dependent endpoints (formOneId / formTwoId / promoter) are enqueued once `core` is known
        self._dependent_keys = keys
        return n

    def enqueue_from_stored(self, run_id: str, project_ids: Iterable[int | str]) -> int:
        """Queue the tasks that depend on responses fetched in an earlier run (block progress / Form 2 / promoter
        from `core`; Form 3 / Form 1 from `qtr_form_ids`). Needed when those parents are skipped as already fetched."""
        before = self.conn.execute("SELECT COUNT(*) FROM crawl_queue WHERE run_id=?", (run_id,)).fetchone()[0]
        self.conn.execute("BEGIN")
        for pid in project_ids:
            core, _ = self.raw.load("core", pid)
            if isinstance(core, dict):
                self._enqueue_dependents(run_id, str(pid), core)
            qtr, _ = self.raw.load("qtr_form_ids", pid)
            if isinstance(qtr, dict) and str(pid) in self.unit_scope_ids():
                self._enqueue_latest_forms(run_id, str(pid), qtr)
        self.conn.execute("COMMIT")
        return self.conn.execute("SELECT COUNT(*) FROM crawl_queue WHERE run_id=?", (run_id,)).fetchone()[0] - before

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

    def _enqueue_latest_forms(self, run_id: str, project_id: str, qtr_json: dict) -> None:
        """The latest Form 3 / Form 1 ids come from `qtr_form_ids` (not from `core`, whose ids can be older filings);
        they are the ids the portal's own project page uses for the Form 3 header + unit list and for the facilities."""
        data = (qtr_json or {}).get("data") or {}
        if not isinstance(data, dict):
            return
        keys = getattr(self, "_dependent_keys", None) or {e.key for e in PROJECT_ENDPOINTS}
        for key, placeholder, field in (("form_three", "formThreeId", "formThreeId"),
                                        ("form_one", "formOneIdLatest", "formOneId")):
            ep = PROJECT_ENDPOINTS_BY_KEY[key]
            if data.get(field) and ep.key in keys:
                self.enqueue(run_id, "project", project_id, ep.key, ep.path.format(**{placeholder: data[field]}),
                             ep.priority)

    # ------------------------------------------------------------------ unit layer fan-out
    @staticmethod
    def unit_list_url(form_three_id, block: str) -> str:
        """Same string the client puts in Response.url / raw_responses.url for this POST."""
        return f"{BASE_URL}{UNIT_LIST.path}#blockName={block}&formThreeId={form_three_id}"

    def enqueue_unit_lists(self, run_id: str, project_ids: Iterable[int | str] | None = None) -> int:
        """Stage 2 of the unit layer: for every in-scope project whose latest Form 3 id and block names are known
        (stored raw responses), queue one POST per block. Safe to call repeatedly: queue rows are unique, and a
        block whose unit list for this exact Form 3 id was fetched within `refetch_days` is skipped, so unchanged
        projects cost nothing and a new quarterly Form 3 (new id -> new URL) is picked up automatically."""
        wanted = {str(p) for p in project_ids} if project_ids is not None else None
        scope = self.unit_scope_ids()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.unit_refetch_days)).replace(microsecond=0).isoformat()
        n = skipped = 0
        self.conn.execute("BEGIN")
        for pid, qtr, _meta in self.raw.iter_endpoint("qtr_form_ids"):
            if pid not in scope or (wanted is not None and pid not in wanted):
                continue
            data = qtr.get("data") if isinstance(qtr, dict) else None
            ft = data.get("formThreeId") if isinstance(data, dict) else None
            if not ft:
                continue   # no Form 3 published: the parser records unit_data_state = not_published
            sources = {k: self.raw.load(k, pid)[0] for k in SOURCES}
            names, _expected, _src = block_candidates(sources)
            for name in names:
                old = self.conn.execute("SELECT http_status, fetched_at FROM raw_responses WHERE url=?",
                                        (self.unit_list_url(ft, name),)).fetchone()
                if old is not None and old["http_status"] == 200 and (old["fetched_at"] or "") >= cutoff:
                    skipped += 1
                    continue
                if self.enqueue(run_id, "project_block", f"{pid}|{name}", UNIT_LIST.key, UNIT_LIST.path,
                                UNIT_LIST.priority, body=json.dumps({"blockName": name, "formThreeId": ft})):
                    n += 1
        self.conn.execute("COMMIT")
        log.info("unit lists: %d block tasks queued, %d skipped (fresh for the same Form 3)", n, skipped)
        return n

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
        ep = PROJECT_ENDPOINTS_BY_KEY.get(row["endpoint_key"])
        retries = 1 if ep is not None and ep.optional else None
        if row["body"]:
            return self.client.post_json(row["path"], json.loads(row["body"]), store=False)
        return self.client.get(row["path"], store=False, max_retries=retries)

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
                ep = PROJECT_ENDPOINTS_BY_KEY.get(row["endpoint_key"])
                status = "empty" if e.status in EMPTY_STATUSES or (ep is not None and ep.optional and e.status == 500) \
                    else None
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
            elif row["endpoint_key"] == "qtr_form_ids":
                try:
                    self._enqueue_latest_forms(run_id, row["entity_id"], r.json())
                except (json.JSONDecodeError, AttributeError) as e:
                    log.warning("qtr_form_ids response for %s not usable: %r", row["entity_id"], e)


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
