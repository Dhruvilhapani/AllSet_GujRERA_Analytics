"""Promoter master parsing.

Privacy rules (see docs/data_dictionary.md#privacy):
  * PAN is never stored; only a salted hash is kept, used to detect promoter accounts of the same legal person.
  * For INDIVIDUAL promoters no personal contact data (address / email / phone / registration no.) is stored.
  * Associates (directors / partners): names only — never PAN, phone, e-mail, photographs.
"""
from __future__ import annotations

import json
import re

from parser.common import clean_str, normalize_name, parse_date, salted_hash, to_int


PAN_RX = re.compile(r"[A-Z]{5}\d{4}[A-Z]")
# Only these formats identify a legal entity unambiguously. Free-text firm registration numbers and placeholders
# ("NOTAPPLICABLE", "0", "GUJSRT17") are kept for display but never used to merge promoters.
CIN_RX = re.compile(r"[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}")
LLPIN_RX = re.compile(r"[A-Z]{3}-\d{4}")


def is_individual(promoter_type: str | None) -> bool:
    return "individual" in (promoter_type or "").lower()


def valid_pan(pan) -> str | None:
    p = (clean_str(pan) or "").upper().replace(" ", "")
    return p if PAN_RX.fullmatch(p) and len(set(p[:5])) > 1 else None


def valid_entity_reg_no(v) -> str | None:
    s = (clean_str(v) or "").upper().replace(" ", "")
    return s if CIN_RX.fullmatch(s) or LLPIN_RX.fullmatch(s) else None


def parse_promoter(j: dict, salt: str) -> dict | None:
    if not isinstance(j, dict) or j.get("id") is None:
        return None
    ptype = clean_str(j.get("promoterType")) or clean_str(j.get("entities_promoterEnterprise"))
    name = clean_str(j.get("promoterName")) or " ".join(
        filter(None, [clean_str(j.get("promoterFirstName")), clean_str(j.get("middleName")),
                      clean_str(j.get("lastName"))])) or None
    indiv = is_individual(ptype)
    assoc = []
    for a in j.get("assosiateList") or []:
        if (clean_str(a.get("status")) or "CONFIRMED").upper() not in ("CONFIRMED", "ACTIVE"):
            continue
        nm = " ".join(filter(None, [clean_str(a.get("associateFirstName")), clean_str(a.get("associateMiddleName")),
                                    clean_str(a.get("associateLastName"))]))
        if nm:
            assoc.append(nm)
    address = ", ".join(filter(None, [clean_str(j.get("address")), clean_str(j.get("address2"))])) or None
    return {
        "developer_id": int(j["id"]),
        "canonical_name": name or f"PROMOTER {j['id']}",
        "legal_name": name,
        "name_normalized": normalize_name(name),
        "entity_type": ptype,
        "registration_status": clean_str(j.get("status")),
        "company_reg_number": None if indiv else (clean_str(j.get("companyRegistrationNumber")) or "").upper() or None,
        "pan_hash": salted_hash(valid_pan(j.get("panNo")), salt),
        "date_of_incorporation": parse_date(j.get("dateOfIncorporation")),
        "registered_address": None if indiv else address,
        "city": clean_str(j.get("talukaName")) or clean_str(j.get("entities_talukaName")),
        "district": clean_str(j.get("districtName")) or clean_str(j.get("entities_districtName")),
        "state": clean_str(j.get("stateName")),
        "pincode": None if indiv else clean_str(j.get("pinCode")),
        "email": None if indiv else clean_str(j.get("emailId")),
        "phone": None if indiv else clean_str(j.get("mobileNo")),
        "website": clean_str(j.get("entities_websiteUrl")),
        "declared_group_name": clean_str(j.get("entities_developerGroupName")),
        "declared_total_projects": to_int(j.get("entities_totalProjects")),
        "declared_completed_projects": to_int(j.get("entities_noOfProjectsCompleted")),
        "declared_ongoing_projects": to_int(j.get("entities_ongoingProjects")),
        "declared_experience_years": clean_str(j.get("entities_experienceInState")),
        "associates_count": len(assoc),
        "associate_names": json.dumps(assoc, ensure_ascii=False) if assoc else None,
        "source_endpoint": "promoter",
    }
