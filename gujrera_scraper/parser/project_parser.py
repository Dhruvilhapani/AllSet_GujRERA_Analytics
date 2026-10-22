"""Turns the raw responses of one project into normalized rows.

Input:  the project's row from the official list + {endpoint_key: (json, meta)} from raw_responses.
Output: ParseResult with rows per table, provenance records and detected conflicts.
No value is invented: unavailable data stays None and its state is recorded.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from parser import qpr_parser
from parser.common import (classify_unit_type, clean_str, parse_date, parse_reg_no, to_float, to_int)

SRC = {
    "list": "GujRERA registered-project list (dashboard/get-district-wise-projectlist)",
    "core": "GujRERA project core record (project_reg/public/alldatabyprojectid)",
    "details": "GujRERA project details (project_reg/public/getproject-details)",
    "applications": "GujRERA project applications (project_reg/public/projectAllApplications)",
    "qpr_details": "GujRERA QPR project header (project_reg/public/project-app/get-project-details-for-qpr)",
    "progress_report": "GujRERA progress report (project_reg/public/getProjectProgressReports-ByPrjRegId)",
    "qpr_progress": "GujRERA QPR progress history (quarter/get-project-progress)",
    "qpr_quarters": "GujRERA QPR filing calendar (quarter/public/getprojectqtrs)",
    "qe_filing": "GujRERA Q-E project-end filing (quarter/get-by-project-id)",
    "block_chart": "GujRERA block booking chart (dashboard/get-all-block-chart-details-by-id)",
    "unit_summary": "GujRERA unit summary (dashboard/get-all-view-unit-details-by-id)",
    "form_five": "GujRERA annual audit Form 5 list (form_five/get-formfive-records-till-date)",
    "documents": "GujRERA project documents (project_reg/public/getproject-doc)",
    "location": "GujRERA map location (maplocation/public/getProjectLocations)",
    "block_progress": "GujRERA block progress (dashboard//project-block-progress-dtl-by-prj-id)",
    "form_two": "GujRERA engineer certificate Form 2 (formtwo/public/getform-two-by-id)",
    "derived": "Derived by this pipeline from the sources listed in its rule",
}

CRITICAL_DOC_TYPES = {"registration_certificate", "extension_certificate", "qe_form_four", "completion_certificate"}


@dataclass
class ParseResult:
    rows: dict[str, list[dict]] = field(default_factory=dict)
    provenance: list[dict] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    def add(self, table: str, row: dict) -> None:
        self.rows.setdefault(table, []).append(row)


def _data(raw: dict, key: str) -> Any:
    v = raw.get(key)
    if not v or v[0] is None:
        return None
    j = v[0]
    if isinstance(j, dict) and "data" in j and key not in ("form_two", "location"):
        return j.get("data")
    return j


def _url(raw: dict, key: str) -> str | None:
    v = raw.get(key)
    return v[1]["url"] if v and v[1] else None


def _camel_to_snake(s: str) -> str:
    s = re.sub(r"(UId|Uid|_UId)$", "", s)
    s = re.sub(r"(?<!^)(?=[A-Z])", "_", s).lower()
    return re.sub(r"_+", "_", s).strip("_")


class ProjectParser:
    def __init__(self, list_row: dict, raw: dict, run_date: str, extracted_at: str):
        self.L = list_row or {}
        self.raw = raw
        self.run_date = run_date
        self.now = extracted_at
        self.pid = int(self.L.get("projectRegId") or self.L.get("project_id"))
        self.r = ParseResult()

    # ------------------------------------------------------------------ helpers
    def prov(self, field_: str, value, source_key: str, confidence: str = "high", state: str | None = None,
             source_document: str | None = None) -> None:
        if state is None:
            state = "NULL" if value is None else "REPORTED"
        self.r.provenance.append({
            "entity_type": "project", "entity_id": str(self.pid), "field": field_,
            "value": None if value is None else str(value), "value_state": state,
            "source": SRC.get(source_key, source_key), "source_url": _url(self.raw, source_key),
            "source_document": source_document, "extracted_at": self.now, "last_verified_at": self.now,
            "confidence": confidence})

    def conflict(self, field_: str, v1, s1: str, v2, s2: str, interpreted, rule: str,
                 status: str = "resolved_by_rule") -> None:
        if v1 is None or v2 is None or str(v1) == str(v2):
            return
        self.r.conflicts.append({
            "entity_type": "project", "entity_id": str(self.pid), "field": field_,
            "value_1": str(v1), "source_1": s1, "value_2": str(v2), "source_2": s2,
            "conflict_status": status, "interpreted_value": None if interpreted is None else str(interpreted),
            "rule": rule, "detected_at": self.now})

    # ------------------------------------------------------------------ main
    def parse(self) -> ParseResult:
        core = _data(self.raw, "core") or {}
        details = _data(self.raw, "details") or {}
        pd = details.get("projectDetail") or {}
        qd = _data(self.raw, "qpr_details") or {}
        apps = _data(self.raw, "applications") or []
        if not isinstance(apps, list):
            apps = []

        reg_current = clean_str(core.get("projRegNo")) or clean_str(self.L.get("regNo"))
        self.conflict("reg_no_current", self.L.get("regNo"), "list", core.get("projRegNo"), "core",
                      reg_current, "core record preferred (fetched later than list)")
        reg = parse_reg_no(reg_current)
        project_type = clean_str(self.L.get("projectType")) or clean_str(core.get("projectType"))

        # --- dates
        registration_date = parse_date(self.L.get("approvedOn")) or parse_date(core.get("approvedDate"))
        start = parse_date(pd.get("startDate")) or parse_date(self.L.get("startDate"))
        orig_list = parse_date(self.L.get("projOrgFDate"))
        orig_qpr = parse_date(qd.get("endDateAtTimeOfRegistration"))
        original_completion = orig_list or orig_qpr
        self.conflict("original_completion_date", orig_list, "list.projOrgFDate", orig_qpr,
                      "qpr_details.endDateAtTimeOfRegistration", original_completion,
                      "list.projOrgFDate preferred", status="unresolved")

        cur_detail = parse_date(pd.get("completionDate"))
        cur_list = parse_date(self.L.get("endDate"))
        cur_qpr = parse_date(qd.get("endDateAsPerExtension"))
        cur_regno = reg.valid_till if reg else None
        current_completion = cur_detail or cur_list or cur_qpr or cur_regno or original_completion
        cc_source = ("details" if cur_detail else "list" if cur_list else "qpr_details" if cur_qpr
                     else "derived" if cur_regno else "list")
        for v, s in ((cur_list, "list.endDate"), (cur_qpr, "qpr_details.endDateAsPerExtension"),
                     (cur_regno, "reg_no valid-till token")):
            self.conflict("current_completion_date", cur_detail, "details.completionDate", v, s,
                          current_completion, "project details record preferred; see field_conflicts",
                          status="unresolved")

        # --- extensions
        extensions = self.parse_extensions(apps, reg, original_completion, current_completion)
        approved_ext = [e for e in extensions if e["status"] == "approved"]
        ext_count_apps = len(approved_ext)
        ext_count_reg = reg.ex_count if reg else 0
        extension_count = max(ext_count_apps, ext_count_reg)
        if "applications" in self.raw:   # only comparable once the application list was fetched
            self.conflict("extension_count", ext_count_apps, "applications (approved Extension rows)", ext_count_reg,
                          "EXn marker in registration number", extension_count,
                          "max of both: some early extensions were processed before online applications existed")

        # --- QPR / progress / completion
        q = qpr_parser.parse_qpr(self.pid, self.raw, self.run_date)
        for table, rows in q.rows.items():
            for row in rows:
                self.r.add(table, row)

        # --- inventory
        inv = self.parse_inventory(details, qd)

        # --- portal progress report
        pr = _data(self.raw, "progress_report") or {}

        # --- status derivation
        status_source = clean_str(self.L.get("project_status")) or clean_str(pd.get("projectStatus"))
        status_derived, basis = derive_status(
            status_source=status_source, qe_completed=q.qe_completed, qe_status=q.qe_status,
            completion_cert=bool(clean_str(pd.get("compeletionCertDocUId"))),
            current_completion=current_completion, extension_count=extension_count,
            run_date=self.run_date, revoked_evidence=self.revocation_evidence(apps),
            has_detail="qe_filing" in self.raw and "core" in self.raw)

        project = {
            "project_id": self.pid,
            "reg_serial": reg.serial if reg else None,
            "reg_no_current": reg_current,
            "reg_no_original": reg_current if reg and reg.ex_count == 0 else None,
            "ack_no": clean_str(core.get("projectAckNo")) or clean_str(self.L.get("project_ack_no")),
            "project_name": clean_str(self.L.get("projectName")) or clean_str(core.get("projectName")),
            "project_type": project_type,
            "project_category": category_of(project_type),
            "affordable_housing": clean_str(qd.get("affordableHousing")),
            "description": clean_str(pd.get("projectDesc")),
            "status_source": status_source,
            "status_derived": status_derived,
            "status_derived_basis": basis,
            "portal_progress_label": clean_str(pr.get("progress")),
            "portal_progress_score": to_float(pr.get("score")),
            "portal_arch_score": to_float(pr.get("archScore")),
            "portal_engg_score": to_float(pr.get("enggScore")),
            "portal_time_lapse_pct": to_float(pr.get("timeLaps")),
            "registration_date": registration_date,
            "application_date": parse_date(core.get("appSubmissionDate")) or parse_date(
                self.L.get("hardcopysubmissionDate")),
            "project_start_date": start,
            "original_completion_date": original_completion,
            "current_completion_date": current_completion,
            "completion_filing_date": q.qe_submitted_on,
            "completion_filing_status": q.qe_status,
            "construction_complete_per_qpr": q.construction_complete,
            "extension_count": extension_count,
            "alteration_count": len(reg.alteration_markers) if reg else None,
            "latest_progress_pct": q.latest_progress,
            "latest_progress_quarter": q.latest_quarter,
            "latest_progress_date": q.latest_date,
            "financial_progress_pct": q.financial_progress,
            **inv,
            "total_blocks": to_int(qd.get("noOfBlocks")),
            "parking_for_sale": to_int(pd.get("noOfParkinfForSale")),
            "garages_for_sale": to_int(pd.get("noOfGarageForSale")),
            "covered_parking": to_int(pd.get("coveredParking")),
            "total_land_area_sqm": to_float(pd.get("totAreaOfLand")) or to_float(pd.get("totLandAreaForProjectUnderReg"))
            or to_float(pd.get("totAreaOfLandLayout")),
            "total_carpet_area_sqm": to_float(pd.get("totCarpetAreaForProjectUnderReg")),
            "total_covered_area_sqm": to_float(pd.get("totCoverdArea")),
            "total_open_area_sqm": to_float(pd.get("totOpenArea")),
            "cost_of_land": to_float(pd.get("costOfLand")),
            "estimated_construction_cost": to_float(pd.get("estimatedCost")),
            "total_project_cost": to_float(pd.get("totalProjectCost")) or to_float(self.L.get("total_est_cost_of_proj")),
            "approving_authority": clean_str(pd.get("approvingAuthority")),
            "months_delayed_declared": to_int(pd.get("noOfMonthDelayed")),
            "last_qpr_submitted_at": q.last_submitted,
            "portal_last_submitted_at": self._portal_last_submitted(),
            "details_updated_on": parse_date(pd.get("updatedOn")),
            "engineer_cert_submitted_on": parse_date(
                (self.raw.get("form_two") or ({},))[0].get("formTwoSubmittedOn")
                if isinstance((self.raw.get("form_two") or ({},))[0], dict) else None),
            "source_endpoint": "list+core+details",
        }
        self.r.add("projects", project)

        # provenance for key analytical fields
        self.prov("registration_date", registration_date, "list")
        self.prov("original_completion_date", original_completion, "list" if orig_list else "qpr_details")
        self.prov("current_completion_date", current_completion, cc_source,
                  confidence="high" if cur_detail else "medium")
        self.prov("extension_count", extension_count, "applications", confidence="high" if ext_count_apps == ext_count_reg else "medium",
                  state="DERIVED")
        self.prov("status_derived", status_derived, "derived", confidence="medium", state="DERIVED")
        self.prov("latest_progress_pct", q.latest_progress, "qpr_progress")
        self.prov("financial_progress_pct", q.financial_progress, "form_two", state=None if q.financial_progress is None else "DERIVED")
        self.prov("completion_filing_date", q.qe_submitted_on, "qe_filing")
        self.prov("total_units", inv["total_units"], "unit_summary", state=inv["total_units_state"])
        self.prov("booked_units", inv["booked_units"], "block_chart", state=inv["inventory_state"])
        self.prov("unbooked_units", inv["unbooked_units"], "block_chart", state=inv["inventory_state"])

        self.parse_location(pd)
        self.parse_infrastructure(details, pd)
        self.parse_professionals(details)
        self.parse_applications(apps)
        for e in extensions:
            self.r.add("project_extensions", e)
        self.parse_documents(core, pd, apps, extensions)
        self.parse_form_five()
        promoter_id = to_int(core.get("promoterId"))
        if promoter_id:
            self.r.add("project_developers", {"project_id": self.pid, "developer_id": promoter_id,
                                              "role": "promoter", "source_endpoint": "core"})
            for alias, src in ((self.L.get("promoterName"), "project_list"), (core.get("promoterName"), "core")):
                if clean_str(alias):
                    self.r.add("developer_aliases", {"developer_id": promoter_id, "alias": clean_str(alias),
                                                     "source": src})
        else:
            self.r.issues.append("promoterId missing in core record")
        return self.r

    # ------------------------------------------------------------------ sections
    def _portal_last_submitted(self) -> str | None:
        """'2019-04-05 16:00:58.0' -> '2019-04-05 16:00:58' (None when the portal answers status 300 / no date)."""
        v = _data(self.raw, "last_submitted")
        s = clean_str(v) if isinstance(v, str) else None
        return s.split(".")[0] if s and parse_date(s) else None

    def parse_inventory(self, details: dict, qd: dict) -> dict:
        us = _data(self.raw, "unit_summary")
        us0 = us[0] if isinstance(us, list) and us else {}
        chart = _data(self.raw, "block_chart")
        chart = chart if isinstance(chart, list) else []
        booked = sum(to_int(c.get("booked")) or 0 for c in chart) if chart else None
        unbooked = sum(to_int(c.get("unbooked")) or 0 for c in chart) if chart else None
        tot = to_int(us0.get("totunit"))
        chart_total = (booked or 0) + (unbooked or 0) if chart else None
        self.conflict("total_units", tot, "unit_summary.totunit", chart_total, "block_chart booked+unbooked",
                      tot, "unit summary preferred for total; booking split from block chart",
                      status="unresolved")
        # unit-type inventory (registration declaration)
        res = com = oth = 0
        have_types = False
        seen: set[str] = set()
        for dev in details.get("dev") or []:
            for it in dev.get("internalDev") or []:
                t = clean_str(it.get("typeOfInventory")) or "UNSPECIFIED"
                n = to_int(it.get("noOfInventory"))
                key = t
                i = 2
                while key in seen:
                    key = f"{t} #{i}"
                    i += 1
                seen.add(key)
                cls = classify_unit_type(t)
                self.r.add("project_units", {
                    "project_id": self.pid, "unit_type_raw": key, "unit_class": cls, "units_planned": n,
                    "units_booked_declared": to_int(it.get("noOfInventoryBooked")),
                    "units_completed_declared": to_int(it.get("noOfInventoryCompleted")),
                    "carpet_area_sqm": to_float(it.get("carpetArea")),
                    "exclusive_area_sqm": to_float(it.get("areaOfExclusive")),
                    "open_terrace_area_sqm": to_float(it.get("areaOfExclusiveOpenTerrace")),
                    "source_endpoint": "details"})
                if n is not None:
                    have_types = True
                    if cls == "residential":
                        res += n
                    elif cls == "commercial":
                        com += n
                    else:
                        oth += n
        if tot is not None:
            tot_state = "REPORTED"
        elif chart:
            tot, tot_state = chart_total, "REPORTED"
        else:
            tot_state = "NULL"
        inv_state = "REPORTED" if chart else ("NULL" if us is None and _data(self.raw, "block_chart") is None else "NULL")
        # blocks
        self.parse_blocks(chart, qd)
        return {
            "total_units": tot, "total_units_state": tot_state,
            "booked_units": booked, "unbooked_units": unbooked, "inventory_state": inv_state,
            "residential_units": res if have_types else None,
            "commercial_units": com if have_types else None,
            "other_units": oth if have_types else None,
            "min_unit_carpet_sqm": to_float(us0.get("mincararea")),
            "max_unit_carpet_sqm": to_float(us0.get("maxcararea")),
            "min_unit_cost": to_float(us0.get("mincost")),
            "max_unit_cost": to_float(us0.get("maxcost")),
        }

    def parse_blocks(self, chart: list, qd: dict) -> None:
        blocks: dict[str, dict] = {}

        def b(name) -> dict:
            name = clean_str(name) or "UNNAMED"
            if name not in blocks:
                blocks[name] = {"project_id": self.pid, "block_name": name, "planned_units": None,
                                "booked_units": None, "unbooked_units": None, "booked_pct_source": None,
                                "progress_pct": None, "engineer_progress_pct": None, "work_done_pct_form2": None,
                                "estimated_cost_form2": None, "cost_incurred_form2": None,
                                "dev_start_date": None, "dev_end_date": None, "source_endpoint": ""}
            return blocks[name]

        def tag(row, src):
            row["source_endpoint"] = ",".join(filter(None, [row["source_endpoint"], src]))

        for c in chart:
            row = b(c.get("bname"))
            row["booked_units"] = to_int(c.get("booked"))
            row["unbooked_units"] = to_int(c.get("unbooked"))
            row["booked_pct_source"] = to_float(c.get("perc"))
            tag(row, "block_chart")
        for c in qd.get("inventoryCountDetails") or []:
            row = b(c.get("block_name"))
            row["planned_units"] = to_int(c.get("cnt"))
            tag(row, "qpr_details")
        bp = _data(self.raw, "block_progress")
        for c in bp if isinstance(bp, list) else []:
            row = b(c.get("blk_name"))
            row["progress_pct"] = to_float(c.get("block_progress"))
            row["engineer_progress_pct"] = to_float(c.get("form_b_prog_report"))
            row["dev_start_date"] = parse_date(c.get("blk_dev_start_date"))
            row["dev_end_date"] = parse_date(c.get("blk_dev_end_date"))
            tag(row, "block_progress")
        f2 = self.raw.get("form_two")
        f2 = f2[0] if f2 and isinstance(f2[0], dict) else {}
        for c in f2.get("formTwoAList") or []:
            row = b(c.get("blockName"))
            row["work_done_pct_form2"] = to_float(c.get("workDonePer"))
            row["estimated_cost_form2"] = to_float(c.get("estimatedCost"))
            row["cost_incurred_form2"] = to_float(c.get("costInncurred"))
            tag(row, "form_two")
        for row in blocks.values():
            self.r.add("project_blocks", row)

    def parse_location(self, pd: dict) -> None:
        loc = self.raw.get("location")
        loc = loc[0] if loc and isinstance(loc[0], dict) else {}
        coords = [(to_float(c.get("lat")), to_float(c.get("lang"))) for c in loc.get("coordinates") or []]
        coords = [(a, b) for a, b in coords if a and b and 20 < a < 25 and 68 < b < 75]  # Gujarat bounding box
        lat = sum(a for a, _ in coords) / len(coords) if coords else None
        lon = sum(b for _, b in coords) / len(coords) if coords else None
        survey = clean_str(pd.get("revenueSurveyNo"))
        block_no = None
        if survey:
            m = re.search(r"block\s*(?:no)?\.?\s*[:\-–]*\s*([0-9A-Za-z/+\- ,]+?)(?:,|\s+r\.?\s*s|$)", survey, re.I)
            block_no = clean_str(m.group(1)) if m else None
        a1, a2 = clean_str(pd.get("projectAddress")), clean_str(pd.get("projectAddress2"))
        self.r.add("project_locations", {
            "project_id": self.pid,
            "address_line1": a1, "address_line2": a2,
            "full_address": ", ".join(filter(None, [a1, a2])) or clean_str(self.L.get("project_address")),
            "district": clean_str(pd.get("distName")) or clean_str(self.L.get("districtName")),
            "district_code": clean_str(pd.get("distCode")),
            "taluka": clean_str(pd.get("subDistName")) or clean_str(pd.get("subDistCode")),
            "village_moje": clean_str(pd.get("moje")),
            "city": None,  # not published as a separate field
            "pincode": clean_str(pd.get("pinCode")),
            "planning_authority": clean_str(pd.get("approvingAuthority")) or clean_str(pd.get("validTpName")),
            "tp_number": clean_str(pd.get("tPNo")), "tp_name": clean_str(pd.get("tPName")),
            "fp_number": clean_str(pd.get("finalPlotNo")) or clean_str(pd.get("plotNo")),
            "survey_number": survey,
            "city_survey_number": clean_str(pd.get("citySurveyNo")),
            "block_number": block_no,
            "sub_plot_number": clean_str(pd.get("subPlotNumber")),
            "latitude": lat, "longitude": lon, "polygon_points": len(coords) or None,
            "source_endpoint": "details+location",
        })

    # fields of projectDetail / dev[] that describe water, sewage and near-by facilities (names are the portal's)
    _NEARBY = ("airportDistance", "ulbServiceDelivery", "publicTransit", "publicGarden", "policeStation",
               "mainRailwayStation", "multiSpecialityHospital", "fireStation")
    _WATER_SEWAGE = ("waterLevel", "waterSplyFlag", "waterSplyFromSource", "waterSplyFromOther", "resDrinkWaterReq",
                     "comDrinkWaterReq", "totalDrinkWaterReq", "resDrinkWaterProps", "comDrinkWaterProps",
                     "totalDrinkWaterProps", "totalwaterPropsPercen", "resQuantySew", "comQuantySew", "totalQuantySew",
                     "seawageDisDistance", "sewDisposalFlag", "sewPrjTreatPrjSource", "sewPrjTreatOther",
                     "sewRecEfflTreat", "sewRecEfflTreatFlag", "sewSolidWasteSource", "sewSolidWasteOther",
                     "sewSwimCapacityFlag", "sewSwimCapLenght", "sewSwimCapBreadth", "sewSwimCapHeight",
                     "sewSwimCapLiter")

    def parse_infrastructure(self, details: dict, pd: dict) -> None:
        """External development ("developed by"), water/sewage and near-by facilities as declared at registration.
        The public API has no structured gym/pool style amenity list; nothing is invented here."""
        seen: set[tuple[str, str]] = set()

        def add(category: str, name: str, value) -> None:
            v = clean_str(value) if not isinstance(value, (dict, list)) else None
            if v is None or (category, name) in seen:
                return
            seen.add((category, name))
            self.r.add("project_infrastructure", {"project_id": self.pid, "category": category, "name": name,
                                                  "value": v, "source_endpoint": "details"})

        for name in self._NEARBY:
            add("nearby", _camel_to_snake(name), pd.get(name))
        for dev in details.get("dev") or []:
            ext = dev.get("externalDev")
            if isinstance(ext, dict):
                for k, v in ext.items():
                    if k.endswith("Id") or k == "extOtherDevSet":
                        continue
                    add("external_development", _camel_to_snake(k), v)
                for i, other in enumerate(ext.get("extOtherDevSet") or [], start=1):
                    if isinstance(other, dict):
                        desc = " | ".join(f"{_camel_to_snake(k)}: {clean_str(v)}" for k, v in other.items()
                                          if clean_str(v) and not k.endswith("Id"))
                        add("other_development", f"item_{i}", desc or None)
            for name in self._WATER_SEWAGE:
                add("water_sewage", _camel_to_snake(name), dev.get(name))
        for name in self._WATER_SEWAGE:   # some projects carry these on projectDetail instead of dev[]
            add("water_sewage", _camel_to_snake(name), pd.get(name))

    def parse_professionals(self, details: dict) -> None:
        seen = set()
        for role, lst, name_k, lic_k in (("engineer", "englist", "name", "licenceNO"),
                                         ("architect", "acrchlist", "name", "localAuthorityLicenceNo"),
                                         ("contractor", "contr", "contractortName", None),
                                         ("chartered_accountant", "calist", "name", None),
                                         ("agent", "agentlist", "name", None)):
            for p in details.get(lst) or []:
                nm = clean_str(p.get(name_k)) or clean_str(p.get("caName")) or clean_str(p.get("agentName"))
                if not nm or (role, nm) in seen:
                    continue
                seen.add((role, nm))
                self.r.add("project_professionals", {"project_id": self.pid, "role": role, "name": nm,
                                                     "licence_no": clean_str(p.get(lic_k)) if lic_k else None})

    def parse_applications(self, apps: list) -> None:
        seen = set()
        for a in apps:
            k = (to_int(a.get("appRegId")), clean_str(a.get("applicationType")))
            if k in seen or k[0] is None:
                continue
            seen.add(k)
            self.r.add("project_applications", {
                "project_id": self.pid, "app_reg_id": k[0], "application_type": k[1],
                "application_number": clean_str(a.get("applicationNumber")),
                "submission_date": parse_date(a.get("submissionDate")),
                "approval_date": parse_date(a.get("approvalDate")),
                "final_status": clean_str(a.get("finalStatus")),
                "complaint_number": clean_str(a.get("complaintNumber")),
                "complaint_status": clean_str(a.get("complaintStatus")),
                "challan_number": clean_str(a.get("challanNumber")),
                "challan_amount": to_float(a.get("challanAmount")),
                "challan_status": clean_str(a.get("challanStatus")),
                "challan_payment_date": parse_date(a.get("challanPaymentDate")),
                "certificate_uid": _uid(a.get("certificate")),
            })

    def parse_extensions(self, apps: list, reg, original_completion: str | None,
                         current_completion: str | None) -> list[dict]:
        ext_apps = [a for a in apps if (clean_str(a.get("applicationType")) or "").lower() == "extension"]

        def sort_key(a):
            return (parse_date(a.get("approvalDate")) or "9999", parse_date(a.get("submissionDate")) or "9999")

        ext_apps.sort(key=sort_key)
        out = []
        prev_end = original_completion
        approved_idx = [i for i, a in enumerate(ext_apps) if parse_date(a.get("approvalDate"))]
        last_approved = approved_idx[-1] if approved_idx else None
        for i, a in enumerate(ext_apps, start=1):
            rn = parse_reg_no(a.get("applicationNumber"))
            approved_on = parse_date(a.get("approvalDate"))
            final = (clean_str(a.get("finalStatus")) or "").lower()
            if "reject" in final:
                status = "rejected"
            elif approved_on:
                status = "approved"
            elif final:
                status = final
            else:
                status = "pending"
            ext_end, ext_src = (rn.valid_till, "reg_no_suffix") if rn and rn.valid_till else (None, None)
            if ext_end is None and status == "approved" and (i - 1) == last_approved and current_completion:
                ext_end, ext_src = current_completion, "project_current_completion_date"
            days = None
            if ext_end and prev_end:
                days = (date.fromisoformat(ext_end) - date.fromisoformat(prev_end)).days
            out.append({
                "project_id": self.pid, "extension_seq": i, "application_id": to_int(a.get("appRegId")),
                "extension_reg_no": rn.raw if rn else clean_str(a.get("applicationNumber")),
                "ex_markers": "/".join(rn.ex_markers) if rn else None,
                "submitted_on": parse_date(a.get("submissionDate")), "approved_on": approved_on,
                "status": status, "previous_completion_date": prev_end,
                "extended_completion_date": ext_end, "extended_completion_date_source": ext_src,
                "extension_days": days, "reason": None,
                "certificate_uid": _uid(a.get("certificate")), "source_endpoint": "applications"})
            if status == "approved":
                # an approved extension with unknown end date makes the next 'previous' date unknown too
                prev_end = ext_end
        return out

    def revocation_evidence(self, apps: list) -> str | None:
        for a in apps:
            t = " ".join(filter(None, [clean_str(a.get("applicationType")), clean_str(a.get("finalStatus"))]))
            if re.search(r"revok|revoc|deregist|cancel+ed registration|lapse", t, re.I):
                return f"application {a.get('applicationNumber')}: {t}"
        return None

    def parse_documents(self, core: dict, pd: dict, apps: list, extensions: list) -> None:
        docs: dict[str, dict] = {}
        ext_cert = {e["certificate_uid"]: e for e in extensions if e.get("certificate_uid")}

        def add(uid, dtype, src, name=None, ddate=None, doc_id=None):
            uid = _uid(uid)
            if not uid or uid in docs:
                return
            docs[uid] = {"document_uid": uid, "document_id": to_int(doc_id), "project_id": self.pid,
                         "related_key": None, "document_type": dtype, "document_name": name,
                         "document_date": ddate, "source_endpoint": src,
                         "download_url": f"https://gujrera.gujarat.gov.in/vdms/download/{uid}",
                         "file_type": "pdf",
                         "download_priority": "critical" if dtype in CRITICAL_DOC_TYPES else "metadata_only"}

        for uid, e in ext_cert.items():
            add(uid, "extension_certificate", "applications", e["extension_reg_no"], e["approved_on"])
        # once a project is extended, the "current" certificate on the core record is the latest extension
        # certificate (document type is re-checked from the PDF content after download)
        cur_reg = parse_reg_no(core.get("projRegNo"))
        if cur_reg and cur_reg.ex_count > 0:
            add(core.get("certificateUid"), "extension_certificate", "core", core.get("projRegNo"),
                parse_date(core.get("approvedDate")), core.get("certificateId"))
        for a in apps:
            if (clean_str(a.get("applicationType")) or "").lower() == "registration":
                add(a.get("certificate"), "registration_certificate", "applications",
                    clean_str(a.get("applicationNumber")), parse_date(a.get("approvalDate")))
        add(core.get("certificateUid"), "registration_certificate", "core", core.get("projRegNo"),
            parse_date(core.get("approvedDate")), core.get("certificateId"))
        add(core.get("extcertificateUid"), "extension_certificate", "core", core.get("projRegNo"))
        add(pd.get("compeletionCertDocUId"), "completion_certificate", "details", None, None,
            pd.get("compeletionCertDocId"))
        add(pd.get("projectCertDocUId"), "project_cert_upload", "details", None, None, pd.get("projectCertDocId"))
        add(pd.get("declarationFormbNewDocUId"), "declaration_form_b", "details", None, None,
            pd.get("declarationFormbNewDocId"))
        qe = _data(self.raw, "qe_filing") or {}
        if isinstance(qe, dict):
            for k, t in (("qt_form_four_a_docuid", "qe_form_four"), ("qt_addidavit_docuid", "qe_affidavit"),
                         ("five_doc_uid", "qe_form_five"), ("qt_notarized_docuid", "qe_notarized_doc"),
                         ("qt_revenue_docuid", "qe_revenue_doc"), ("qt_society_reg_docuid", "qe_society_registration")):
                add(qe.get(k), t, "qe_filing", None, parse_date(qe.get("submitted_on")))
        f2 = self.raw.get("form_two")
        f2 = f2[0] if f2 and isinstance(f2[0], dict) else {}
        add(f2.get("formTwoPdfUId"), "engineer_certificate_form2", "form_two", None,
            parse_date(f2.get("formTwoSubmittedOn")), f2.get("formTwoPdfId"))
        add(f2.get("formAnnxPdfUId"), "engineer_certificate_form2_annexure", "form_two", None,
            parse_date(f2.get("formTwoSubmittedOn")), f2.get("formAnnxPdfId"))
        dd = _data(self.raw, "documents") or {}
        if isinstance(dd, dict):
            for sect, obj in dd.items():
                if not isinstance(obj, dict):
                    continue
                for k, v in obj.items():
                    if k.lower().endswith("uid") and isinstance(v, str):
                        add(v, _camel_to_snake(k), f"documents.{sect}")
        for row in docs.values():
            self.r.add("project_documents", row)

    def parse_form_five(self) -> None:
        ff = self.raw.get("form_five")
        ff = ff[0] if ff else None
        if not isinstance(ff, list):
            return
        seen = set()
        for r in ff:
            fy = clean_str(r.get("projectFinancialYear"))
            if not fy or fy in seen:
                continue
            seen.add(fy)
            self.r.add("annual_compliance", {"project_id": self.pid, "financial_year": fy,
                                             "status": clean_str(r.get("projectFinancialYearStatus")),
                                             "pdf_uid": _uid(r.get("pdfUid"))})
            if _uid(r.get("pdfUid")):
                self.r.add("project_documents", {
                    "document_uid": _uid(r.get("pdfUid")), "document_id": None, "project_id": self.pid,
                    "related_key": None, "document_type": "annual_audit_form5", "document_name": f"Form 5 {fy}",
                    "document_date": None, "source_endpoint": "form_five",
                    "download_url": f"https://gujrera.gujarat.gov.in/vdms/download/{_uid(r.get('pdfUid'))}",
                    "file_type": "pdf", "download_priority": "metadata_only"})


def _uid(v) -> str | None:
    s = clean_str(v)
    if not s or len(s) < 20 or not re.fullmatch(r"[A-Za-z0-9]+", s):
        return None  # e.g. 'AMIT' placeholders in annual-compliance rows
    return s


def category_of(project_type: str | None) -> str | None:
    """residential | mixed | commercial | plotted (portal labels: Residential/Group Housing, Mixed Development,
    Commercial, Plotted Development). Unknown labels pass through lower-cased."""
    if not project_type:
        return None
    t = project_type.lower()
    if "mixed" in t:
        return "mixed"
    if "resid" in t:
        return "residential"
    if "commerc" in t:
        return "commercial"
    if "plot" in t:
        return "plotted"
    return t


def derive_status(*, status_source, qe_completed, qe_status, completion_cert, current_completion, extension_count,
                  run_date, revoked_evidence, has_detail: bool = True) -> tuple[str, str]:
    """Rules (first match wins) — documented in docs/data_dictionary.md#status_derived."""
    if revoked_evidence:
        return "cancelled_revoked", revoked_evidence
    if (status_source or "").lower() == "completed":
        return "completed", "portal project_status = Completed"
    if not has_detail:
        return "unknown", "project detail / Q-E filing not crawled yet (list data only)"
    if qe_completed:
        return "completed", f"Q-E project-end filing with Form 4 submitted (status {qe_status})"
    if completion_cert:
        return "completed", "completion certificate document uploaded on project record"
    if current_completion:
        if current_completion < run_date:
            return "overdue", (f"latest approved completion date {current_completion} is before {run_date} "
                               f"and no project-end (Q-E/Form 4) filing found")
        if extension_count and extension_count > 0:
            return "extended", f"{extension_count} extension(s); within latest approved completion date {current_completion}"
        return "ongoing", f"within original completion date {current_completion}"
    return "unknown", "no completion date or completion filing available"
