# GujRERA portal — verified architecture

Everything below was **observed** in the official web application's own network traffic (Chrome, 2026-10-07)
and then reproduced with plain HTTP GET requests. Nothing here is guessed. Endpoints that were not observed
are not used.

## Front end

| Item | Finding |
|---|---|
| Framework | Angular SPA (`<app-root>`, hashed bundles `main-es2015.*.js`, lazy chunks `NN-es2015.*.js`), hash routing (`/#/...`) |
| Base URL | `https://gujrera.gujarat.gov.in/` (API on the same origin; microservices are path prefixes) |
| Auth | **None** for every endpoint used here (public search, "Public Search (No Login)") |
| Captcha | Only on login / forms (`user_reg/public/get-captcha-random-details`); never touched |
| robots.txt | Not present (HTTP 404) |
| TLS | Server requires *legacy renegotiation*. OpenSSL 3 refuses this by default (`UNSAFE_LEGACY_RENEGOTIATION_DISABLED`); the client sets `OP_LEGACY_SERVER_CONNECT`. Certificate verification stays on. |
| Response format | JSON; most wrap results as `{status, message, data}` (status may be a string or a number). Errors may be HTTP 200 with `status: "300"` / `"Request Submit Failed."` |
| Pagination | **None** on the list endpoints — the whole result set comes back in one response (Residential ≈ 8.7 MB) |
| Encoded ids | Projects use a plain integer `projectRegId` (= `wfoId`). Documents use opaque 50–80 char upper/lower alphanumeric `uid`s served by the `vdms` document service. |

## Microservices (path prefixes)

`dashboard/`, `project_reg/`, `quarter/`, `formone/`, `formtwo/`, `formthree/`, `form_five/`, `user_reg/`,
`complain/`, `citizen_survey/`, `maplocation/`, `vdms/` (documents), `rera-cms/` (static content).

## Request flow

```text
Dashboard count grid            dashboard/get-district-wise-projects/0/0/all/Gujarat
    │  (type × district counts; used to verify completeness)
    ▼
Project list (complete)         dashboard/get-district-wise-projectlist/0/0/all/all/{Residential|Mixed}
    │  rows: projectRegId, regNo, status, dates, promoter, district, cost …
    ▼
projectRegId ──► project core   project_reg/public/alldatabyprojectid/{id}
    │               ├─ promoterId ──► promoter master     user_reg/promoter/promoter{promoterId}
    │               ├─ formOneId  ──► block progress      dashboard//project-block-progress-dtl-by-prj-id/{formOneId}
    │               ├─ formTwoId  ──► engineer cert.      formtwo/public/getform-two-by-id/{formTwoId}
    │               └─ certificateUid / extcertificateUid ──► vdms/download/{uid}
    ├─► details              project_reg/public/getproject-details/{id}
    ├─► applications         project_reg/public/projectAllApplications/{id}   (registration, extensions,
    │                            annual compliance, complaints, challans, BCR — with certificate uids)
    ├─► QPR header           project_reg/public/project-app/get-project-details-for-qpr/{id}
    ├─► progress report      project_reg/public/getProjectProgressReports-ByPrjRegId/{id}
    ├─► progress history     quarter/get-project-progress/{id}
    ├─► QPR calendar         quarter/public/getprojectqtrs/{id}
    ├─► Q-E (project end)    quarter/get-by-project-id/{id}      (Form 4 / Form 5 / society reg. doc uids)
    ├─► booking chart        dashboard/get-all-block-chart-details-by-id/{id}
    ├─► unit summary         dashboard/get-all-view-unit-details-by-id/{id}
    ├─► Form 5 list          form_five/get-formfive-records-till-date/{id}
    ├─► documents            project_reg/public/getproject-doc/{id}
    └─► map polygon          maplocation/public/getProjectLocations/{id}   (HTTP 400 when absent)

Documents                       vdms/getDocMetadata/{uid}  ·  vdms/download/{uid}  (PDF; some uploads are JPEG)

Complaints / orders
  summary grid                  complain/ecourt/public/grievanceRedressalSummary/GUJARAT/null
  list per cell (district)      complain/ecourt/public/getcomplaints-by-distcode-formtype/{district}/{formType}/null
  list per cell ("Others")      complain/ecourt/public/complaints-othersdist-byformType/{formType}/null
     formType ∈ summary formTypes (FORM_A, OFF_COMPLAINT_A, FORM_B, …, SOUMOTO)
              ∪ suo-motu non-compliance codes NCQR / NCQER / NCAR (each row of the grid is its own list)
  joined to projects by         proj_reg_no (registration serial, e.g. RAA00362) and acknowledgement number
  Appellate Tribunal orders     rera-cms/public/master/staticdeployment/wrapper-type/APPELLATE_TRIBUNAL
  Court lists                   complain/user/get-court-details-by-type/{AT|HC|SC}   (currently empty)
```

## How the UI reaches a project (no `href` in the list)

The registered-project grid links (`href="javascript:void(0)"`) call the list endpoint; the UI's own list page
passes `null` for the district when opened from the "Total" column and therefore shows 0 rows — passing `all`
returns the full list. Opening a project from the global search (`project_reg/public/global-search`, POST) stores
the `projectRegId` in client state and navigates to `#/project-preview`, which fires the ~40 per-project calls
listed above. Every one of them is keyed by `projectRegId` (or by ids returned by the core record).

## Identifiers

| Identifier | Where | Stability | Use |
|---|---|---|---|
| `projectRegId` (`wfoId`) | list, every project endpoint | stable integer | **primary key** `projects.project_id` |
| registration number `PR/GJ/<DIST>/<TALUKA>/<AUTH>/<SERIAL>/…` | list `regNo`, core `projRegNo` | **changes on every extension** (`/EXn/…`) | stored as `reg_no_current`; serial `RAA00362` (stable) used for joins |
| registration serial `[RMCP]AA\d{5}` | inside the reg. no. | stable | joins complaints → projects |
| `project_ack_no` `PR/<DIST>/<TALUKA>/<AUTH>/<YYMMDD>/<n>` | list, core | stable | stored |
| `promoterId` | core | stable per GujRERA account | `developers.developer_id` |
| document `uid` | many endpoints | stable | `project_documents.document_uid` |
| acknowledgement no. `CMP/A/…`, `SMC/<code>/…`, `GJ/CMP/<code>/…` | applications, judgement lists | stable | `complaints.complaint_key`, `regulatory_actions.action_key` |

### Registration-number grammar (verified against FORM C / FORM F certificates)

```text
PR/GJ/AHMEDABAD/DASKROI/Others/RAA00362/061017                    registered (approved 06-10-2017)
PR/GJ/JAMNAGAR/JAMNAGAR/Others/RAA00742/EX1/240220                extension 1 approved 24-02-2020
PR/GJ/VADODARA/VADODARA/Others/MAA00393/EX2/190924/250125         extension 2 approved 19-09-2024, valid till 25-01-2025
PR/GJ/VADODARA/VADODARA/Others/MAA00393/EX2/EX3/110325/250126     extension 3 approved 11-03-2025, valid till 25-01-2026
PR/GJ/SURAT/CHORYASI/Surat Municipal Corporation/RAA17409/051026/311230   new-format registration, valid till 31-12-2030
PR/GJ/AHMEDABAD/AHMADABAD CITY/Others/MAA00001/A1M/031218         alteration marker A1M
```

Note that district / taluka / authority segments may contain spaces.

## Observed data-quality facts

* The dashboard grid shows the same Ahmedabad count (2,260) for Residential and Mixed; the list endpoint is used
  as ground truth (list totals equal the grid totals: 7,880 + 6,004).
* `project_status` in the list is mostly `New` / `Ongoing`; only a few dozen projects are `Completed`, while the
  project-end (Q-E) filing shows many more completions → completion is derived (see data dictionary).
* `disposed_date` is never populated; no revocation list endpoint was observed.
* Registration certificates of older extensions were re-issued as FORM C carrying the `/EXn/` number.
* Complaint lists are queried per district spelling as shown in the grid (`Surat` and `SURAT` both appear); rows
  are de-duplicated on the acknowledgement number.

## Unit-wise booking endpoints (project page: "Unit Wise Booking Status" / "Block Booking Details")

Observed from the portal's own network traffic and bundle (2026-10-21). All are unauthenticated.

| Call | Purpose |
|---|---|
| `GET quarter/public/get-qtr-form-details/{projectId}` | `{formOneId, formTwoId, formThreeId}` of the **latest** filings |
| `GET citizen_survey/citizen_claim/get-project-all-block{projectId}` | `{blockName: [unit numbers…]}`; drives the block selector, empty for some projects |
| `POST formthree/public/get-inv-details-for-view` body `{"blockName": "A", "formThreeId": 123}` | one row per unit: `id, formThreePk, blockId, blockName, usage, flatNo, carpetArea, areaofExBalcony, status, unitConsideration, receivedAmount, balanceAmount, encumbranceStatus, dateOfAgrrement, alloteeName, typeofKYC, kycId, redeveloped, mobileNumber, createdOn` |
| `GET formthree/public/getform-three-byid/{formThreeId}` | Form 3 header: `submissionOn`, `numberOfUnits`, `bookedUnit`, … (HTTP 500 for some ids) |
| `GET project_reg/project-app/getproject-blocklist/{projectId}` | block master: `blockId`, development dates, height / FSI / built-up area |
| `GET formone/public/getfrom-one-photos-by-name/{formOneId}/{block}` | block photo document ids (not crawled) |

Facts that shape the data model: amounts come back as `******`, allottee fields as null / masked; the balcony and
open-terrace area is a single field; there is no per-unit floor; there is no per-quarter Form 3 id, so only the latest
snapshot is available (booking history cannot be reconstructed from the portal); `getform-three-byid` returns only the
registration-time list of units, while `get-inv-details-for-view` returns the current one. The `Commercial` and
`Plotted` list tokens were verified against the live portal.
