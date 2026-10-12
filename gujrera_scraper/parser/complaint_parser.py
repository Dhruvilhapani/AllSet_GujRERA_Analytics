"""Complaints, suo-motu proceedings, penalties and appeals.

Classification uses only the source's own codes (form type, acknowledgement-number prefix, application type).
Free-text order remarks are stored verbatim and never interpreted into legal conclusions.

Sources
  A. Project application list (per project): every complaint / suo-motu matter filed against the project with
     its live status (incl. pending matters without an order) and challans (penalty payments).
  B. Published judgement lists (per form type x district): orders with date, outcome text, penalty flag/amount,
     order PDF uid. Joined to A on the acknowledgement / application number.
  C. Appellate Tribunal orders (CMS list): free-text parties only -> linked to projects by name, low confidence.
"""
from __future__ import annotations

import re

from parser.common import clean_str, parse_date, reg_serial, salted_hash, to_float

FORM_CATEGORY = {
    "FORM_A": ("complaint", "rera_complaint"),                 # Sec 31 complaint to the Authority (online)
    "OFF_COMPLAINT_A": ("complaint", "rera_complaint"),        # Sec 31 complaint to the Authority (offline)
    "FORM_B": ("complaint", "adjudication_complaint"),         # Sec 31 r/w 71 complaint to Adjudicating Officer
    "OFF_COMPLAINT_B": ("complaint", "adjudication_complaint"),
    "REVIEW_FORM_A": ("complaint", "review"), "REVIEW_FORM_B": ("complaint", "review"),
    "OFF_REVIEW_FORM_A": ("complaint", "review"), "OFF_REVIEW_FORM_B": ("complaint", "review"),
    "OFF_REVIEW_OFF_COMPLAINT_A": ("complaint", "review"),
    "RATIFICATION_FORM_A": ("complaint", "ratification"), "RATIFICATION_FORM_B": ("complaint", "ratification"),
    "CONCILIATION_FORUM": ("complaint", "conciliation"),
    "SOUMOTO": ("action", "suo_motu_proceeding"),
    "NCQR": ("action", "suo_motu_proceeding"),
    "NCQER": ("action", "suo_motu_proceeding"),
    "NCAR": ("action", "suo_motu_proceeding"),
}

# Labels for suo-motu sub-type codes. The codes are published by GujRERA; the expansions below are
# inferred from the code names and the related compliance obligations (marked as such in the dictionary).
SUO_MOTU_LABELS = {
    "NCQR": "Non-compliance: quarterly progress report (inferred)",
    "NCQER": "Non-compliance: project-end (Q-E) report (inferred)",
    "NCAR": "Non-compliance: annual audit report / Form 5 (inferred)",
    "AGREEMENTSALE": "Agreement for sale related (inferred)",
    "ADVERTISEMENT": "Advertisement related (inferred)",
    "SELL": "Sale related (inferred)",
    "SM": "Suo-motu (general)",
    "OTHERS": "Other",
}


def classify_ack(ack: str | None) -> tuple[str, str, str | None]:
    """(kind, category, subtype) from an acknowledgement / application number prefix."""
    a = (clean_str(ack) or "").upper()
    if a.startswith("SMC/"):
        parts = a.split("/")
        sub = parts[1] if len(parts) > 1 else None
        return "action", "suo_motu_proceeding", sub
    m = re.match(r"^GJ/CMP/([A-Z]+)/", a)              # older suo-motu format: GJ/CMP/NCQR/Ahmedabad/220209/0002
    if m and m.group(1) in SUO_MOTU_LABELS:
        return "action", "suo_motu_proceeding", m.group(1)
    m = re.match(r"^(NCQER|NCQR|NCAR|SM)[-/]\d", a)    # register numbers: NCQR-15/2018
    if m:
        return "action", "suo_motu_proceeding", m.group(1)
    if a.startswith("CMP/A/"):
        return "complaint", "rera_complaint", None
    if a.startswith("CMP/B/"):
        return "complaint", "adjudication_complaint", None
    if re.match(r"^[AB]/\d+/\d{4}$", a):         # e.g. A/881/2026 (offline register numbers)
        return "complaint", "rera_complaint" if a.startswith("A/") else "adjudication_complaint", None
    if a.startswith("CMP/"):
        return "complaint", "complaint_unspecified_form", None
    return "complaint", "unknown", None


def from_applications(project_id: int, apps: list, now: str) -> tuple[list[dict], list[dict]]:
    complaints, actions = [], []
    for a in apps or []:
        t = (clean_str(a.get("applicationType")) or "").lower()
        num = clean_str(a.get("applicationNumber"))
        if t == "complaint" and num:
            kind, cat, sub = classify_ack(num)
            base = {"project_id": project_id, "project_link_method": "project_applications",
                    "filed_date": parse_date(a.get("submissionDate")),
                    "status": clean_str(a.get("complaintStatus")), "status_source": "applications",
                    "source_endpoint": "applications", "first_seen_at": now, "last_seen_at": now}
            if kind == "action":
                actions.append({"action_key": num, "action_category": cat, "action_subtype": sub,
                                "action_subtype_label": SUO_MOTU_LABELS.get(sub or "", sub),
                                "related_complaint_number": clean_str(a.get("complaintNumber")),
                                "initiated_date": base.pop("filed_date"), **base})
            else:
                complaints.append({"complaint_key": num, "complaint_number": clean_str(a.get("complaintNumber")),
                                   "category": cat, **base})
        elif t == "challan":
            key = clean_str(a.get("challanNumber")) or num
            if not key:
                continue
            actions.append({
                "action_key": key, "action_category": "penalty_challan", "action_subtype": None,
                "action_subtype_label": "Challan (amount levied & payment) linked to complaint/proceeding",
                "project_id": project_id, "project_link_method": "project_applications",
                "related_complaint_number": clean_str(a.get("complaintNumber")),
                "initiated_date": parse_date(a.get("challanAssignDate")) or parse_date(a.get("submissionDate")),
                "status": clean_str(a.get("challanStatus")) or clean_str(a.get("finalStatus")),
                "penalty_flag": "YES", "penalty_amount": to_float(a.get("challanAmount")),
                "payment_status": clean_str(a.get("challanStatus")),
                "payment_date": parse_date(a.get("challanPaymentDate")),
                "source_endpoint": "applications", "first_seen_at": now, "last_seen_at": now})
    return complaints, actions


def from_judgment_rows(rows: list, endpoint_id: str, serial_to_project: dict[str, int], salt: str,
                       now: str) -> tuple[list[dict], list[dict], list[dict]]:
    """Returns (complaints, actions, documents)."""
    complaints, actions, docs = [], [], []
    for r in rows or []:
        ack = clean_str(r.get("aknowledgement_no")) or (f"COMP_ID:{r.get('comp_id')}" if r.get("comp_id") else None)
        if not ack:
            continue
        form = clean_str(r.get("formtype")) or endpoint_id.split("|")[0]
        kind, cat = FORM_CATEGORY.get(form, (None, None))
        k2, c2, sub = classify_ack(ack)
        if kind is None:
            kind, cat = k2, c2
        list_form = endpoint_id.split("|")[0]
        if sub is None and list_form in ("NCQR", "NCQER", "NCAR"):
            sub = list_form
        regno = clean_str(r.get("proj_reg_no"))
        serial = reg_serial(regno)
        pid = serial_to_project.get(serial) if serial else None
        link = "reg_serial" if pid else ("serial_not_in_scope" if serial else "unlinked")
        doc_uid = clean_str(r.get("doc_uid"))
        common = {
            "project_id": pid, "project_link_method": link, "proj_reg_no_source": regno,
            "district": clean_str(r.get("project_distric")),
            "respondent_name": clean_str(r.get("res_respondent_fname")),
            "order_date": parse_date(r.get("judg_pub_date")),
            "outcome_text": clean_str(r.get("remarks")) or clean_str(r.get("metaData")),
            "penalty_flag": clean_str(r.get("penalty")), "penalty_amount": to_float(r.get("penaltyamt")),
            "order_doc_uid": doc_uid, "source_endpoint": f"complaint_list:{endpoint_id}",
            "first_seen_at": now, "last_seen_at": now,
        }
        if kind == "action":
            actions.append({"action_key": ack, "action_category": cat, "action_subtype": sub,
                            "action_subtype_label": SUO_MOTU_LABELS.get((sub or "").upper(), sub),
                            "initiated_date": parse_date(r.get("created_date")),
                            "status": "order_published", "payment_status": clean_str(r.get("payment_status")),
                            **common})
            dtype = "suo_motu_order"
        else:
            complaints.append({"complaint_key": ack, "comp_id": clean_str(r.get("comp_id")), "category": cat,
                               "form_type": form, "complaint_type_source": clean_str(r.get("comptype")),
                               "project_name_source": clean_str(r.get("projectName")),
                               "complainant_hash": salted_hash(r.get("complaintant_fname"), salt),
                               "filed_date": None, "status": "order_published", "status_source": "judgement_list",
                               "order_type": clean_str(r.get("judge_type")), **common})
            dtype = "complaint_order"
        if doc_uid:
            docs.append({"document_uid": doc_uid, "document_id": None, "project_id": pid, "related_key": ack,
                         "document_type": dtype, "document_name": clean_str(r.get("metaData")),
                         "document_date": parse_date(r.get("judg_pub_date")),
                         "source_endpoint": f"complaint_list:{endpoint_id}",
                         "download_url": f"https://gujrera.gujarat.gov.in/vdms/download/{doc_uid}",
                         "file_type": "pdf", "download_priority": "critical"})
    return complaints, actions, docs


def merge_records(base: dict, new: dict) -> dict:
    """Merge two records for the same key: keep existing non-null values, fill gaps from `new`.
    Status from the project application list (live) takes precedence over 'order_published'."""
    out = dict(base)
    for k, v in new.items():
        if v is None:
            continue
        if out.get(k) is None:
            out[k] = v
        elif k == "status" and out.get("status_source") == "judgement_list" and new.get("status_source") == "applications":
            out[k] = v
            out["status_source"] = "applications"
        elif k == "project_link_method" and out[k] in ("unlinked", "serial_not_in_scope"):
            out[k] = v
        elif k == "source_endpoint" and v not in out[k]:
            out[k] = out[k] + "," + v
    return out


def from_appellate(rows: list, name_to_projects: dict[str, list[int]]) -> tuple[list[dict], list[dict]]:
    appeals, docs = [], []
    for r in rows or []:
        key = clean_str(r.get("aknowledgementNo")) or clean_str(r.get("subject")) or f"AT:{r.get('staticDepId')}"
        resp = clean_str(r.get("respondentName"))
        pid, method, conf = None, "unlinked", None
        if resp:
            cand = _match_project_name(resp, name_to_projects)
            if cand is not None:
                pid, method, conf = cand, "name_match", "low"
        uids = [clean_str(d.get("docUID")) for d in r.get("docLists") or [] if clean_str(d.get("docUID"))]
        appeals.append({"appeal_key": f"AT|{key}", "forum": "appellate_tribunal", "case_number": key,
                        "subject": clean_str(r.get("subject")), "respondent_text": resp,
                        "decision_date": parse_date(r.get("decisionDate")), "project_id": pid,
                        "project_link_method": method, "project_link_confidence": conf,
                        "doc_uids": ",".join(uids) or None, "source_endpoint": "appellate_tribunal"})
        for u in uids:
            docs.append({"document_uid": u, "document_id": None, "project_id": pid, "related_key": f"AT|{key}",
                         "document_type": "appellate_order", "document_name": clean_str(r.get("subject")),
                         "document_date": parse_date(r.get("decisionDate")), "source_endpoint": "appellate_tribunal",
                         "download_url": f"https://gujrera.gujarat.gov.in/vdms/download/{u}", "file_type": "pdf",
                         "download_priority": "critical"})
    return appeals, docs


def from_court_list(rows: list, forum: str) -> list[dict]:
    out = []
    for r in rows or []:
        key = clean_str(r.get("caseNo")) or clean_str(r.get("case_number")) or clean_str(r.get("id"))
        if not key:
            continue
        out.append({"appeal_key": f"{forum}|{key}", "forum": forum, "case_number": key,
                    "subject": clean_str(r.get("subject")), "respondent_text": clean_str(r.get("respondentName")),
                    "decision_date": parse_date(r.get("decisionDate")), "project_id": None,
                    "project_link_method": "unlinked", "project_link_confidence": None, "doc_uids": None,
                    "source_endpoint": f"court_list:{forum}"})
    return out


def _match_project_name(text: str, name_to_projects: dict[str, list[int]]) -> int | None:
    """Only links when exactly one in-scope project name (>= 2 words, >= 8 chars) appears verbatim."""
    t = re.sub(r"[^a-z0-9 ]+", " ", text.lower())
    t = f" {re.sub(r' +', ' ', t)} "
    hits = set()
    for name, pids in name_to_projects.items():
        if len(name) >= 8 and " " in name and f" {name} " in t:
            hits.update(pids)
    return next(iter(hits)) if len(hits) == 1 else None
