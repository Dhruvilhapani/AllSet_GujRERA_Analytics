"""GujRERA Residential + Mixed-use pipeline — command line.

  python cli.py discover                      # universe counts + coverage/duplicate check (no detail crawl)
  python cli.py poc [--ids 62 184 ...]        # proof of concept on ~10 projects into data/poc.db
  python cli.py crawl --mode full             # full crawl (persistent queue)
  python cli.py crawl --resume                # continue the latest unfinished run from its checkpoint
  python cli.py crawl --mode incremental      # only new/changed projects get a full refresh
  python cli.py crawl --retry-failed          # move the failure queue back to pending and resume
  python cli.py parse                         # rebuild normalized tables from raw responses
  python cli.py docs [--limit N]              # download + parse analytics-critical PDFs
  python cli.py analyze                       # metrics + analytical risk score
  python cli.py export                        # CSV / Excel / executive report
  python cli.py status                        # queue + table counts
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from database.db import RawStore, connect, init_schema  # noqa: E402
from settings import settings  # noqa: E402

log = logging.getLogger("gujrera")


def setup_logging(verbose: bool) -> None:
    Path(settings()["database_path"]).parent.mkdir(parents=True, exist_ok=True)
    handlers = [logging.StreamHandler(sys.stdout),
                logging.FileHandler(Path(settings()["database_path"]).parent / "pipeline.log", encoding="utf-8")]
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_conn(db: str | None):
    conn = connect(db or settings()["database_path"])
    init_schema(conn)
    return conn


_LOCKS = []  # keep lock file handles open for the life of the process


def single_instance(name: str) -> None:
    """Exit if another process already runs `name` (crawl / refresh-app). The OS releases the lock when the
    process ends, even after a crash, so a stale lock never blocks a restart."""
    path = Path(settings()["database_path"]).parent / f"{name}.lock"
    fh = open(path, "a+")  # noqa: SIM115 - must stay open to hold the lock
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(f"Another '{name}' process is already running — not starting a second one.")
        sys.exit(2)
    _LOCKS.append(fh)


def make_client(conn, s):
    from scraper.network_client import GujReraClient
    n = s["network"]
    return GujReraClient(raw_store=None, rate_per_sec=n["rate_per_sec"], max_retries=n["max_retries"],
                         timeout=n["timeout_sec"])


# ---------------------------------------------------------------------------------------------- commands
def cmd_discover(args):
    from scraper.orchestrator import Crawler
    from scraper.project_listing import validate_universe
    s = settings()
    conn = get_conn(args.db)
    with make_client(conn, s) as client:
        crawler = Crawler(conn, client, s)
        crawler.fetch_universe()
    raw = RawStore(conn)
    counts, _ = raw.load("dashboard_counts", "dashboard")
    rows = {k: (raw.load(k, k)[0] or {}).get("data") or [] for k in s["target_project_types"]}
    print(json.dumps(validate_universe(rows, counts), indent=2))


def cmd_poc(args):
    from parser.pipeline import parse_all
    from scraper.documents import DocumentDownloader
    from scraper.orchestrator import Crawler
    from scraper.project_detail import fetch_project
    from scraper.project_listing import validate_universe
    from scraper import complaints as cm
    s = settings()
    db = args.db or str(Path(s["database_path"]).with_name("poc.db"))
    conn = get_conn(db)
    raw = RawStore(conn)
    print(f"[1] database: {db}")
    with make_client(conn, s) as client:
        crawler = Crawler(conn, client, s)
        rows, counts = crawler.fetch_universe()
        lists = {k: (raw.load(k, k)[0] or {}).get("data") or [] for k in s["target_project_types"]}
        print("[2] universe:", json.dumps(validate_universe(lists, counts)))
        ids = args.ids or pick_sample(rows, 10)
        print(f"[3] sample projects ({len(ids)}): {ids}")
        for pid in ids:
            r = fetch_project(client, raw, pid)
            print(f"    project {pid}: ok={len(r['ok'])} empty={r['empty']} failed={r['failed']} ids={r['ids']}")
        # complaints for the first project's district (+ 'Others' suo-motu list) to prove the join
        first = next(r for r in rows if int(r["projectRegId"]) == ids[0])
        summary = crawler._fetch_and_store(cm.SUMMARY_PATH, "complaint_summary", "complaint_list", "summary").json()
        district = first.get("districtName")
        tasks = [t for t in cm.list_tasks(summary)
                 if t[0] == "complaint_list" and (t[1].split("|")[1].lower() == (district or "").lower()
                                                  or t[1] == "SOUMOTO|OTHERS")]
        tasks += [t for t in cm.list_tasks(summary) if t[0] != "complaint_list"]
        for key, eid, path in tasks:
            try:
                crawler._fetch_and_store(path, key, "complaint_list", eid)
            except Exception as e:  # noqa: BLE001
                print(f"    complaint list {eid} failed: {e}")
        print(f"[4] complaint lists fetched: {len(tasks)} (district={district})")
        stats = parse_all(conn, s, project_ids=ids)
        print("[5] parse:", json.dumps(stats))
        dl = DocumentDownloader(conn, client, s)
        dstats = dl.run(project_ids=ids, types=["registration_certificate", "extension_certificate", "qe_form_four",
                                                 "completion_certificate", "complaint_order", "suo_motu_order"])
        print("[6] documents:", json.dumps(dstats))
    parse_all(conn, s, project_ids=ids)  # re-apply certificate fields after download
    print_poc_report(conn, ids)


def pick_sample(rows: list[dict], n: int) -> list[int]:
    """Deterministic stratified sample: both categories x statuses, with/without extensions."""
    picked: list[int] = []
    buckets: dict[tuple, list[int]] = {}
    for r in rows:
        k = (r.get("projectType"), r.get("project_status"), "/EX" in (r.get("regNo") or ""))
        buckets.setdefault(k, []).append(int(r["projectRegId"]))
    if any(int(r["projectRegId"]) == 62 for r in rows):
        picked.append(62)
    while len(picked) < n and any(buckets.values()):
        for k in sorted(buckets, key=str):
            if buckets[k] and len(picked) < n:
                pid = buckets[k].pop(0)
                if pid not in picked:
                    picked.append(pid)
    return picked


def print_poc_report(conn, ids):
    tables = ["projects", "project_locations", "developers", "developer_aliases", "project_developers",
              "project_blocks", "project_units", "project_progress", "project_quarters", "project_extensions",
              "project_applications", "annual_compliance", "complaints", "regulatory_actions", "appeals",
              "project_documents", "field_provenance", "field_conflicts"]
    print("[7] table counts:")
    for t in tables:
        print(f"    {t:24s} {conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]}")
    d = conn.execute("SELECT download_status, text_status, COUNT(*) n FROM project_documents "
                     "WHERE download_priority='critical' GROUP BY 1,2").fetchall()
    print("    critical documents:", [dict(x) for x in d])
    p = conn.execute("""SELECT p.project_id, p.reg_no_current, p.project_name, p.status_source, p.status_derived,
                        p.original_completion_date, p.current_completion_date, p.extension_count, p.latest_progress_pct,
                        p.total_units, p.booked_units, p.unbooked_units, d.canonical_name developer
                        FROM projects p LEFT JOIN project_developers pd ON pd.project_id=p.project_id
                        LEFT JOIN developers d ON d.developer_id=pd.developer_id WHERE p.project_id=?""",
                     (ids[0],)).fetchone()
    print("[8] example project:", json.dumps(dict(p), indent=2, default=str))


def cmd_crawl(args):
    from scraper.orchestrator import Crawler, plan_incremental
    s = settings()
    single_instance("crawl")
    conn = get_conn(args.db)
    with make_client(conn, s) as client:
        crawler = Crawler(conn, client, s)
        signal.signal(signal.SIGINT, lambda *_: (print("\nstopping after in-flight requests..."), crawler.stop()))
        if args.resume or args.retry_failed:
            run_id = args.run_id or crawler.latest_unfinished_run()
            if not run_id:
                print("no unfinished run to resume")
                return
            if args.retry_failed:
                print(f"reset {crawler.reset_failed(run_id)} failed tasks to pending")
        else:
            run_id = crawler.new_run(args.mode, notes=json.dumps({"limit": args.limit}))
            rows, counts = crawler.fetch_universe()
            ids = [int(r["projectRegId"]) for r in rows]
            if args.limit:
                ids = sample_ids(ids, args.limit)
            conn.execute("UPDATE crawl_runs SET universe_count=? WHERE run_id=?", (len(rows), run_id))
            if args.mode == "incremental":
                full, volatile = plan_incremental(conn, [r for r in rows if int(r["projectRegId"]) in set(ids)], s)
                n = crawler.enqueue_projects(run_id, full)
                n += crawler.enqueue_projects(run_id, volatile, s["incremental"]["volatile_endpoints"])
                print(f"incremental: {len(full)} projects full refresh, {len(volatile)} volatile-only")
            else:
                n = crawler.enqueue_projects(run_id, ids)
            if not args.skip_complaints:
                n += crawler.enqueue_complaint_lists(run_id)
            print(f"run {run_id}: enqueued {n} tasks for {len(ids)} projects")
        res = crawler.run_queue(run_id, max_tasks=args.max_tasks)
        print(json.dumps(res, indent=2))
        st = res["queue"]
        if not st.get("pending") and not crawler._stop.is_set():
            crawler.finish_run(run_id)
            print(f"run {run_id} finished. failed tasks: {st.get('failed', 0)} (see `status`, `crawl --retry-failed`)")


def sample_ids(ids: list[int], n: int) -> list[int]:
    """Evenly spaced deterministic sample across the id range (covers old and new registrations)."""
    ids = sorted(ids)
    if n >= len(ids):
        return ids
    step = len(ids) / n
    return [ids[int(i * step)] for i in range(n)]


def cmd_parse(args):
    from parser.pipeline import parse_all
    s = settings()
    conn = get_conn(args.db)
    run = conn.execute("SELECT run_id FROM crawl_runs ORDER BY started_at DESC LIMIT 1").fetchone()
    stats = parse_all(conn, s, run_id=run["run_id"] if run else None,
                      project_ids=[int(x) for x in args.ids] if args.ids else None)
    print(json.dumps(stats, indent=2))


def cmd_docs(args):
    from scraper.documents import DocumentDownloader
    s = settings()
    conn = get_conn(args.db)
    ids = None
    if args.district:
        ids = [r[0] for r in conn.execute("SELECT project_id FROM project_locations WHERE LOWER(TRIM(district))=?",
                                          (args.district.strip().lower(),))]
    with make_client(conn, s) as client:
        dl = DocumentDownloader(conn, client, s)
        print(json.dumps(dl.run(limit=args.limit, types=args.types, project_ids=ids), indent=2))


def cmd_area(args):
    from analytics.area_report import build_area_report
    from analytics.dashboard import build_dashboard
    conn = get_conn(args.db)
    out = build_area_report(conn, settings(), args.district, args.out)
    out["dashboard"] = build_dashboard(conn, settings(), args.district,
                                       str(Path(args.out) / f"{args.district.lower()}_dashboard.html") if args.out else None)
    print(json.dumps(out, indent=2, default=str))


def default_app_path(district: str) -> str:
    return str(Path(settings()["database_path"]).parent / "app" / f"gujrera_{district.lower()}.db")


def cmd_publish_app(args):
    from analytics.app_export import publish
    conn = get_conn(args.db)
    print(json.dumps(publish(conn, settings(), args.district, args.out or default_app_path(args.district)), indent=2))


def cmd_refresh_app(args):
    """Copy the live crawl DB (online backup; the crawler keeps running) -> parse the copy -> publish the
    web app snapshot. With --every N the cycle repeats every N minutes so the web app follows the crawl."""
    import sqlite3
    import time

    from datetime import datetime, timedelta

    from analytics.app_export import crawl_status, publish
    from parser.pipeline import parse_all
    s = settings()
    if args.every:
        single_instance("refresh-app")
    live = args.db or s["database_path"]
    snap = str(Path(live).with_name("snapshot_app.db"))

    def cycle():
        t0 = time.monotonic()
        src = sqlite3.connect(live, timeout=120)
        status = crawl_status(src)
        dst = sqlite3.connect(snap)
        src.backup(dst)
        dst.close()
        src.close()
        conn = get_conn(snap)
        try:
            parse_all(conn, s)
            out = publish(conn, s, args.district, args.out or default_app_path(args.district), live_status=status)
        finally:
            conn.close()
        log.info("app snapshot published in %.0fs: %s analysed / %s total, crawl %s", time.monotonic() - t0,
                 out["projects_analyzed"], out["projects_total"], out["crawl_state"])
        print(json.dumps(out), flush=True)

    while True:
        started = datetime.now()
        try:
            cycle()
        except Exception:  # noqa: BLE001 - a failed refresh must not stop the next one
            log.exception("app snapshot refresh failed; retrying next cycle")
            if not args.every:
                raise
        if not args.every:
            return
        # Wall-clock schedule checked every 15 s: after the computer sleeps, the next refresh runs as soon as it
        # wakes instead of waiting out a timer that was suspended with it.
        due = started + timedelta(minutes=args.every)
        while datetime.now() < due:
            time.sleep(15)


def cmd_analyze(args):
    from analytics.run import run_analytics
    conn = get_conn(args.db)
    print(json.dumps(run_analytics(conn, settings()), indent=2, default=str))


def cmd_export(args):
    from exports.exporter import export_all
    conn = get_conn(args.db)
    out = export_all(conn, settings(), args.out)
    print(json.dumps(out, indent=2, default=str))


def cmd_status(args):
    conn = get_conn(args.db)
    for r in conn.execute("SELECT * FROM crawl_runs ORDER BY started_at DESC LIMIT 5"):
        q = conn.execute("SELECT status, COUNT(*) n FROM crawl_queue WHERE run_id=? GROUP BY status",
                         (r["run_id"],)).fetchall()
        print(f"run {r['run_id']} mode={r['mode']} started={r['started_at']} finished={r['finished_at']} "
              f"universe={r['universe_count']} queue={ {x['status']: x['n'] for x in q} }")
        fails = conn.execute("SELECT endpoint_key, COUNT(*) n, MIN(last_error) e FROM crawl_queue WHERE run_id=? AND "
                             "status IN ('failed','empty') GROUP BY endpoint_key", (r["run_id"],)).fetchall()
        for f in fails:
            print(f"    {f['endpoint_key']}: {f['n']} failed/empty  e.g. {f['e']}")
    for t in ["raw_responses", "projects", "developers", "complaints", "regulatory_actions", "project_documents"]:
        print(f"{t:22s} {conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", help="database path (default from config/settings.yaml)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("discover")
    p = sub.add_parser("poc")
    p.add_argument("--ids", nargs="*", type=int)
    p = sub.add_parser("crawl")
    p.add_argument("--mode", choices=["full", "incremental", "sample"], default="full")
    p.add_argument("--limit", type=int, help="only crawl N evenly spaced projects (validation runs)")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--retry-failed", action="store_true")
    p.add_argument("--run-id")
    p.add_argument("--max-tasks", type=int)
    p.add_argument("--skip-complaints", action="store_true")
    p = sub.add_parser("parse")
    p.add_argument("--ids", nargs="*")
    p = sub.add_parser("docs")
    p.add_argument("--limit", type=int)
    p.add_argument("--types", nargs="*")
    p.add_argument("--district", help="only documents of projects in this district")
    p = sub.add_parser("area", help="district builder-intelligence report (e.g. Ahmedabad)")
    p.add_argument("--district", required=True)
    p.add_argument("--out")
    p = sub.add_parser("publish-app", help="write the read-only web app snapshot from an already-parsed DB")
    p.add_argument("--district", required=True)
    p.add_argument("--out")
    p = sub.add_parser("refresh-app", help="copy live DB, parse, publish web app snapshot (optionally repeat)")
    p.add_argument("--district", required=True)
    p.add_argument("--out")
    p.add_argument("--every", type=int, help="repeat every N minutes")
    sub.add_parser("analyze")
    p = sub.add_parser("export")
    p.add_argument("--out")
    sub.add_parser("status")
    args = ap.parse_args(argv)
    setup_logging(args.verbose)
    {"discover": cmd_discover, "poc": cmd_poc, "crawl": cmd_crawl, "parse": cmd_parse, "docs": cmd_docs,
     "analyze": cmd_analyze, "export": cmd_export, "status": cmd_status, "area": cmd_area,
     "publish-app": cmd_publish_app, "refresh-app": cmd_refresh_app}[args.cmd](args)


if __name__ == "__main__":
    main()
