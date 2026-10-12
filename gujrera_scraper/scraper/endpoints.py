"""Registry of the public GujRERA endpoints used by the pipeline.

All endpoints were discovered by observing the official web application's own network
traffic (see docs/gujrera_architecture.md). They are unauthenticated GET requests.

Placeholders:
  {id}          GujRERA projectRegId (== wfoId)
  {promoterId}  GujRERA promoter account id (from alldatabyprojectid)
  {formOneId}   / {formTwoId}  ids of the latest architect / engineer certificates
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Endpoint:
    key: str
    path: str
    entity_type: str
    family: str
    priority: int          # lower = earlier
    depends_on: str | None = None   # placeholder that must be resolved from 'core' first
    description: str = ""


UNIVERSE = {
    "dashboard_counts": Endpoint("dashboard_counts", "dashboard/get-district-wise-projects/0/0/all/Gujarat",
                                 "universe", "listing", 0, description="Counts by project type x district"),
    "list_residential": Endpoint("list_residential",
                                 "dashboard/get-district-wise-projectlist/0/0/all/all/Residential",
                                 "universe", "listing", 0, description="Full list of Residential projects"),
    "list_mixed": Endpoint("list_mixed", "dashboard/get-district-wise-projectlist/0/0/all/all/Mixed",
                           "universe", "listing", 0, description="Full list of Mixed Development projects"),
}

PROJECT_ENDPOINTS: list[Endpoint] = [
    Endpoint("core", "project_reg/public/alldatabyprojectid/{id}", "project", "project_detail", 10,
             description="Promoter id, form ids, certificate uids, approval date"),
    Endpoint("details", "project_reg/public/getproject-details/{id}", "project", "project_detail", 11,
             description="Project detail, land/area, inventory types, professionals"),
    Endpoint("applications", "project_reg/public/projectAllApplications/{id}", "project", "extensions", 12,
             description="Registration / extension / annual compliance / complaint / challan applications"),
    Endpoint("qpr_details", "project_reg/public/project-app/get-project-details-for-qpr/{id}", "project",
             "qpr", 13, description="End date at registration vs per extension, block inventory counts"),
    Endpoint("progress_report", "project_reg/public/getProjectProgressReports-ByPrjRegId/{id}", "project",
             "qpr", 14, description="Portal progress label and scores"),
    Endpoint("qpr_progress", "quarter/get-project-progress/{id}", "project", "qpr", 15,
             description="Physical progress % per reported quarter"),
    Endpoint("qpr_quarters", "quarter/public/getprojectqtrs/{id}", "project", "qpr", 16,
             description="Quarter filing calendar, submission dates, defaulter flags"),
    Endpoint("qe_filing", "quarter/get-by-project-id/{id}", "project", "qpr", 17,
             description="Q-E (project end) filing: Form 4 / Form 5 / society registration"),
    Endpoint("block_chart", "dashboard/get-all-block-chart-details-by-id/{id}", "project", "qpr", 18,
             description="Booked / unbooked units per block"),
    Endpoint("unit_summary", "dashboard/get-all-view-unit-details-by-id/{id}", "project", "qpr", 19,
             description="Total units, carpet-area and cost ranges"),
    Endpoint("form_five", "form_five/get-formfive-records-till-date/{id}", "project", "documents", 20,
             description="Annual audit (Form 5) status per financial year"),
    Endpoint("documents", "project_reg/public/getproject-doc/{id}", "project", "documents", 21,
             description="Project document uids"),
    Endpoint("location", "maplocation/public/getProjectLocations/{id}", "project", "project_detail", 22,
             description="Project polygon / coordinates"),
    Endpoint("block_progress", "dashboard//project-block-progress-dtl-by-prj-id/{formOneId}", "project",
             "qpr", 23, depends_on="formOneId", description="Per-block progress from latest Form 1"),
    Endpoint("form_two", "formtwo/public/getform-two-by-id/{formTwoId}", "project", "qpr", 24,
             depends_on="formTwoId", description="Latest engineer certificate: work done %, cost incurred"),
]
PROJECT_ENDPOINTS_BY_KEY = {e.key: e for e in PROJECT_ENDPOINTS}

PROMOTER_ENDPOINT = Endpoint("promoter", "user_reg/promoter/promoter{promoterId}", "promoter", "promoter", 30,
                             description="Promoter master record")

# Complaint / order lists ----------------------------------------------------
COMPLAINT_SUMMARY = Endpoint("complaint_summary",
                             "complain/ecourt/public/grievanceRedressalSummary/GUJARAT/null",
                             "complaint_list", "complaints", 40)
COMPLAINT_LIST_BY_DISTRICT = "complain/ecourt/public/getcomplaints-by-distcode-formtype/{district}/{form_type}/null"
COMPLAINT_LIST_OTHERS = "complain/ecourt/public/complaints-othersdist-byformType/{form_type}/null"
COURT_LISTS = {
    "court_at": "complain/user/get-court-details-by-type/AT",
    "court_hc": "complain/user/get-court-details-by-type/HC",
    "court_sc": "complain/user/get-court-details-by-type/SC",
}
APPELLATE_TRIBUNAL = "rera-cms/public/master/staticdeployment/wrapper-type/APPELLATE_TRIBUNAL"

# Documents --------------------------------------------------------------------
DOC_METADATA = "vdms/getDocMetadata/{uid}"
DOC_DOWNLOAD = "vdms/download/{uid}"
