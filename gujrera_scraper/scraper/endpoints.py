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
    depends_on: str | None = None   # placeholder that must be resolved from an earlier response first
    description: str = ""
    method: str = "GET"
    optional: bool = False   # nice-to-have: one quick retry, and a persistent HTTP 500 is recorded as 'empty', not 'failed'


UNIVERSE = {
    "dashboard_counts": Endpoint("dashboard_counts", "dashboard/get-district-wise-projects/0/0/all/Gujarat",
                                 "universe", "listing", 0, description="Counts by project type x district"),
    "list_residential": Endpoint("list_residential",
                                 "dashboard/get-district-wise-projectlist/0/0/all/all/Residential",
                                 "universe", "listing", 0, description="Full list of Residential projects"),
    "list_mixed": Endpoint("list_mixed", "dashboard/get-district-wise-projectlist/0/0/all/all/Mixed",
                           "universe", "listing", 0, description="Full list of Mixed Development projects"),
    "list_commercial": Endpoint("list_commercial",
                                "dashboard/get-district-wise-projectlist/0/0/all/all/Commercial",
                                "universe", "listing", 0, description="Full list of Commercial projects"),
    "list_plotted": Endpoint("list_plotted",
                             "dashboard/get-district-wise-projectlist/0/0/all/all/Plotted",
                             "universe", "listing", 0, description="Full list of Plotted Development projects"),
}

# Portal label (projectType in the list rows / dashboard grid) for every list endpoint.
TYPE_LABEL = {"list_residential": "Residential/Group Housing", "list_mixed": "Mixed Development",
              "list_commercial": "Commercial", "list_plotted": "Plotted Development"}

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
    # ---- unit layer (block-wise / unit-wise booking status) ------------------------------------------------
    Endpoint("qtr_form_ids", "quarter/public/get-qtr-form-details/{id}", "project", "units", 25,
             description="Ids of the latest Form 1 / 2 / 3 (formThreeId feeds the unit list)"),
    Endpoint("block_names", "citizen_survey/citizen_claim/get-project-all-block{id}", "project", "units", 26,
             description="Block names the portal offers in its unit selector (+ unit numbers as free text)"),
    Endpoint("block_master", "project_reg/project-app/getproject-blocklist/{id}", "project", "units", 27,
             description="Block master: blockId, development dates, height / FSI / built-up area"),
    Endpoint("form_three", "formthree/public/getform-three-byid/{formThreeId}", "project", "units", 28,
             depends_on="formThreeId", description="Form 3 header: submission date, declared totals "
                                                   "(answers HTTP 500 for some projects; the unit rows do not need it)",
             optional=True),
    Endpoint("last_submitted", "project_reg/project/last-submitted-date/{id}", "project", "units", 31, optional=True,
             description="Date/time of the project's latest submission recorded on GujRERA (the portal's own "
                         "'process submission date'); answers status 300 / null for some projects"),
    Endpoint("form_one", "formone/public/getfrom-one-byformone-id/{formOneIdLatest}", "project", "units", 30,
             depends_on="formOneIdLatest", optional=True,
             description="Latest Form 1 (architect's certificate): common facilities with applicability, progress %, "
                         "dates; named extra facilities (e.g. CLUB HOUSE); per-block floors and lifts"),
]
PROJECT_ENDPOINTS_BY_KEY = {e.key: e for e in PROJECT_ENDPOINTS}

# Fan-out endpoints: one task per (project, block). They are queued by Crawler.enqueue_unit_lists once the
# block set of a project is known, so they are not part of PROJECT_ENDPOINTS.
UNIT_LIST = Endpoint("unit_list", "formthree/public/get-inv-details-for-view", "project_block", "units", 29,
                     description="One row per unit of a block (latest Form 3): unit no, usage, carpet area, "
                                 "combined exclusive balcony/veranda + open-terrace area, status, agreement date",
                     method="POST")

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
