"""Read-only access to the GujRERA app snapshot published by the pipeline.

The pipeline (`gujrera_scraper/cli.py refresh-app`) rewrites the snapshot file
atomically every ~30 minutes while the crawl runs. Each query opens a fresh
read-only connection, so a new snapshot is picked up on the next request and the
file is never locked against replacement.
"""

import os
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SNAPSHOT = PROJECT_ROOT / "gujrera_scraper" / "data" / "app" / "gujrera_ahmedabad.db"


class SnapshotUnavailableError(RuntimeError):
    """The snapshot has not been published yet (or the path is wrong)."""


def snapshot_path() -> Path:
    path = Path(os.environ.get("RERA_APP_DB", str(DEFAULT_SNAPSHOT)))
    if not path.is_file():
        raise SnapshotUnavailableError(f"GujRERA snapshot not found at {path}")
    return path


def snapshot_version() -> int:
    return int(snapshot_path().stat().st_mtime)


def query(sql: str, params: dict | None = None) -> list[dict]:
    uri = f"{snapshot_path().resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params or {}).fetchall()]
    finally:
        conn.close()
