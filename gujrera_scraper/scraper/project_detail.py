"""Project detail endpoints + a synchronous single-project fetch (used by the POC and for spot checks).

The bulk crawl does not call fetch_project(); it enqueues the same endpoints in the persistent queue
(scraper/orchestrator.py) so it can resume.
"""
from __future__ import annotations

import logging

from database.db import RawStore
from scraper.endpoints import PROJECT_ENDPOINTS, PROMOTER_ENDPOINT
from scraper.network_client import FetchError, GujReraClient

log = logging.getLogger(__name__)

ENDPOINTS = [e for e in PROJECT_ENDPOINTS if e.family == "project_detail"]


def fetch_project(client: GujReraClient, raw: RawStore, project_id: int, with_promoter: bool = True) -> dict:
    """Fetch every registered endpoint for one project (resolving dependent ids) into raw_responses."""
    result = {"ok": [], "empty": [], "failed": []}
    ids: dict[str, object] = {}
    for ep in sorted(PROJECT_ENDPOINTS, key=lambda e: (e.depends_on is not None, e.priority)):
        if ep.depends_on:
            if not ids.get(ep.depends_on):
                result["empty"].append(ep.key)
                continue
            path = ep.path.format(**{ep.depends_on: ids[ep.depends_on]})
        else:
            path = ep.path.format(id=project_id)
        try:
            r = client.get(path, store=False)
        except FetchError as e:
            (result["empty"] if e.status in (400, 404) else result["failed"]).append(f"{ep.key}: {e.status}")
            continue
        raw.save_raw(url=r.url, status=r.status, content_type=r.content_type, body=r.content, sha256=r.sha256,
                     size=len(r.content), endpoint_key=ep.key, entity_type="project", entity_id=project_id)
        result["ok"].append(ep.key)
        if ep.key == "core":
            data = (r.json() or {}).get("data") or {}
            ids = {"formOneId": data.get("formOneId"), "formTwoId": data.get("formTwoId"),
                   "promoterId": data.get("promoterId")}
    if with_promoter and ids.get("promoterId"):
        r = client.get(PROMOTER_ENDPOINT.path.format(promoterId=ids["promoterId"]), store=False)
        raw.save_raw(url=r.url, status=r.status, content_type=r.content_type, body=r.content, sha256=r.sha256,
                     size=len(r.content), endpoint_key="promoter", entity_type="promoter", entity_id=ids["promoterId"])
        result["ok"].append("promoter")
    result["ids"] = ids
    return result
