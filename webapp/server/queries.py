"""List / search / project-detail queries over the snapshot.

Developer- and market-level metrics live in analytics.py (pandas/NumPy); this module only selects, filters
and pages rows as the pipeline published them. NULL means "not available / not crawled yet" and is returned
as None — never 0. User input only reaches SQL as bound parameters; sort columns come from a whitelist.
"""

import json
import re

from . import db

PAGE_SIZE_DEFAULT = 25
PAGE_SIZE_MAX = 200
PROJECTS_PAGE_SIZE_MAX = 10_000  # the Projects page can show every project at once (about 7,200 rows, ~2 MB)
UNITS_PAGE_SIZE_DEFAULT = 100
UNITS_PAGE_SIZE_MAX = 500
COMPARE_MAX = 4
CATEGORIES = db.CATEGORIES
UNIT_STATUSES = ("booked", "unbooked", "other", "unknown")

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

PROJECT_SORT_COLUMNS = {
    "name": "p.name COLLATE NOCASE",
    "registration": "p.registration_date",
    "completion": "p.current_completion_date",
    "progress": "p.progress_pct",
    "units": "p.total_units",
    "sales_pct": "p.sales_pct",
    "locality": "p.locality COLLATE NOCASE",
}

UNIT_SORT_COLUMNS = {
    "unit": "u.unit_sort",
    "carpet": "u.carpet_area_sqm",
    "area": "u.area_excl_balcony_terrace_sqm",
    "status": "u.status_norm",
    "agreement": "u.agreement_date",
    "usage": "u.usage_class",
}

_JSON_FIELDS = ("promoter_account_ids", "aliases", "similar_names", "localities", "talukas")

_LIST_COLUMNS = """
    d.developer_id, d.name, d.entity_type, d.projects_total, d.projects_analyzed,
    d.residential_projects, d.mixed_projects, {extra} d.completed, d.ongoing, d.delayed, d.pending,
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
    "commercial_total",
    "plotted_total",
)


def _decode(row: dict) -> dict:
    for key in _JSON_FIELDS:
        if isinstance(row.get(key), str):
            row[key] = json.loads(row[key])
    row.pop("name_search", None)
    return row


def _list_columns() -> str:
    have = db.columns("developers")
    extra = "".join(f"d.{c}, " for c in ("commercial_projects", "plotted_projects") if c in have)
    return _LIST_COLUMNS.format(extra=extra)


def meta() -> dict:
    out = {r["key"]: r["value"] for r in db.query("SELECT key, value FROM meta")}
    for key in _INT_META:
        if out.get(key) is not None:
            out[key] = int(out[key])
    out["dataset_complete"] = out.get("dataset_complete") == "1"
    city = db.city_expr("p")
    rows = db.query(
        f"SELECT {city} AS city, p.locality, COUNT(*) n FROM projects p WHERE p.locality IS NOT NULL "  # noqa: S608
        f"GROUP BY {city}, p.locality HAVING n >= 2 ORDER BY p.locality COLLATE NOCASE"
    )
    out["localities"] = sorted({r["locality"] for r in rows}, key=str.lower)
    by_city: dict[str, list[str]] = {}
    for r in rows:
        by_city.setdefault(r["city"] or "", []).append(r["locality"])
    out["localities_by_city"] = by_city
    if db.has_table("meta_counts"):
        counts = db.query("SELECT city, category, total, analyzed FROM meta_counts ORDER BY city, category")
    else:  # snapshot published before the multi-city layout
        counts = db.query(
            f"SELECT {city} AS city, p.category, COUNT(*) total, SUM(p.detail_crawled = 1) analyzed "  # noqa: S608
            f"FROM projects p GROUP BY {city}, p.category"
        )
    out["meta_counts"] = counts
    out["cities"] = sorted({c["city"] for c in counts if c["city"]})
    present = {c["category"] for c in counts}
    out["categories"] = [c for c in CATEGORIES if c in present]
    out["units_available"] = db.units_available()
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
    clause, like = _all_tokens_like("d.name_search", params.get("q"), "q")
    if like:
        where.append(clause)
        bound.update(like)
    scope, scope_bound = db.scope_clause(params.get("city"), params.get("category"), "p")
    if scope != "1 = 1":  # developers having at least one project in that city / category
        where.append(f"EXISTS (SELECT 1 FROM projects p WHERE p.developer_id = d.developer_id AND {scope})")  # noqa: S608
        bound.update(scope_bound)
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


def page_bounds(params: dict, default: int = PAGE_SIZE_DEFAULT, maximum: int = PAGE_SIZE_MAX) -> tuple[int, int]:
    try:
        page = max(1, int(params.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        size = int(params.get("page_size") or default)
    except (TypeError, ValueError):
        size = default
    return page, min(max(size, 1), maximum)


def developers(params: dict) -> dict:
    where, bound = _filters(params)
    order = SORT_COLUMNS.get(params.get("sort_by"), "d.projects_total")
    direction = "ASC" if (params.get("sort_dir") or "").lower() == "asc" else "DESC"
    page, size = page_bounds(params)
    bound.update({"limit": size, "offset": (page - 1) * size})
    # where/order are built only from the whitelisted fragments above; values are bound.
    select = f"SELECT {_list_columns()} FROM developers d WHERE {where}"  # noqa: S608
    ordering = f" ORDER BY {order} IS NULL, {order} {direction}, d.developer_id"
    rows = db.query(f"{select}{ordering} LIMIT :limit OFFSET :offset", bound)
    total = db.query(f"SELECT COUNT(*) n FROM developers d WHERE {where}", bound)[0]["n"]  # noqa: S608
    return {"developers": [_decode(r) for r in rows], "total": total, "page": page, "page_size": size}


# ---------------------------------------------------------------------------------------------- search
SEARCH_MIN_CHARS = 2
SEARCH_LIMIT_MAX = 10
_MAX_TOKENS = 6


def _tokens(text: str | None) -> list[str]:
    return [t for t in re.split(r"\s+", (text or "").strip().lower()) if t][:_MAX_TOKENS]


def _all_tokens_like(column: str, text: str | None, prefix: str) -> tuple[str, dict]:
    """`column LIKE %t1% AND column LIKE %t2% ...`: every word must appear, in any order ('35 shlok' = 'SHLOK 35')."""
    toks = _tokens(text)
    return (
        " AND ".join(f"{column} LIKE :{prefix}{i}" for i in range(len(toks))) or "1 = 1",
        {f"{prefix}{i}": f"%{t}%" for i, t in enumerate(toks)},
    )


def search(params: dict) -> dict:
    """Global search over projects (name / RERA no.), developers (name and aliases) and localities.

    Every word of the query must match (any order, case-insensitive). Names that start with the query come first. Each
    group returns at most `limit` rows plus the number of matches, so the UI can say 'show all 143 projects'."""
    q = " ".join(_tokens(params.get("q")))
    try:
        limit = min(max(int(params.get("limit") or 6), 1), SEARCH_LIMIT_MAX)
    except (TypeError, ValueError):
        limit = 6
    out = {"q": q, "projects": [], "projects_total": 0, "developers": [], "developers_total": 0, "localities": []}
    if len(q) < SEARCH_MIN_CHARS:
        return out
    prefix = f"{q}%"

    where, bound = _all_tokens_like("p.name_search", q, "t")
    city = db.city_expr("p")
    bound.update({"limit": limit, "prefix": prefix})
    out["projects"] = db.query(
        f"SELECT p.project_id, p.name, p.rera_number, p.category, {city} AS city, p.locality, p.status, "  # noqa: S608
        "d.name AS developer_name FROM projects p LEFT JOIN developers d ON d.developer_id = p.developer_id "
        f"WHERE {where} ORDER BY (LOWER(p.name) LIKE :prefix) DESC, p.registration_date DESC, p.project_id "
        "LIMIT :limit",
        bound,
    )
    out["projects_total"] = db.query(f"SELECT COUNT(*) n FROM projects p WHERE {where}", bound)[0]["n"]  # noqa: S608

    where, bound = _all_tokens_like("d.name_search", q, "t")
    bound.update({"limit": limit, "prefix": prefix})
    out["developers"] = db.query(
        "SELECT d.developer_id, d.name, d.projects_total FROM developers d "  # noqa: S608
        f"WHERE {where} ORDER BY (LOWER(d.name) LIKE :prefix) DESC, d.projects_total DESC, d.developer_id LIMIT :limit",
        bound,
    )
    out["developers_total"] = db.query(f"SELECT COUNT(*) n FROM developers d WHERE {where}", bound)[0]["n"]  # noqa: S608

    out["localities"] = db.query(
        f"SELECT p.locality AS name, {city} AS city, COUNT(*) AS projects FROM projects p "  # noqa: S608
        "WHERE p.locality IS NOT NULL AND LOWER(p.locality) LIKE :like "
        "GROUP BY p.locality, 2 ORDER BY (LOWER(p.locality) LIKE :prefix) DESC, projects DESC LIMIT :limit",
        {"like": f"%{q}%", "prefix": prefix, "limit": limit},
    )
    return out


# ---------------------------------------------------------------------------------------------- projects list
def _project_filters(params: dict) -> tuple[str, dict]:
    where, bound = db.scope_clause(params.get("city"), params.get("category"), "p")
    parts = [where]
    clause, like = _all_tokens_like("p.name_search", params.get("q"), "q")
    if like:
        parts.append(clause)
        bound.update(like)
    if params.get("status"):
        parts.append("p.status = :status")
        bound["status"] = params["status"]
    if params.get("locality"):
        parts.append("p.locality = :locality")
        bound["locality"] = params["locality"]
    dev = (params.get("developer") or "").strip().lower()
    if dev.isdigit():
        parts.append("p.developer_id = :developer_id")
        bound["developer_id"] = int(dev)
    elif dev:
        parts.append(
            "EXISTS (SELECT 1 FROM developers d WHERE d.developer_id = p.developer_id "
            "AND d.name_search LIKE :developer)"
        )
        bound["developer"] = f"%{dev}%"
    return " AND ".join(parts), bound


def list_projects(params: dict) -> dict:
    """Paged project list. Filters: q (name / RERA no.), city, category (comma list), status, developer (id or
    name), locality. Unit-data availability is reported per row so the list never implies 'no units'."""
    where, bound = _project_filters(params)
    order = PROJECT_SORT_COLUMNS.get(params.get("sort_by"), "p.registration_date")
    direction = "ASC" if (params.get("sort_dir") or "").lower() == "asc" else "DESC"
    page, size = page_bounds(params, maximum=PROJECTS_PAGE_SIZE_MAX)
    bound.update({"limit": size, "offset": (page - 1) * size})
    have = db.columns("projects")
    unit_state = "p.unit_data_state" if "unit_data_state" in have else "NULL"
    cols = (
        f"p.project_id, p.name, p.rera_number, p.category, p.project_type, {db.city_expr('p')} AS city, p.taluka, "
        "p.locality, p.status, p.developer_id, d.name AS developer_name, p.registration_date, "
        "p.current_completion_date, p.progress_pct, p.total_units, p.sold_units, p.unsold_units, p.sales_pct, "
        f"p.detail_crawled, {unit_state} AS unit_data_state"
    )
    frm = "FROM projects p LEFT JOIN developers d ON d.developer_id = p.developer_id"
    rows = db.query(
        f"SELECT {cols} {frm} WHERE {where} ORDER BY {order} IS NULL, {order} {direction}, p.project_id "
        "LIMIT :limit OFFSET :offset",
        bound,
    )
    total = db.query(f"SELECT COUNT(*) n FROM projects p WHERE {where}", bound)[0]["n"]  # noqa: S608
    statuses = db.query(f"SELECT p.status, COUNT(*) n FROM projects p WHERE {where} GROUP BY p.status", bound)  # noqa: S608
    return {
        "projects": rows,
        "total": total,
        "page": page,
        "page_size": size,
        "status_counts": {r["status"]: r["n"] for r in statuses if r["status"]},
    }


# ---------------------------------------------------------------------------------------------- project detail
_PROJECT_CHILDREN = (
    (
        "progress",
        "SELECT seq, quarter_label, period_date, progress_pct FROM project_progress "
        "WHERE project_id = :id ORDER BY period_date, seq",
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
)

# optional (newer snapshots): table -> (key, SQL)
_OPTIONAL_CHILDREN = (
    (
        "project_amenities",
        "amenities",
        "SELECT item_key, label, category, source, applicable, progress_pct, planned_start, planned_end, "
        "actual_start, actual_end, remarks, form1_submitted_on FROM project_amenities WHERE project_id = :id "
        "ORDER BY category, label",
    ),
    (
        "project_infrastructure",
        "infrastructure",
        "SELECT category, name, value FROM project_infrastructure WHERE project_id = :id ORDER BY category, name",
    ),
    (
        "project_documents",
        "documents",
        "SELECT document_type, document_name, document_date, url FROM project_documents WHERE project_id = :id "
        "ORDER BY document_date DESC",
    ),
    (
        "project_professionals",
        "professionals",
        "SELECT role, name FROM project_professionals WHERE project_id = :id ORDER BY role, name",
    ),
    (
        "project_form3",
        "form3",
        "SELECT form_three_id, submitted_on, number_of_units, booked_declared, unbooked_declared, total_residential, "
        "total_shop, total_office, total_others FROM project_form3 WHERE project_id = :id AND is_latest = 1",
    ),
)

_BLOCK_BASE = "block_name, planned_units, sold_units, unsold_units, progress_pct"
_BLOCK_NEW = (
    "unit_rows",
    "unit_booked",
    "unit_unbooked",
    "unit_other",
    "fetch_outcome",
    "height",
    "fsi",
    "builtup_area_sqm",
    "dev_start_date",
    "dev_end_date",
    "floors",
    "lifts",
)


def _blocks(project_id: int) -> list[dict]:
    have = db.columns("project_blocks")
    cols = _BLOCK_BASE + "".join(f", {c}" for c in _BLOCK_NEW if c in have)
    return db.query(f"SELECT {cols} FROM project_blocks WHERE project_id = :id ORDER BY block_name", {"id": project_id})  # noqa: S608


_DEV_PROFILE = (
    "entity_type",
    "registration_status",
    "company_reg_number",
    "date_of_incorporation",
    "registered_address",
    "website",
    "declared_group_name",
    "declared_total_projects",
    "declared_completed_projects",
    "declared_ongoing_projects",
    "declared_experience_years",
    "associates",
)


def _developer_profile(developer_id: int | None) -> dict | None:
    """Company details of the promoter (business information only; contact data is never published)."""
    if developer_id is None:
        return None
    have = db.columns("developers")
    cols = ["developer_id", "name"] + [c for c in _DEV_PROFILE if c in have]
    rows = db.query(f"SELECT {', '.join(cols)} FROM developers WHERE developer_id = :id", {"id": developer_id})  # noqa: S608
    if not rows:
        return None
    out = rows[0]
    if isinstance(out.get("associates"), str):
        out["associates"] = json.loads(out["associates"])
    return out


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
    if "city" not in proj:
        proj["city"] = (proj.get("district") or "").strip().title() or None
    for key, sql in _PROJECT_CHILDREN:
        proj[key] = db.query(sql, {"id": project_id})
    proj["blocks"] = _blocks(project_id)
    for table, key, sql in _OPTIONAL_CHILDREN:
        proj[key] = db.query(sql, {"id": project_id}) if db.has_table(table) else []
    proj["form3"] = (proj["form3"] or [None])[0]
    proj["developer"] = _developer_profile(proj.get("developer_id"))
    proj["polygon"] = (
        [
            [r["lat"], r["lng"]]
            for r in db.query(
                "SELECT lat, lng FROM project_polygon WHERE project_id = :id ORDER BY seq", {"id": project_id}
            )
        ]
        if db.has_table("project_polygon")
        else []
    )
    proj["units_available"] = db.units_available() and "unit_data_state" in proj
    return proj


def blocks(project_id: int) -> dict | None:
    """Block cards: portal block-chart counts and counts from the unit rows side by side (they can differ)."""
    proj = db.query(
        "SELECT p.project_id, p.category, p.unit_data_state, p.unit_data_note, p.units_as_of, p.units_row_count "
        "FROM projects p WHERE p.project_id = :id"
        if "unit_data_state" in db.columns("projects")
        else "SELECT p.project_id, p.category, NULL AS unit_data_state, NULL AS unit_data_note, NULL AS units_as_of, "
        "NULL AS units_row_count FROM projects p WHERE p.project_id = :id",
        {"id": project_id},
    )
    if not proj:
        return None
    return {**proj[0], "blocks": _blocks(project_id), "units_available": db.units_available()}


def units(project_id: int, params: dict) -> dict | None:
    """Unit rows of one project (latest Form 3), filtered and paged on the server.

    Filters: block, q (unit number contains), status (booked | unbooked | other | unknown), usage (usage class).
    `summary` counts the rows matching block / q / usage across ALL statuses, so the status chips stay meaningful.
    A project whose unit data is not `complete` is reported as such; missing rows are never counted as unbooked."""
    info = blocks(project_id)
    if info is None:
        return None
    page, size = page_bounds(params, UNITS_PAGE_SIZE_DEFAULT, UNITS_PAGE_SIZE_MAX)
    out = {
        **{
            k: info[k]
            for k in ("project_id", "category", "unit_data_state", "unit_data_note", "units_as_of", "units_row_count")
        },
        "available": info["units_available"],
        "units": [],
        "total": 0,
        "page": page,
        "page_size": size,
        "summary": {s: 0 for s in UNIT_STATUSES} | {"total": 0},
        "usage_classes": [],
    }
    if not out["available"]:
        return out
    where, bound = ["u.project_id = :id"], {"id": project_id}
    if params.get("block"):
        where.append("u.block_name = :block")
        bound["block"] = params["block"]
    q = re.sub(r"\s+", " ", (params.get("q") or "").strip().lower())
    if q:
        where.append("u.unit_no_norm LIKE :q")
        bound["q"] = f"%{q}%"
    if params.get("usage"):
        where.append("u.usage_class = :usage")
        bound["usage"] = params["usage"]
    base = " AND ".join(where)
    for r in db.query(
        f"SELECT u.status_norm s, COUNT(*) n FROM unit_inventory u WHERE {base} GROUP BY u.status_norm",  # noqa: S608
        bound,
    ):
        out["summary"][r["s"] if r["s"] in UNIT_STATUSES else "unknown"] += r["n"]
        out["summary"]["total"] += r["n"]
    out["usage_classes"] = [
        r["usage_class"]
        for r in db.query(
            "SELECT DISTINCT usage_class FROM unit_inventory WHERE project_id = :id ORDER BY usage_class",
            {"id": project_id},
        )
        if r["usage_class"]
    ]
    if params.get("status") in UNIT_STATUSES:
        where.append("u.status_norm = :status")
        bound["status"] = params["status"]
    where_sql = " AND ".join(where)
    order = UNIT_SORT_COLUMNS.get(params.get("sort_by"), "u.unit_sort")
    direction = "DESC" if (params.get("sort_dir") or "").lower() == "desc" else "ASC"
    bound.update({"limit": size, "offset": (page - 1) * size})
    out["total"] = db.query(f"SELECT COUNT(*) n FROM unit_inventory u WHERE {where_sql}", bound)[0]["n"]  # noqa: S608
    out["units"] = db.query(
        "SELECT u.block_name, u.unit_no, u.unit_kind, u.usage, u.usage_class, u.carpet_area_sqm, "  # noqa: S608
        "u.area_excl_balcony_terrace_sqm, u.status, u.status_norm, u.agreement_date, u.encumbrance_status, "
        "u.redeveloped, u.last_changed_at "
        f"FROM unit_inventory u WHERE {where_sql} ORDER BY {order} IS NULL, {order} {direction}, "
        "u.block_name, u.unit_sort, u.dup_seq LIMIT :limit OFFSET :offset",
        bound,
    )
    return out
