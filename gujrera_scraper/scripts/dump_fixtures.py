"""Developer tool: dump raw responses of every known per-project endpoint to tests/fixtures.

Usage: python scripts/dump_fixtures.py 62 2080 648
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scraper.network_client import FetchError, GujReraClient  # noqa: E402

PER_PROJECT = [
    "project_reg/public/alldatabyprojectid/{id}",
    "project_reg/public/getproject-details/{id}",
    "project_reg/public/getproject-doc/{id}",
    "project_reg/public/projectAllApplications/{id}",
    "project_reg/public/getprev-project-list/{id}",
    "project_reg/public/bank/get_col_account/{id}",
    "project_reg/public/project-app/getproject-banks/{id}",
    "project_reg/public/getProjectProgressReports-ByPrjRegId/{id}",
    "project_reg/public/project-app/get-project-details-for-qpr/{id}",
    "project_reg/project-app/getproject-blocklist/{id}",
    "project_reg/project/last-submitted-date/{id}",
    "quarter/get-project-progress/{id}",
    "quarter/public/getprojectqtrs/{id}",
    "quarter/public/get-qtr-form-details/{id}",
    "quarter/get-by-project-id/{id}",
    "quarter/getQEEnquiryStatus/{id}",
    "quarter/getformpdf-by-id/{id}",
    "dashboard/get-all-block-chart-details-by-id/{id}",
    "dashboard/get-all-view-unit-details-by-id/{id}",
    "formone/public/getform-one-form-two-id/{id}",
    "formone/public/getpublicform-one-form-two-id/{id}",
    "form_five/get-formfive-records-till-date/{id}",
    "citizen_survey/notification/get-by-process-id/{id}",
    "citizen_survey/citizen_claim/project-id{id}",
    "citizen_survey/citizen_claim/get-project-all-block{id}",
    "maplocation/public/getProjectLocations/{id}",
]


def safe(name: str) -> str:
    return name.replace("/", "__").replace("{", "").replace("}", "")


def main(ids):
    out_root = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
    with GujReraClient(rate_per_sec=2.0, max_retries=2) as c:
        for pid in ids:
            d = out_root / str(pid)
            d.mkdir(parents=True, exist_ok=True)
            results = {}
            for tpl in PER_PROJECT:
                path = tpl.format(id=pid)
                t = time.time()
                try:
                    r = c.get(path, store=False)
                    (d / (safe(tpl) + ".json")).write_bytes(r.content)
                    results[tpl] = f"{r.status} {len(r.content)}B {time.time()-t:.1f}s"
                except FetchError as e:
                    results[tpl] = f"ERR {e.status}"
            # dependent endpoints
            try:
                core = json.loads((d / (safe(PER_PROJECT[0]) + ".json")).read_bytes())["data"]
                deps = {
                    "user_reg/promoter/promoter{promoterId}": f"user_reg/promoter/promoter{core.get('promoterId')}",
                    "formtwo/public/getform-two-by-id/{formTwoId}": f"formtwo/public/getform-two-by-id/{core.get('formTwoId')}",
                    "formone/public/getfrom-one-byformone-id/{formOneId}": f"formone/public/getfrom-one-byformone-id/{core.get('formOneId')}",
                    "dashboard/project-block-progress-dtl-by-prj-id/{formOneId}": f"dashboard//project-block-progress-dtl-by-prj-id/{core.get('formOneId')}",
                    "formone/public/project/noc/getnoc-by-proj-proid-type/{id}/{formOneId}": f"formone/public/project/noc/getnoc-by-proj-proid-type/{pid}/{core.get('formOneId')}",
                }
                for tpl, path in deps.items():
                    t = time.time()
                    try:
                        r = c.get(path, store=False)
                        (d / (safe(tpl) + ".json")).write_bytes(r.content)
                        results[tpl] = f"{r.status} {len(r.content)}B {time.time()-t:.1f}s"
                    except FetchError as e:
                        results[tpl] = f"ERR {e.status}"
            except Exception as e:  # noqa: BLE001
                results["deps"] = f"ERR {e!r}"
            print(f"== {pid}")
            for k, v in results.items():
                print(f"  {v:>22}  {k}")


if __name__ == "__main__":
    main(sys.argv[1:] or ["62"])
