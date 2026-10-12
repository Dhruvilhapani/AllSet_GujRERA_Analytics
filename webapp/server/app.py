"""Ahmedabad Developer Intelligence — API + web app server.

    uvicorn webapp.server.app:app --port 8600       (from the project root)

Routes
    GET /api/meta                       dataset status (crawl progress, last update) + filter options
    GET /api/dashboard                  market-level analytics for the Dashboard page (analytics.market_report)
    GET /api/developers                 paged/filtered/sorted developer list (?ids=1,2 for comparison)
    GET /api/developers/{developer_id}  developer intelligence report (analytics.developer_report)
    GET /api/projects/{project_id}      one project: details, progress history, blocks, legal, events
    /*                                  the built React app (webapp/frontend/dist), SPA fallback

All data comes from the GujRERA snapshot published by the pipeline (see db.py).
When it is missing the API answers 503 with a plain message (never empty data).
"""

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import analytics, db, queries
from .cache import cached

log = logging.getLogger(__name__)
DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
UNAVAILABLE = "Developer data is not available yet. The GujRERA dataset has not been published."

app = FastAPI(title="Ahmedabad Developer Intelligence", docs_url="/api/docs", openapi_url="/api/openapi.json")


@app.exception_handler(db.SnapshotUnavailableError)
async def _unavailable(_request: Request, exc: db.SnapshotUnavailableError):
    log.warning("snapshot unavailable: %s", exc)
    return JSONResponse({"error": UNAVAILABLE}, status_code=503)


@app.get("/api/meta")
def get_meta():
    return cached("meta", queries.meta)


@app.get("/api/dashboard")
def get_dashboard():
    return cached("dashboard", analytics.market_report)


@app.get("/api/developers")
def list_developers(request: Request):
    params = dict(sorted(request.query_params.items()))
    return cached("developers", queries.developers, params)


@app.get("/api/developers/{developer_id}")
def get_developer(developer_id: int):
    dev = cached("developer", analytics.developer_report, developer_id)
    if dev is None:
        return JSONResponse({"error": "Developer not found."}, status_code=404)
    return dev


@app.get("/api/projects/{project_id}")
def get_project(project_id: int):
    proj = cached("project", queries.project, project_id)
    if proj is None:
        return JSONResponse({"error": "Project not found."}, status_code=404)
    return proj


@app.get("/api/{rest:path}")
def unknown_api(rest: str):
    return JSONResponse({"error": f"Unknown endpoint /api/{rest}"}, status_code=404)


if (DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    """Serve the built frontend; client-side routes (/developers/12) fall back to index.html."""
    index = DIST / "index.html"
    if not index.is_file():
        return JSONResponse({"error": "Frontend not built. Run `npm run build` in webapp/frontend."}, status_code=503)
    target = (DIST / path).resolve()
    if path and target.is_file() and DIST.resolve() in target.parents:
        return FileResponse(target)
    return FileResponse(index)
