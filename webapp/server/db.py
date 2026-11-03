"""Read-only access to the GujRERA app snapshot published by the pipeline.

The pipeline (`gujrera_scraper/cli.py refresh-app`) rewrites the snapshot file
atomically every ~30 minutes while the crawl runs. Each query opens a fresh
read-only connection, so a new snapshot is picked up on the next request and the
file is never locked against replacement.

One file holds everything the app shows (`RERA_APP_DB`): projects, developers, blocks, amenities, documents,
map outlines and the unit-wise booking rows (`unit_inventory`).
Older snapshots (without the `city` column, `meta_counts`, `unit_inventory`, ...) keep working: callers use `columns()`
and `has_table()` to adapt; the API then reports `available: false` for units instead of failing.
"""

import os
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = PROJECT_ROOT / "gujrera_scraper" / "data" / "app"
# newest layout first: the merged multi-city snapshot (git-ignored, shared separately) wins over the old
# Ahmedabad-only snapshot that is tracked in git (formerly gujrera_ahmedabad.db)
DEFAULT_CANDIDATES = (APP_DIR / "gujrera_ahmedabad_gandhinagar.db", APP_DIR / "Gujrara_old_data_15_10_26.db")
DEFAULT_SNAPSHOT = DEFAULT_CANDIDATES[-1]

CATEGORIES = ("residential", "mixed", "commercial", "plotted")


class SnapshotUnavailableError(RuntimeError):
    """The snapshot has not been published yet (or the path is wrong)."""


def snapshot_path() -> Path:
    env = os.environ.get("RERA_APP_DB")
    path = Path(env) if env else next((c for c in DEFAULT_CANDIDATES if c.is_file()), DEFAULT_SNAPSHOT)
    if not path.is_file():
        raise SnapshotUnavailableError(f"GujRERA snapshot not found at {path}")
    return path


def snapshot_version() -> int:
    return int(snapshot_path().stat().st_mtime)


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def query(sql: str, params: dict | None = None) -> list[dict]:
    conn = _connect(snapshot_path())
    try:
        return [dict(r) for r in conn.execute(sql, params or {}).fetchall()]
    finally:
        conn.close()


_schema_cache: dict[tuple, frozenset] = {}


def columns(table: str) -> frozenset:
    """Column names of a snapshot table (empty when the table does not exist), cached per snapshot version."""
    key = (str(snapshot_path()), int(snapshot_path().stat().st_mtime), table)
    if key not in _schema_cache:
        if len(_schema_cache) > 200:
            _schema_cache.clear()
        _schema_cache[key] = frozenset(r["name"] for r in query(f"PRAGMA table_info({table})"))
    return _schema_cache[key]


def has_table(table: str) -> bool:
    return bool(columns(table))


def city_expr(alias: str = "") -> str:
    """SQL for a project's city: the published `city` column, or the (case-normalised) district of older snapshots."""
    a = f"{alias}." if alias else ""
    if "city" in columns("projects"):
        return f"{a}city"
    return f"(UPPER(SUBSTR(TRIM({a}district), 1, 1)) || LOWER(SUBSTR(TRIM({a}district), 2)))"


def scope_clause(city: str | None, category: str | None, alias: str = "") -> tuple[str, dict]:
    """WHERE fragment (and bound values) restricting projects to a city and/or categories.
    `category` may be a comma-separated list; unknown categories are ignored."""
    a = f"{alias}." if alias else ""
    where, bound = [], {}
    if city and city.strip():
        where.append(f"LOWER({city_expr(alias)}) = LOWER(:scope_city)")
        bound["scope_city"] = city.strip()
    cats = [c for c in (category or "").split(",") if c in CATEGORIES]
    if cats:
        where.append(f"{a}category IN ({','.join(f':scope_cat{i}' for i in range(len(cats)))})")
        bound.update({f"scope_cat{i}": c for i, c in enumerate(cats)})
    return (" AND ".join(where) if where else "1 = 1"), bound


def units_available() -> bool:
    """True when this snapshot carries the unit-wise booking table (older snapshots do not)."""
    return has_table("unit_inventory")
