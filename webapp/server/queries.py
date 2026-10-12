"""List / search / project-detail queries over the snapshot.

Developer- and market-level metrics live in analytics.py (pandas/NumPy); this module only selects, filters
and pages rows as the pipeline published them. NULL means "not available / not crawled yet" and is returned
as None — never 0. User input only reaches SQL as bound parameters; sort columns come from a whitelist.
"""

import json

from . import db

PAGE_SIZE_DEFAULT = 25
PAGE_SIZE_MAX = 200
COMPARE_MAX = 4

# Public sort key -> column. "growth" = projects registered in the last 24 months.
SORT_COLUMNS = {
    "name": "d.name COLLATE NOCASE",
    "projects": "d.projects_total",
    "completed": "d.completed",
    "ongoing": "d.ongoing",
    "delayed": "d.delayed",
    "pending": "d.pending",
    "sales_pct": "d.sales_pct",
    "progress": "d.avg_progress_pct",
    "complaints": "d.complaints_total",
    "extensions": "d.total_extensions",
    "growth": "recent_projects",
}

_JSON_FIELDS = ("promoter_account_ids", "aliases", "similar_names", "localities", "talukas")

_LIST_COLUMNS = """
    d.developer_id, d.name, d.entity_type, d.projects_total, d.projects_analyzed,
    d.residential_projects, d.mixed_projects, d.completed, d.ongoing, d.delayed, d.pending,
    d.stalled, d.extended_projects, d.total_extensions, d.sales_pct, d.avg_progress_pct,
    d.projects_with_progress_data, d.completion_rate_pct, d.delay_rate_pct, d.complaints_total,
    d.complaints_pending, d.suo_motu, d.challans, d.regulatory_actions, d.localities,
    (SELECT COUNT(*) FROM projects p WHERE p.developer_id = d.developer_id
       AND p.registration_date >= date('now', '-24 months')) AS recent_projects
"""

_INT_META = (
    "projects_total",
    "projects_analyzed",
    "projects_pending",
    "developers_identified",
    "residential_total",
    "mixed_total",
)


def _decode(row: dict) -> dict:
    for key in _JSON_FIELDS:
        if isinstance(row.get(key), str):
            row[key] = json.loads(row[key])
    row.pop("name_search", None)
    return row


def meta() -> dict:
    out = {r["key"]: r["value"] for r in db.query("SELECT key, value FROM meta")}
    for key in _INT_META:
        if out.get(key) is not None:
            out[key] = int(out[key])
    out["dataset_complete"] = out.get("dataset_complete") == "1"
    out["localities"] = [
        r["locality"]
        for r in db.query(
            "SELECT locality, COUNT(*) n FROM projects WHERE locality IS NOT NULL "
            "GROUP BY locality HAVING n >= 2 ORDER BY locality COLLATE NOCASE"
        )
    ]
    return out


def _number(raw):
    try:
        return float(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _ids(raw) -> list[int]:
    if not raw:
        return []
    return [int(p) for p in str(raw).split(",")[:COMPARE_MAX] if p.strip().isdigit()]


def _filters(params: dict) -> tuple[str, dict]:
    where, bound = ["1 = 1"], {}
    q = (params.get("q") or "").strip().lower()
    if q:
        where.append("d.name_search LIKE :q")
        bound["q"] = f"%{q}%"
    if params.get("category") in {"residential", "mixed"}:
        where.append(
            "EXISTS (SELECT 1 FROM projects p WHERE p.developer_id = d.developer_id AND p.category = :category)"
        )
        bound["category"] = params["category"]
    if params.get("locality"):
        where.append(
            "EXISTS (SELECT 1 FROM projects p WHERE p.developer_id = d.developer_id AND p.locality = :locality)"
        )
        bound["locality"] = params["locality"]
    for name, column in (
        ("min_projects", "d.projects_total"),
        ("min_completed", "d.completed"),
        ("min_ongoing", "d.ongoing"),
        ("min_sales_pct", "d.sales_pct"),
    ):
        value = _number(params.get(name))
        if value is not None:
            where.append(f"{column} >= :{name}")
            bound[name] = value
    if params.get("has_delayed") == "1":
        where.append("d.delayed > 0")
    if params.get("has_complaints") == "1":
        where.append("d.complaints_total > 0")
    ids = _ids(params.get("ids"))
    if ids:
        where.append(f"d.developer_id IN ({','.join(f':id{i}' for i in range(len(ids)))})")
        bound.update({f"id{i}": v for i, v in enumerate(ids)})
    return " AND ".join(where), bound


def page_bounds(params: dict) -> tuple[int, int]:
    try:
        page = max(1, int(params.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        size = int(params.get("page_size") or PAGE_SIZE_DEFAULT)
    except (TypeError, ValueError):
        size = PAGE_SIZE_DEFAULT
    return page, min(max(size, 1), PAGE_SIZE_MAX)


def developers(params: dict) -> dict:
    where, bound = _filters(params)
    order = SORT_COLUMNS.get(params.get("sort_by"), "d.projects_total")
    direction = "ASC" if (params.get("sort_dir") or "").lower() == "asc" else "DESC"
    page, size = page_bounds(params)
    bound.update({"limit": size, "offset": (page - 1) * size})
    # where/order are built only from the whitelisted fragments above; values are bound.
    select = f"SELECT {_LIST_COLUMNS} FROM developers d WHERE {where}"  # noqa: S608
    ordering = f" ORDER BY {order} IS NULL, {order} {direction}, d.developer_id"
    rows = db.query(f"{select}{ordering} LIMIT :limit OFFSET :offset", bound)
    total = db.query(f"SELECT COUNT(*) n FROM developers d WHERE {where}", bound)[0]["n"]  # noqa: S608
    return {"developers": [_decode(r) for r in rows], "total": total, "page": page, "page_size": size}


def project(project_id: int) -> dict | None:
    rows = db.query(
        "SELECT p.*, d.name AS developer_name FROM projects p "
        "LEFT JOIN developers d ON d.developer_id = p.developer_id WHERE p.project_id = :id",
        {"id": project_id},
    )
    if not rows:
        return None
    proj = rows[0]
    proj.pop("name_search", None)
    for key, sql in (
        (
            "progress",
            "SELECT seq, quarter_label, period_date, progress_pct FROM project_progress "
            "WHERE project_id = :id ORDER BY period_date, seq",
        ),
        (
            "blocks",
            "SELECT block_name, planned_units, sold_units, unsold_units, progress_pct "
            "FROM project_blocks WHERE project_id = :id ORDER BY block_name",
        ),
        (
            "legal",
            "SELECT record_type, reference, category, subtype, filed_date, order_date, status, outcome, "
            "penalty_amount, order_url FROM project_legal WHERE project_id = :id "
            "ORDER BY COALESCE(order_date, filed_date) DESC",
        ),
        (
            "events",
            "SELECT event_date, event_type, title, detail, reference FROM project_events "
            "WHERE project_id = :id ORDER BY event_date",
        ),
        (
            "extensions",
            "SELECT seq, extension_reg_no, approved_on, status, previous_completion_date, extended_completion_date, "
            "extension_days FROM project_extensions WHERE project_id = :id ORDER BY seq",
        ),
    ):
        proj[key] = db.query(sql, {"id": project_id})
    return proj
