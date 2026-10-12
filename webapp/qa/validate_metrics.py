"""Offline QA: recompute the app's metrics independently and compare them with the API.

The recomputation uses plain sqlite3 + Python (not the pandas code path the server uses):

* yearly progress  <- the PIPELINE database's project_progress (physical_progress_pct), so the snapshot
                      publisher is checked too;
* extensions       <- the PIPELINE database's project_extensions (approved only);
* status / sales % <- the snapshot's project rows.

Project -> developer membership is taken from the snapshot (entity resolution is the publisher's job).
Matplotlib PNGs are written for visual review. Matplotlib is a QA-only dependency; the server never imports it.

Usage (from the project root):
    .venv/Scripts/python.exe webapp/qa/validate_metrics.py
    .venv/Scripts/python.exe webapp/qa/validate_metrics.py --pipeline-db gujrera_scraper/data/dev_copy.db \
        --snapshot gujrera_scraper/data/app/dev_ahmedabad.db --developers 10

Exit code 0 = every compared metric matches; 1 = at least one mismatch (listed).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import date
from itertools import pairwise
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "out"
TOLERANCE = 0.06  # API rounds to 1 decimal
MARKET_MIN_N = 10  # documented in METRICS.md; re-stated here so the QA does not import the server constant


def ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def yearly_progress(rows, as_of: str, min_projects: int = 1) -> dict[int, tuple[float, int]]:
    """rows: (project_id, seq, period_date, pct). Last report per project-year, capped, mean per year."""
    last: dict[tuple[int, int], tuple[str, int, float]] = {}
    for pid, seq, period, pct in rows:
        if period is None or pct is None or period > as_of:
            continue
        key = (pid, int(period[:4]))
        if key not in last or (period, seq) > last[key][:2]:
            last[key] = (period, seq, min(max(float(pct), 0.0), 100.0))
    per_year: dict[int, list[float]] = defaultdict(list)
    for (_, year), (_, _, pct) in last.items():
        per_year[year].append(pct)
    return {y: (sum(v) / len(v), len(v)) for y, v in sorted(per_year.items()) if len(v) >= min_projects}


def future_points(rows, as_of: str) -> int:
    return sum(1 for _, _, period, pct in rows if period is not None and pct is not None and period > as_of)


class Checker:
    def __init__(self):
        self.checked = 0
        self.mismatches: list[str] = []

    def eq(self, label, expected, actual):
        self.checked += 1
        ok = (
            abs(expected - actual) <= TOLERANCE
            if isinstance(expected, float) and isinstance(actual, (int, float))
            else expected == actual
        )
        if not ok:
            self.mismatches.append(f"{label}: expected {expected!r}, API {actual!r}")


def check_progress(chk: Checker, scope: str, expected: dict, api: dict, as_of: str, pipe_rows) -> None:
    got = {p["year"]: p for p in api["points"]}
    chk.eq(f"{scope} progress years", list(expected), list(got))
    for year, (mean, n) in expected.items():
        if year in got:
            chk.eq(f"{scope} {year} avg_progress", round(mean, 1), got[year]["avg_progress"])
            chk.eq(f"{scope} {year} n_projects", n, got[year]["n_projects"])
    chk.eq(f"{scope} excluded future points", future_points(pipe_rows, as_of), api["excluded_future_points"])


def status_counts(projects) -> dict:
    a = [p for p in projects if p["detail_crawled"] == 1]
    out = {s.lower(): sum(1 for p in a if p["status"] == s) for s in ("Completed", "Ongoing", "Delayed", "Pending")}
    out["not_crawled"] = len(projects) - len(a)
    return out


def plot(points_by_scope: dict, revisions: list[float]) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    OUT.mkdir(parents=True, exist_ok=True)
    written = []
    for i, (scope, pts) in enumerate(points_by_scope.items()):
        if not pts:
            continue
        fig, ax = plt.subplots(figsize=(7, 3.2))
        years = [p["year"] for p in pts]
        ax.plot(years, [p["avg_progress"] for p in pts], marker="o", color="#2a78d6", label="Average (API)")
        weighted = [(p["year"], p["weighted_progress"]) for p in pts if p["weighted_progress"] is not None]
        if weighted:
            ax.plot(
                *zip(*weighted, strict=True), marker=".", linestyle="--", color="#eb6834", label="Units-weighted (QA)"
            )
        for p in pts:
            ax.annotate(
                f"n={p['n_projects']}",
                (p["year"], p["avg_progress"]),
                textcoords="offset points",
                xytext=(0, 6),
                ha="center",
                fontsize=7,
                color="#667085",
            )
        ax.set_ylim(0, 105)
        ax.set_ylabel("Progress %")
        ax.set_title(f"Project progress by year — {scope}", fontsize=10)
        ax.grid(axis="y", color="#e9ecf0")
        ax.legend(fontsize=8, frameon=False)
        fig.tight_layout()
        path = OUT / f"{i:02d}_progress_{''.join(c if c.isalnum() else '_' for c in scope)[:40]}.png"
        fig.savefig(path, dpi=110)
        plt.close(fig)
        written.append(path)
    if revisions:
        fig, ax = plt.subplots(figsize=(7, 3.2))
        ax.hist(revisions, bins=20, color="#2a78d6")
        ax.set_xlabel("Drop between consecutive reports (percentage points)")
        ax.set_ylabel("Reports")
        ax.set_title("Published progress revisions (drops > 30 pp)", fontsize=10)
        fig.tight_layout()
        path = OUT / "progress_revisions.png"
        fig.savefig(path, dpi=110)
        plt.close(fig)
        written.append(path)
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pipeline-db", default=str(ROOT / "gujrera_scraper/data/dev_copy.db"))
    ap.add_argument("--snapshot", default=str(ROOT / "gujrera_scraper/data/app/dev_ahmedabad.db"))
    ap.add_argument("--developers", type=int, default=10)
    ap.add_argument("--no-plots", action="store_true")
    args = ap.parse_args()

    os.environ["RERA_APP_DB"] = str(Path(args.snapshot).resolve())
    sys.path.insert(0, str(ROOT))
    from fastapi.testclient import TestClient

    from webapp.server.app import app

    client = TestClient(app)
    as_of = date.today().isoformat()
    snap, pipe = ro(Path(args.snapshot)), ro(Path(args.pipeline_db))
    chk = Checker()

    projects = [
        dict(r)
        for r in snap.execute(
            "SELECT project_id, developer_id, status, detail_crawled, sold_units, unsold_units FROM projects"
        )
    ]
    by_dev: dict[int, list[dict]] = defaultdict(list)
    for p in projects:
        by_dev[p["developer_id"]].append(p)
    progress_rows = [
        tuple(r)
        for r in pipe.execute("SELECT project_id, seq, period_date, physical_progress_pct FROM project_progress")
    ]
    progress_by_project: dict[int, list] = defaultdict(list)
    for r in progress_rows:
        progress_by_project[r[0]].append(r)
    ext_counts: dict[int, int] = defaultdict(int)
    for (pid,) in pipe.execute("SELECT project_id FROM project_extensions WHERE status = 'approved'"):
        ext_counts[pid] += 1

    # ---- market
    market = client.get("/api/dashboard").json()
    snap_ids = {p["project_id"] for p in projects}
    market_rows = [r for r in progress_rows if r[0] in snap_ids]
    check_progress(
        chk, "market", yearly_progress(market_rows, as_of, MARKET_MIN_N), market["yearly_progress"], as_of, market_rows
    )
    for key, value in status_counts(projects).items():
        chk.eq(f"market status {key}", value, market["status"][key])
    plots = {"Ahmedabad market": market["yearly_progress"]["points"]}

    # ---- developers: the largest by analysed projects, plus the largest with extensions
    devs = [
        dict(r)
        for r in snap.execute(
            "SELECT developer_id, name FROM developers WHERE projects_analyzed > 0 "
            "ORDER BY projects_analyzed DESC, developer_id LIMIT :n",
            {"n": args.developers},
        )
    ]
    extended = snap.execute(
        "SELECT developer_id, name FROM developers WHERE total_extensions > 0 "
        "ORDER BY total_extensions DESC, developer_id LIMIT 3"
    ).fetchall()
    devs += [dict(r) for r in extended if r["developer_id"] not in {d["developer_id"] for d in devs}]
    summary = []
    for dev in devs:
        did, scope = dev["developer_id"], dev["name"]
        report = client.get(f"/api/developers/{did}").json()
        members = by_dev[did]
        rows = [r for p in members for r in progress_by_project.get(p["project_id"], [])]
        check_progress(chk, scope, yearly_progress(rows, as_of), report["yearly_progress"], as_of, rows)
        for key, value in status_counts(members).items():
            chk.eq(f"{scope} status {key}", value, report["construction"][key])
        analysed = [p for p in members if p["detail_crawled"] == 1]
        extended_projects = [p for p in analysed if ext_counts.get(p["project_id"])]
        chk.eq(
            f"{scope} projects with extensions",
            len(extended_projects),
            report["extensions"]["projects_with_extensions"],
        )
        chk.eq(
            f"{scope} extension events",
            sum(ext_counts[p["project_id"]] for p in extended_projects),
            report["extensions"]["extension_events"],
        )
        api_sales = {s["project_id"]: s["sales_pct"] for s in report["sales"]["projects"]}
        expected_sales = {
            p["project_id"]: round(100 * p["sold_units"] / (p["sold_units"] + p["unsold_units"]), 1)
            for p in analysed
            if p["sold_units"] is not None and p["unsold_units"] is not None and p["sold_units"] + p["unsold_units"] > 0
        }
        chk.eq(f"{scope} sales projects", sorted(expected_sales), sorted(api_sales))
        for pid, pct in expected_sales.items():
            if pid in api_sales:
                chk.eq(f"{scope} project {pid} sales %", pct, api_sales[pid])
        plots[scope] = report["yearly_progress"]["points"]
        summary.append(
            {
                "developer_id": did,
                "name": scope,
                "projects": len(members),
                "analysed": len(analysed),
                "progress_years": len(report["yearly_progress"]["points"]),
                "extension_events": report["extensions"]["extension_events"],
            }
        )

    # ---- revisions (QA observation, not a check)
    revisions = []
    for rows in progress_by_project.values():
        vals = [pct for _, _, _, pct in sorted(rows, key=lambda r: (r[2] or "", r[1])) if pct is not None]
        revisions += [a - b for a, b in pairwise(vals) if a - b > 30]

    written = [] if args.no_plots else plot(plots, revisions)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary.json").write_text(
        json.dumps(
            {
                "as_of": as_of,
                "snapshot": args.snapshot,
                "pipeline_db": args.pipeline_db,
                "checks": chk.checked,
                "mismatches": chk.mismatches,
                "developers": summary,
                "progress_revisions_over_30pp": len(revisions),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"Checked {chk.checked} values for the market and {len(devs)} developers (as of {as_of}).")
    print(f"Published progress revisions (> 30 pp drop): {len(revisions)}")
    for path in written:
        print(f"  wrote {path.relative_to(ROOT)}")
    if chk.mismatches:
        print(f"{len(chk.mismatches)} MISMATCH(ES):")
        for m in chk.mismatches[:50]:
            print(f"  - {m}")
        return 1
    print("All metrics match.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
