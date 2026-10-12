"""Controlled vocabularies used across tables (single source of truth for docs and validation)."""

VALUE_STATES = {
    "REPORTED": "value published by the source (an explicit 0 is a reported zero)",
    "NULL": "source does not provide the value for this entity",
    "UNKNOWN": "source exists but could not be interpreted",
    "NOT_APPLICABLE": "field does not apply to this entity",
    "DERIVED": "computed by this pipeline from reported values (rule documented in the data dictionary)",
}

STATUS_DERIVED = {
    "completed": "Q-E project-end filing with Form 4 submitted, completion certificate on record, or portal status Completed",
    "ongoing": "no completion filing, within the original approved completion date, no extensions",
    "extended": "no completion filing, >=1 approved extension, within the latest approved completion date",
    "overdue": "no completion filing and the latest approved completion date has passed",
    "cancelled_revoked": "an application/record explicitly indicates revocation / de-registration / lapse",
    "unknown": "insufficient data (e.g. project detail not crawled, or no completion date)",
}

COMPLAINT_CATEGORIES = {
    "rera_complaint": "Complaint to the Authority under Section 31 (Form A, online/offline)",
    "adjudication_complaint": "Complaint to the Adjudicating Officer under Section 31 r/w 71 (Form B)",
    "review": "Review application against an order",
    "ratification": "Ratification application",
    "conciliation": "Conciliation forum matter",
    "complaint_unspecified_form": "Complaint number without form indicator (older numbering)",
    "unknown": "Number format not recognised",
}

ACTION_CATEGORIES = {
    "suo_motu_proceeding": "Proceeding initiated by the Authority on its own motion (SMC/... or GJ/CMP/<code>/...)",
    "penalty_challan": "Challan (penalty amount) levied and its payment status, linked to a complaint/proceeding",
}

APPEAL_FORUMS = {"appellate_tribunal": "Gujarat Real Estate Appellate Tribunal", "high_court": "High Court",
                 "supreme_court": "Supreme Court"}

DOCUMENT_PRIORITY = {"critical": "downloaded + text-extracted + parsed", "metadata_only": "uid + URL recorded only"}
