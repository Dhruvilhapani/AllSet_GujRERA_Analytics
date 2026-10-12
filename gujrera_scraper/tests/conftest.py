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


@pytest.fixture
def raw_for():
    return load_raw


@pytest.fixture
def list_row_for():
    return list_row
