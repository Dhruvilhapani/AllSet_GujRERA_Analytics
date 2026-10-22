"""Developer tool: record the unit-layer responses of a few projects into tests/fixtures/units/<id>/.

Usage: python scripts/dump_unit_fixtures.py 12 16972 5162

Files written per project: qtr_form_ids.json, block_names.json, block_chart.json, block_master.json, form_three.json,
unit_list__<block>.json (one per block). Personal fields are scrubbed (set to null) before anything is written,
the same way the existing fixtures are anonymised: allottee name / KYC / mobile in unit rows and the chartered
accountant fields of the Form 3 header.
"""
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scraper.endpoints import PROJECT_ENDPOINTS_BY_KEY, UNIT_LIST  # noqa: E402
from scraper.network_client import FetchError, GujReraClient  # noqa: E402
from scraper.units import SOURCES, block_candidates  # noqa: E402

PII_KEYS = {"alloteeName", "mobileNumber", "kycId", "kycid", "typeofKYC", "caName", "caEmailId", "caFrnNo", "caUdiNo",
            "caMemberShipNumber", "caFirmName", "caDesignation", "caPlace", "promoterName", "formThreePdfUId",
            "formMofPdfUId"}


def scrub(o):
    if isinstance(o, dict):
        return {k: (None if k in PII_KEYS else scrub(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [scrub(v) for v in o]
    return o


def write(path: Path, content: bytes) -> None:
    j = scrub(json.loads(content))
    if path.name == "form_three.json" and isinstance(j.get("data"), dict):
        # the parser only reads the scalar header fields; the nested registration-time unit list is large
        j["data"] = {k: v for k, v in j["data"].items() if not isinstance(v, (list, dict))}
    path.write_text(json.dumps(j, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def main(ids):
    out_root = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "units"
    with GujReraClient(rate_per_sec=2.0, max_retries=2) as c:
        for pid in ids:
            d = out_root / str(pid)
            d.mkdir(parents=True, exist_ok=True)
            got = {}
            for key in ("qtr_form_ids", "block_names", "block_master"):
                got[key] = c.get(PROJECT_ENDPOINTS_BY_KEY[key].path.format(id=pid), store=False).content
                write(d / f"{key}.json", got[key])
            got["block_chart"] = c.get(f"dashboard/get-all-block-chart-details-by-id/{pid}", store=False).content
            write(d / "block_chart.json", got["block_chart"])
            ft = json.loads(got["qtr_form_ids"])["data"]["formThreeId"]
            try:
                write(d / "form_three.json", c.get(PROJECT_ENDPOINTS_BY_KEY["form_three"].path.format(formThreeId=ft),
                                                   store=False).content)
            except FetchError as e:
                print(f"  form_three {ft}: {e.status}")
            names, _expected, _src = block_candidates({k: json.loads(got[k]) for k in SOURCES})
            for name in names:
                r = c.post_json(UNIT_LIST.path, {"blockName": name, "formThreeId": ft}, store=False)
                safe = re.sub(r"[^A-Za-z0-9_+-]", "_", name)
                write(d / f"unit_list__{safe}.json", r.content)
                print(f"  {pid} block {name!r}: {len(json.loads(r.content).get('data') or [])} rows")
            time.sleep(0.5)


if __name__ == "__main__":
    main(sys.argv[1:])
