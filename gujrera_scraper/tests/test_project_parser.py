from parser.project_parser import ProjectParser, derive_status

RUN_DATE = "2026-10-07"


def parse(pid, raw_for, list_row_for):
    return ProjectParser(list_row_for(pid), raw_for(pid), RUN_DATE, "2026-10-07T00:00:00+00:00").parse()


def test_extended_mixed_project_476(raw_for, list_row_for):
    r = parse(476, raw_for, list_row_for)
    p = r.rows["projects"][0]
    assert p["project_id"] == 476 and p["project_category"] == "mixed"
    assert p["reg_serial"] == "MAA00393"
    assert p["extension_count"] == 3
    assert p["original_completion_date"] == "2023-01-25"
    assert p["current_completion_date"] == "2026-01-25"
    # Q-E filing with Form 4 submitted -> completed, even though portal status says Ongoing
    assert p["status_source"] == "Ongoing" and p["status_derived"] == "completed"
    # inventory: block chart sums (verified manually against the fixture)
    assert p["booked_units"] == 138 and p["unbooked_units"] == 96 and p["total_units"] == 234
    assert p["residential_units"] == 184 and p["commercial_units"] == 50
    exts = r.rows["project_extensions"]
    assert [e["status"] for e in exts] == ["approved"] * 3
    assert exts[1]["extended_completion_date"] == "2025-01-25"           # from EX2/190924/250125
    assert exts[2]["extended_completion_date"] == "2026-01-25"
    assert exts[2]["previous_completion_date"] == "2025-01-25" and exts[2]["extension_days"] == 365
    # complaints / challans are visible in the application rows
    types = {a["application_type"] for a in r.rows["project_applications"]}
    assert {"Registration", "Extension", "Complaint", "Challan"} <= types


def test_completed_by_qe_filing_62(raw_for, list_row_for):
    p = parse(62, raw_for, list_row_for).rows["projects"][0]
    assert p["status_derived"] == "completed"
    assert p["completion_filing_status"] == "SUBMIT_FORM_FOUR_FIVE"
    assert p["latest_progress_pct"] == 94.45
    assert p["booked_units"] == 108 and p["unbooked_units"] == 95


def test_new_project_without_qpr_32294(raw_for, list_row_for):
    r = parse(32294, raw_for, list_row_for)
    p = r.rows["projects"][0]
    assert p["latest_progress_pct"] is None            # no QPR yet -> NULL, not 0
    assert p["status_derived"] == "ongoing"
    assert p["booked_units"] == 0 and p["inventory_state"] == "REPORTED"   # explicit zero stays zero


def test_documents_classified(raw_for, list_row_for):
    docs = parse(476, raw_for, list_row_for).rows["project_documents"]
    by_type = {}
    for d in docs:
        by_type.setdefault(d["document_type"], []).append(d)
    assert "extension_certificate" in by_type
    assert all(d["download_priority"] == "critical" for d in by_type["extension_certificate"])
    assert by_type["qe_form_four"][0]["download_priority"] == "critical"
    assert any(t.startswith("audited_bal_sheet") for t in by_type)
    assert all(d["download_priority"] == "metadata_only" for t, ds in by_type.items()
               if t.startswith("audited") for d in ds)


def test_derive_status_rules():
    base = dict(status_source="Ongoing", qe_completed=False, qe_status=None, completion_cert=False,
                current_completion="2025-01-01", extension_count=0, run_date=RUN_DATE, revoked_evidence=None)
    assert derive_status(**base)[0] == "overdue"
    assert derive_status(**{**base, "current_completion": "2027-01-01"})[0] == "ongoing"
    assert derive_status(**{**base, "current_completion": "2027-01-01", "extension_count": 2})[0] == "extended"
    assert derive_status(**{**base, "qe_completed": True})[0] == "completed"
    assert derive_status(**{**base, "revoked_evidence": "x"})[0] == "cancelled_revoked"
    assert derive_status(**{**base, "current_completion": None})[0] == "unknown"
    assert derive_status(**{**base, "has_detail": False})[0] == "unknown"
