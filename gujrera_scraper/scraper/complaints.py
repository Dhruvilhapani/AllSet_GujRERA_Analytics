"""Complaint / suo-motu / appeal list discovery.

The judgement page shows a summary grid (formType x district). Each cell is backed by:
  * named districts:   complain/ecourt/public/getcomplaints-by-distcode-formtype/{district}/{formType}/null
  * the "Others" col:  complain/ecourt/public/complaints-othersdist-byformType/{formType}/null
The summary itself tells us which (formType, district) combinations exist, so we never hardcode them.
Per-project complaint *status* (incl. pending matters with no order yet) comes from the project's
application list (endpoint `applications`), parsed in parser/complaint_parser.py.
"""
from __future__ import annotations

from urllib.parse import quote

from scraper.endpoints import (APPELLATE_TRIBUNAL, COMPLAINT_LIST_BY_DISTRICT, COMPLAINT_LIST_OTHERS,
                               COMPLAINT_SUMMARY, COURT_LISTS)

SUMMARY_PATH = COMPLAINT_SUMMARY.path
OTHERS_LABEL = "OTHERS"


def summary_rows(summary_json: dict) -> list[dict]:
    try:
        return summary_json["data"]["data"]["list"] or []
    except (KeyError, TypeError):
        return []


def list_tasks(summary_json: dict) -> list[tuple[str, str, str]]:
    """Return (endpoint_key, entity_id, path) for every complaint list implied by the summary."""
    form_types = {r["formType"] for r in summary_rows(summary_json) if r.get("formType")}
    # Suo-motu non-compliance rows (NCQR / NCQER / NCAR) are separate lists queried by their sub-type code;
    # the "General" suo-motu row is the SOUMOTO list itself (verified against the web UI's own requests).
    form_types |= {r["soumotocase"] for r in summary_rows(summary_json)
                   if r.get("formType") == "SOUMOTO" and str(r.get("soumotocase") or "").upper().startswith("NC")}
    form_types = sorted(form_types)
    districts = sorted({r["projectDistricName"] for r in summary_rows(summary_json)
                        if r.get("projectDistricName")})
    tasks: list[tuple[str, str, str]] = []
    for ft in form_types:
        # The UI queries every district column for every form type; mirror that (cells may be 0).
        for d in districts:
            if d.upper() == OTHERS_LABEL:
                continue
            tasks.append(("complaint_list", f"{ft}|{d}",
                          COMPLAINT_LIST_BY_DISTRICT.format(district=quote(d), form_type=ft)))
        tasks.append(("complaint_list", f"{ft}|{OTHERS_LABEL}", COMPLAINT_LIST_OTHERS.format(form_type=ft)))
    for key, path in COURT_LISTS.items():
        tasks.append((key, key, path))
    tasks.append(("appellate_tribunal", "appellate_tribunal", APPELLATE_TRIBUNAL))
    return tasks


def expected_counts(summary_json: dict) -> dict[str, int]:
    """Summary counts per formType (used to report coverage of complaint lists)."""
    out: dict[str, int] = {}
    for r in summary_rows(summary_json):
        out[r["formType"]] = out.get(r["formType"], 0) + int(r.get("requestCount") or 0)
    return out
