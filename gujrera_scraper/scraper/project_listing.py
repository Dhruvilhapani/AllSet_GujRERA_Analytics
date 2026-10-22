"""Project universe: the official registered-project list for the target project types.

The list endpoint returns the complete result set in one response (no pagination), so completeness is
verified by comparing against the dashboard count grid and by checking projectRegId / regNo uniqueness.
"""
from __future__ import annotations

from collections import Counter

from scraper.endpoints import TYPE_LABEL, UNIVERSE
from scraper.network_client import GujReraClient


def in_scope(row: dict, key: str, settings: dict) -> bool:
    """True when a list row belongs to the crawl scope. Types listed in `district_scoped_types` are limited to
    `target_districts` (case-insensitive: the source has spelling variants such as 'AHmedabad'); the legacy
    all-Gujarat types are kept whole."""
    scoped = settings.get("district_scoped_types") or []
    if key not in scoped:
        return True
    wanted = {d.strip().lower() for d in settings.get("target_districts") or []}
    return not wanted or (row.get("districtName") or "").strip().lower() in wanted


def in_unit_scope(row: dict, settings: dict) -> bool:
    """True when the unit layer (block names, Form 3, per-block unit lists) is crawled for this list row."""
    wanted = {d.strip().lower() for d in (settings.get("units") or {}).get("districts") or []}
    return (row.get("districtName") or "").strip().lower() in wanted


def fetch_list(client: GujReraClient, key: str) -> list[dict]:
    return client.get_json(UNIVERSE[key].path).get("data") or []


def fetch_counts(client: GujReraClient) -> dict:
    return client.get_json(UNIVERSE["dashboard_counts"].path)


def validate_universe(rows_by_key: dict[str, list[dict]], counts: dict) -> dict:
    """Coverage + duplicate checks for the discovered universe."""
    grid = {r.get("type"): r for r in (counts.get("data") or [])}
    report = {}
    all_ids: Counter = Counter()
    all_reg: Counter = Counter()
    for key, rows in rows_by_key.items():
        expected = int(grid.get(TYPE_LABEL.get(key), {}).get("total") or 0)
        ids = Counter(int(r["projectRegId"]) for r in rows)
        all_ids.update(ids)
        all_reg.update(r.get("regNo") for r in rows if r.get("regNo"))
        report[key] = {"dashboard_total": expected, "list_rows": len(rows), "unique_project_ids": len(ids),
                       "duplicate_rows": sum(c - 1 for c in ids.values() if c > 1),
                       "coverage_pct": round(len(ids) / expected * 100, 2) if expected else None}
    report["total_unique_projects"] = len(all_ids)
    report["cross_type_duplicates"] = sum(1 for c in all_ids.values() if c > 1)
    report["duplicate_reg_numbers"] = sum(1 for c in all_reg.values() if c > 1)
    return report
