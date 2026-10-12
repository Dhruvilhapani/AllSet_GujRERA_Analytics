"""QPR (quarterly progress report) parsing: progress history, filing calendar, project-end (Q-E) filing,
engineer certificate (Form 2) financial progress."""
from __future__ import annotations

from dataclasses import dataclass, field

from parser.common import clean_str, parse_date, parse_month_label, to_float, to_int

QE_DONE_STATUSES = ("SUBMIT", "APPROV", "COMPLET")


@dataclass
class QprResult:
    rows: dict[str, list[dict]] = field(default_factory=dict)
    latest_progress: float | None = None
    latest_quarter: str | None = None
    latest_date: str | None = None
    construction_complete: int | None = None
    qe_status: str | None = None
    qe_submitted_on: str | None = None
    qe_completed: bool = False
    financial_progress: float | None = None
    last_submitted: str | None = None


def _payload(raw: dict, key: str):
    v = raw.get(key)
    if not v or v[0] is None:
        return None
    return v[0]


def parse_qpr(project_id: int, raw: dict, run_date: str) -> QprResult:
    res = QprResult()

    # --- physical progress history
    prog = _payload(raw, "qpr_progress")
    items = prog.get("data") if isinstance(prog, dict) else None
    hist = []
    for seq, it in enumerate(items if isinstance(items, list) else [], start=1):
        label = clean_str(it.get("qtr_name"))
        pct = to_float(it.get("project_progress"))
        if label is None and pct is None:
            continue
        row = {"project_id": project_id, "seq": seq, "quarter_label": label or f"#{seq}",
               "period_date": parse_month_label(label), "physical_progress_pct": pct,
               "source_endpoint": "qpr_progress"}
        hist.append(row)
    res.rows["project_progress"] = hist
    dated = [h for h in hist if h["physical_progress_pct"] is not None]
    if dated:
        # latest by reporting period when parseable, else by published order
        latest = max(dated, key=lambda h: (h["period_date"] or "", h["seq"]))
        res.latest_progress = round(latest["physical_progress_pct"], 2)
        res.latest_quarter = latest["quarter_label"]
        res.latest_date = latest["period_date"]
        res.construction_complete = int(latest["physical_progress_pct"] >= 100)

    # --- quarter filing calendar
    qq = _payload(raw, "qpr_quarters")
    qitems = qq.get("data") if isinstance(qq, dict) else None
    quarters = []
    for it in qitems if isinstance(qitems, list) else []:
        qid = to_int(it.get("qusrterId"))
        if qid is None:
            continue
        quarters.append({
            "quarter_id": qid, "project_id": project_id,
            "quarter_name": clean_str(it.get("quarterName")), "quarter_index": to_int(it.get("qtrIndex")),
            "period_start": parse_date(it.get("startDate")), "period_end": parse_date(it.get("endDate")),
            "due_date": parse_date(it.get("extendedDate")), "submitted_on": parse_date(it.get("submittedOn")),
            "status": clean_str(it.get("status")), "defaulter_code": clean_str(it.get("defauterId")),
            "penalty_amount": to_float(it.get("penaltyAmt")), "auth_remarks": clean_str(it.get("authRemarks")),
            "reg_no_at_time": clean_str(it.get("projectRegNo")), "source_endpoint": "qpr_quarters"})
    res.rows["project_quarters"] = quarters
    subs = [q["submitted_on"] for q in quarters if q["submitted_on"]]
    res.last_submitted = max(subs) if subs else None

    # --- Q-E (project end) filing
    qe = _payload(raw, "qe_filing")
    qd = qe.get("data") if isinstance(qe, dict) else None
    if isinstance(qd, dict) and qd:
        res.qe_status = clean_str(qd.get("status"))
        res.qe_submitted_on = parse_date(qd.get("submitted_on"))
        has_form4 = bool(clean_str(qd.get("qt_form_four_a_docid")) or clean_str(qd.get("qt_form_four_a_docuid")))
        st = (res.qe_status or "").upper()
        res.qe_completed = has_form4 and any(s in st for s in QE_DONE_STATUSES)

    # --- Form 2 (engineer certificate): financial progress = cost incurred / estimated cost
    f2 = _payload(raw, "form_two")
    if isinstance(f2, dict):
        parts = list(f2.get("formTwoAList") or [])
        if isinstance(f2.get("formTwoB"), dict):
            parts.append(f2["formTwoB"])
        est = sum(to_float(p.get("estimatedCost")) or 0 for p in parts)
        inc = sum(to_float(p.get("costInncurred")) or 0 for p in parts)
        if est > 0:
            res.financial_progress = round(min(inc / est * 100, 999), 2)
    return res
