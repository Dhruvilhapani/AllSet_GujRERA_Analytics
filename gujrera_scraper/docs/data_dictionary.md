# Data dictionary

Database: `data/gujrera.db` (SQLite). Full DDL with column comments: `database/schema.sql`.
Exports: `exports/csv/*.csv`, `exports/excel/gujrera_dataset.xlsx`, `exports/reports/*`.

## Missing-value conventions

| Value | Meaning |
|---|---|
| a number, including `0` | reported by the source (an explicit zero is a reported zero) |
| empty / NULL | the source does not provide it for this entity |
| `*_state = UNKNOWN` | a source exists but could not be interpreted |
| `*_state = NOT_APPLICABLE` | the field does not apply |
| `value_state = DERIVED` (provenance) | computed by this pipeline by the rule documented here |

Counts of complaints / actions / QPR defaults are **NULL for projects whose detail was not crawled**
(`detail_crawled = 0`), never 0.

## Provenance

* `raw_responses` keeps every API response (gzip) with URL, SHA-256, first/last fetch and change time.
* `field_provenance` (entity, field) → value, value_state, source, source_url, source_document, extracted_at,
  last_verified_at, confidence — for every analytically important project field.
* Every child row has `source_endpoint`.
* `field_conflicts` stores both values when two official sources disagree (`value_1/source_1`, `value_2/source_2`,
  `conflict_status`, `interpreted_value`, `rule`). Sources are never overwritten silently.

## Core tables

| Table | Grain | Key | Main source |
|---|---|---|---|
| `projects` | project | `project_id` (= GujRERA projectRegId) | list + core + details + QPR endpoints |
| `project_locations` | project | `project_id` | details, map polygon, FORM C remarks (TP / FP fill-ins) |
| `developers` | GujRERA promoter account | `developer_id` (= promoterId) | promoter master |
| `developer_aliases` | developer × name × source | — | list, core, promoter master |
| `developer_groups` | suggested grouping | (group, developer, basis) | PAN hash / CIN (confirmed), declared group / identical normalized name (review) |
| `project_developers` | project × developer × role | — | core.promoterId |
| `project_professionals` | project × role × name | — | details (architect, engineer, contractor, CA, agent) |
| `project_blocks` | project × block | (project, block_name) | block chart (booked/unbooked), QPR header (planned), block progress, Form 2 |
| `project_units` | project × declared unit type | (project, unit_type_raw) | details.dev.internalDev |
| `project_progress` | project × reported quarter | (project, seq) | quarter/get-project-progress |
| `project_quarters` | project × QPR slot | quarter_id | quarter/public/getprojectqtrs |
| `project_extensions` | project × extension | (project, extension_seq) | applications + reg-no suffix + FORM F |
| `project_applications` | project × application | (project, app_reg_id, type) | applications (verbatim) |
| `annual_compliance` | project × FY | (project, FY) | Form 5 list |
| `complaints` | complainant-initiated matter | acknowledgement no. | applications + judgement lists |
| `regulatory_actions` | suo-motu proceeding / challan | ack. no. / challan no. | applications + judgement lists |
| `appeals` | appeal / court order | forum + case | Appellate Tribunal CMS list, court lists |
| `project_documents` | document | vdms uid | all endpoints exposing uids; downloads |
| `project_status_history` | change event | — | parse-time comparison of tracked fields |
| `project_scrape_history` | project × crawl run | — | crawl queue |
| `project_metrics`, `developer_metrics` | analytics output | — | `cli.py analyze` |

## Key project fields and how they are derived

| Field | Definition / rule | Source |
|---|---|---|
| `reg_no_current` | current registration number | core.projRegNo (list.regNo cross-checked) |
| `reg_no_original` | registration number before any extension | the number itself when it has no EX marker; else FORM C or FORM F "previous registration number" |
| `reg_serial` | stable serial inside the reg. no. (`RAA00362`) | parsed |
| `project_category` | `residential` for "Residential/Group Housing", `mixed` for "Mixed Development" | list.projectType |
| `registration_date` | registration approval date | list.approvedOn (core.approvedDate) — FORM C issue date recorded as conflict when different |
| `original_completion_date` | completion date at registration | list.projOrgFDate (cross-checked with QPR header `endDateAtTimeOfRegistration` and FORM C "ending with") |
| `current_completion_date` | latest approved completion date | details.completionDate → list.endDate → QPR header → reg-no valid-till (first available); differences → `field_conflicts` |
| `extension_count` | approved extensions | max(approved "Extension" applications, EXn marker in reg. no.) — both differ for early offline extensions |
| `alteration_count` | number of `An`/`AnM` markers in the reg. no. | parsed |
| `completion_filing_date/status` | Q-E (project end) filing submission date / status | quarter/get-by-project-id |
| `status_source` | portal status (New / Ongoing / Completed) | list.project_status |
| `status_derived` | see rules below | derived |
| `portal_progress_label`, `portal_*_score` | GujRERA's own progress classification and architect/engineer scores | progress report endpoint (published as-is, not reinterpreted) |
| `latest_progress_pct` | physical progress of the latest reported quarter | QPR progress history |
| `construction_complete_per_qpr` | 1 if latest QPR progress ≥ 100 | derived |
| `financial_progress_pct` | Σ cost incurred ÷ Σ estimated cost × 100 (Form 2A blocks + 2B) | latest engineer certificate (Form 2) |
| `total_units` | total units | unit summary `totunit` (fallback: Σ booked+unbooked) |
| `booked_units` / `unbooked_units` | Σ over blocks | block booking chart |
| `residential_units` / `commercial_units` / `other_units` | Σ declared inventory by keyword class of `typeOfInventory` (`mixed_unspecified` lines count as other) | details |
| `months_delayed_declared` | `noOfMonthDelayed` as published | details |

### `status_derived` (first matching rule wins)

1. `cancelled_revoked` — an application row explicitly says revoked / de-registered / lapsed.
2. `completed` — portal status is Completed, **or** a Q-E filing with Form 4 (architect's completion certificate) has
   status SUBMIT* / APPROV* / COMPLET*, **or** a completion-certificate document is attached to the project record.
3. `unknown` — project detail / Q-E data not crawled.
4. `overdue` — run date after `current_completion_date` and none of the above.
5. `extended` — ≥ 1 approved extension and still within `current_completion_date`.
6. `ongoing` — within the original completion date, no extension.
7. `unknown` — no completion date at all.

Registration expiry alone never marks a project completed.

## Metrics (`project_metrics`, `projects_master.csv`)

| Metric | Formula | Source |
|---|---|---|
| `sales_pct` | booked ÷ (booked + unbooked) × 100; NULL when either is NULL or total = 0 | block chart |
| `unsold_pct` | 100 − sales_pct | block chart |
| `completion_pct` | min(latest_progress_pct, 100) | QPR progress |
| `previous_progress_pct` | progress of the previous reported quarter | QPR progress |
| `progress_velocity_pp_per_qtr` | (latest − value 3 reports earlier) ÷ number of intervals (≤ 3) | QPR progress |
| `months_since_last_qpr` | months from last QPR submission (or last reported period) to run date | QPR calendar |
| `stalled_flag` | not completed AND (gain < 2 pp over the last 3 reported quarters OR no QPR for ≥ 9 months) — parameters in `config/settings.yaml` | derived |
| `days_beyond_original` / `days_beyond_current` | days past the original / latest approved completion date, measured at the Q-E filing date for completed projects and at run date otherwise; 0 if not late | derived |
| `total_extension_days` | current_completion_date − original_completion_date (≥ 0) | derived |
| `timeline_elapsed_pct` | (run date − start) ÷ (current completion − start) × 100, capped 0–100 | derived |
| `complaints_total / pending / resolved` | linked complainant matters; pending = live status not matching CLOSED/DISPOSED/DISMISSED/WITHDRAWN/…; a published *Final* order = resolved, *Interim* = pending, otherwise unknown | applications + judgement lists |
| `suo_motu_total` | linked suo-motu proceedings | applications + judgement lists |
| `challans_total`, `challan_amount_total` | challans levied on the project and their amounts | applications |
| `qpr_defaults` | QPR slots carrying a portal defaulter code (`D-n`) | QPR calendar |

### Developer metrics (`developers_master.csv`)

Portfolio counts per `status_derived`; `completion_rate_pct` = completed ÷ total × 100;
`avg/median_completion_pct` over projects with QPR progress; `units_weighted_completion_pct` = Σ(progress × units) ÷ Σ units;
progress buckets (<25, 25–50, 50–75, ≥75); inventory totals and sold/unsold %; `projects_past_promised_completion`
(days_beyond_current > 0), average / maximum delay; extension totals, `projects_with_repeated_extensions` (≥ 2);
complaints total / pending / resolved, `complaint_to_project_ratio` (per crawled project), projects with
complaints / authority actions / penalties; challans and amounts; QPR defaults; analytical risk score.

## Complaint and action classification (no legal inference)

Category is taken only from the source's own form type or number prefix:

| Source code | Category |
|---|---|
| FORM_A, OFF_COMPLAINT_A, `CMP/A/…`, `A/n/yyyy` | `rera_complaint` (Section 31, Authority) |
| FORM_B, OFF_COMPLAINT_B, `CMP/B/…` | `adjudication_complaint` (Section 31 r/w 71, Adjudicating Officer) |
| REVIEW_*, OFF_REVIEW_* | `review` |
| RATIFICATION_* | `ratification` |
| CONCILIATION_FORUM | `conciliation` |
| SOUMOTO, NCQR, NCQER, NCAR, `SMC/<code>/…`, `GJ/CMP/<code>/…`, `NCQR-n/yyyy` | `suo_motu_proceeding` (regulatory_actions) |
| Challan application rows | `penalty_challan` (regulatory_actions) |
| Appellate Tribunal CMS list | `appeals.forum = appellate_tribunal` |

Sub-type labels for NCQR / NCQER / NCAR / AGREEMENTSALE / ADVERTISEMENT / SELL are expansions **inferred** from
the codes and are marked "(inferred)". `outcome_text` is the published remark, verbatim.

## Privacy

* Complainant names are never stored — only a salted hash (`complainant_hash`) to count repeat complainants.
* Promoter PAN is never stored — only a salted hash (`pan_hash`) used to detect accounts of the same legal person.
* Individual promoters: no address, e-mail, phone or registration number stored.
* Associates (directors/partners): names only.
* Bank-account endpoints are not crawled.
* Salt: `config/settings.yaml → privacy.hash_salt` or environment variable `GUJRERA_HASH_SALT`.

## Documents

`download_priority = critical` for registration / extension certificates, Q-E Form 4, completion certificates,
complaint & suo-motu orders and Appellate Tribunal orders; everything else is `metadata_only` (uid, type,
source endpoint, `download_url`). For downloaded files: `local_path`, `size_bytes`, `sha256`, `text_status`
(`extracted` | `ocr` | `ocr_needed` | `failed`), `text_path`, `parsed_fields` (JSON for certificates).
