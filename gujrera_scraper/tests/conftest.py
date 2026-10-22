import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FIX = ROOT / "tests" / "fixtures"

# fixture file name (as written by scripts/dump_fixtures.py)  ->  endpoint key used by the pipeline
FILE_TO_KEY = {
    "project_reg__public__alldatabyprojectid__id": "core",
    "project_reg__public__getproject-details__id": "details",
    "project_reg__public__projectAllApplications__id": "applications",
    "project_reg__public__project-app__get-project-details-for-qpr__id": "qpr_details",
    "project_reg__public__getProjectProgressReports-ByPrjRegId__id": "progress_report",
    "quarter__get-project-progress__id": "qpr_progress",
    "quarter__public__getprojectqtrs__id": "qpr_quarters",
    "quarter__get-by-project-id__id": "qe_filing",
    "dashboard__get-all-block-chart-details-by-id__id": "block_chart",
    "dashboard__get-all-view-unit-details-by-id__id": "unit_summary",
    "form_five__get-formfive-records-till-date__id": "form_five",
    "project_reg__public__getproject-doc__id": "documents",
    "maplocation__public__getProjectLocations__id": "location",
    "dashboard__project-block-progress-dtl-by-prj-id__formOneId": "block_progress",
    "formtwo__public__getform-two-by-id__formTwoId": "form_two",
    "formone__public__getfrom-one-byformone-id__formOneId": "form_one",
    "project_reg__project__last-submitted-date__id": "last_submitted",
}


def load_raw(pid: int) -> dict:
    raw = {}
    for f in (FIX / str(pid)).glob("*.json"):
        key = FILE_TO_KEY.get(f.stem)
        if key:
            raw[key] = (json.loads(f.read_bytes()), {"id": 0, "url": f"fixture://{pid}/{key}", "fetched_at": "x"})
    return raw


def list_row(pid: int) -> dict:
    for t in ("Residential", "Mixed"):
        for r in json.loads((FIX / "lists" / f"{t}.json").read_bytes())["data"]:
            if int(r["projectRegId"]) == pid:
                return r
    raise KeyError(pid)


def load_units(pid: int) -> tuple[dict, dict]:
    """Unit-layer fixtures recorded by scripts/dump_unit_fixtures.py -> (project raw dict, unit_list responses).
    The unit_list responses are keyed '<pid>|<block>' with the url the crawler would store."""
    d = FIX / "units" / str(pid)
    raw = {}
    for key in ("qtr_form_ids", "block_names", "block_chart", "block_master", "form_three"):
        f = d / f"{key}.json"
        if f.exists():
            raw[key] = (json.loads(f.read_text(encoding="utf-8")),
                        {"id": 0, "url": f"fixture://{pid}/{key}", "fetched_at": "2026-10-21T00:00:00+00:00"})
    ft = raw["qtr_form_ids"][0]["data"]["formThreeId"]
    unit_raw = {}
    for f in sorted(d.glob("unit_list__*.json")):
        j = json.loads(f.read_text(encoding="utf-8"))
        rows = j.get("data") or []
        block = rows[0]["blockName"] if rows else f.stem.split("__", 1)[1].replace("_", " ")
        unit_raw[f"{pid}|{block}"] = (j, {"id": 0, "url": f"fixture://units#blockName={block}&formThreeId={ft}",
                                           "fetched_at": "2026-10-21T00:00:00+00:00"})
    return raw, unit_raw


@pytest.fixture
def raw_for():
    return load_raw


@pytest.fixture
def list_row_for():
    return list_row


@pytest.fixture
def units_for():
    return load_units
