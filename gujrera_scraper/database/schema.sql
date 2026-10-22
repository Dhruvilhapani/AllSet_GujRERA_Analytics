-- GujRERA Residential + Mixed-use dataset — normalized SQLite schema.
-- Conventions:
--   * Dates are ISO-8601 text (YYYY-MM-DD); timestamps are ISO-8601 UTC text.
--   * NULL means "not available from source". Explicit zeros reported by the source are stored as 0.
--   * Where the difference matters, a companion *_state column holds one of
--     REPORTED | NULL | UNKNOWN | NOT_APPLICABLE  (see docs/data_dictionary.md).
--   * Every parsed row carries source_endpoint (+ raw_response_id) for provenance.
--   * REFERENCES clauses document relationships; enforcement is off because tables are rebuilt from raw
--     data in dependency-independent order. Orphans are reported by the data-quality checks instead.

------------------------------------------------------------------------------
-- Crawl infrastructure
------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw_responses (
    id              INTEGER PRIMARY KEY,
    url             TEXT NOT NULL UNIQUE,
    endpoint_key    TEXT,              -- logical endpoint name (see scraper/endpoints.py)
    entity_type     TEXT,              -- project | promoter | complaint_list | document | universe
    entity_id       TEXT,
    http_status     INTEGER,
    content_type    TEXT,
    size_bytes      INTEGER,
    sha256          TEXT,
    body_gz         BLOB,              -- gzip(JSON body); NULL for binary documents (stored on disk)
    first_fetched_at TEXT NOT NULL,
    fetched_at      TEXT NOT NULL,     -- last fetch
    changed_at      TEXT NOT NULL      -- last time sha256 changed
);
CREATE INDEX IF NOT EXISTS ix_raw_entity ON raw_responses(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS ix_raw_endpoint ON raw_responses(endpoint_key);
-- RawStore.load(endpoint_key, entity_id) is called per project by the parser and the crawler; without this index each
-- call scanned every row of the endpoint (minutes of CPU for a few thousand projects).
CREATE INDEX IF NOT EXISTS ix_raw_endpoint_entity ON raw_responses(endpoint_key, entity_id);

CREATE TABLE IF NOT EXISTS crawl_queue (
    id              INTEGER PRIMARY KEY,
    run_id          TEXT NOT NULL,
    entity_type     TEXT NOT NULL,
    entity_id       TEXT NOT NULL,
    endpoint_key    TEXT NOT NULL,
    path            TEXT NOT NULL,
    priority        INTEGER NOT NULL DEFAULT 50,
    sort_key        INTEGER,                           -- entity order: lists first, then projects in pseudo-random order
    status          TEXT NOT NULL DEFAULT 'pending',   -- pending | done | failed | empty
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    changed         INTEGER,                           -- 1 if the response differed from the last stored copy
    updated_at      TEXT,
    body            TEXT,                              -- JSON body for POST tasks (unit_list); NULL for GET tasks
    UNIQUE (run_id, endpoint_key, entity_id)
);
CREATE INDEX IF NOT EXISTS ix_queue_status ON crawl_queue(run_id, status, priority, id);

CREATE TABLE IF NOT EXISTS crawl_runs (
    run_id          TEXT PRIMARY KEY,
    mode            TEXT NOT NULL,          -- full | incremental | poc | sample
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    universe_count  INTEGER,
    notes           TEXT
);

------------------------------------------------------------------------------
-- Developers / promoters
------------------------------------------------------------------------------
-- developer_id is GujRERA's own promoterId (one GujRERA promoter account).
CREATE TABLE IF NOT EXISTS developers (
    developer_id            INTEGER PRIMARY KEY,
    canonical_name          TEXT NOT NULL,
    legal_name              TEXT,
    name_normalized         TEXT,
    entity_type             TEXT,       -- COMPANY | PARTNERSHIP FIRM | LLP | INDIVIDUAL | ... as published
    registration_status     TEXT,       -- promoter account status as published (e.g. REGISTERED)
    company_reg_number      TEXT,       -- CIN / firm registration number (business identifier) — not stored for individuals
    pan_hash                TEXT,       -- salted SHA-256 of PAN; used only for identity matching, PAN itself is never stored
    date_of_incorporation   TEXT,
    registered_address      TEXT,       -- not stored for individuals
    city                    TEXT,
    district                TEXT,
    state                   TEXT,
    pincode                 TEXT,
    email                   TEXT,       -- business contact as published; NULL for individuals
    phone                   TEXT,       -- business contact as published; NULL for individuals
    website                 TEXT,
    declared_group_name     TEXT,       -- 'developer group' self-declared by the promoter
    declared_total_projects INTEGER,    -- self-declared experience fields (unverified)
    declared_completed_projects INTEGER,
    declared_ongoing_projects INTEGER,
    declared_experience_years TEXT,
    associates_count        INTEGER,
    associate_names         TEXT,       -- directors/partners names as published (JSON list); no PAN/phone/email
    source_endpoint         TEXT,
    raw_response_id         INTEGER,
    first_seen_at           TEXT,
    last_seen_at            TEXT,
    last_changed_at         TEXT,
    last_scraped_at         TEXT
);

CREATE TABLE IF NOT EXISTS developer_aliases (
    developer_id    INTEGER NOT NULL REFERENCES developers(developer_id),
    alias           TEXT NOT NULL,
    alias_normalized TEXT,
    source          TEXT NOT NULL,      -- project_list | promoter_master | qpr | complaint | certificate
    PRIMARY KEY (developer_id, alias, source)
);

-- Suggested groupings of promoter accounts that may be the same legal entity / business group.
-- These are NEVER auto-merged into analytics; group-level analytics only use match_basis='pan_hash'
-- (same PAN = same legal person), everything else is review-only.
CREATE TABLE IF NOT EXISTS developer_groups (
    group_id        TEXT NOT NULL,
    developer_id    INTEGER NOT NULL REFERENCES developers(developer_id),
    match_basis     TEXT NOT NULL,      -- pan_hash | company_reg_number | declared_group_name | name_similarity
    match_score     REAL,
    review_status   TEXT NOT NULL DEFAULT 'auto_confirmed', -- auto_confirmed (pan/cin) | needs_review
    PRIMARY KEY (group_id, developer_id, match_basis)
);

------------------------------------------------------------------------------
-- Projects
------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS projects (
    project_id              INTEGER PRIMARY KEY,   -- GujRERA projectRegId (stable internal ID)
    reg_serial              TEXT,                  -- stable serial inside the reg no, e.g. RAA00362
    reg_no_current          TEXT,                  -- changes on every extension (/EXn/...)
    reg_no_original         TEXT,                  -- reg no as first registered (from applications / certificate)
    ack_no                  TEXT,
    project_name            TEXT,
    project_type            TEXT,                  -- Residential/Group Housing | Mixed Development | Commercial | Plotted Development
    project_category        TEXT,                  -- residential | mixed | commercial | plotted
    affordable_housing      TEXT,
    description             TEXT,
    status_source           TEXT,                  -- project_status as published (New / Ongoing / Completed)
    status_derived          TEXT,                  -- completed | ongoing | extended | overdue | cancelled_revoked | unknown
    status_derived_basis    TEXT,                  -- human-readable evidence for status_derived
    portal_progress_label   TEXT,                  -- GujRERA's own label from progress report endpoint (e.g. On Track)
    portal_progress_score   REAL,
    portal_arch_score       REAL,
    portal_engg_score       REAL,
    portal_time_lapse_pct   REAL,
    registration_date       TEXT,                  -- approval date of registration
    application_date        TEXT,
    project_start_date      TEXT,
    original_completion_date TEXT,                 -- completion date at time of registration
    current_completion_date TEXT,                  -- latest approved completion date (after extensions)
    completion_filing_date  TEXT,                  -- Q-E / Form-4 submission date (project-end filing)
    completion_filing_status TEXT,
    construction_complete_per_qpr INTEGER,         -- 1 if latest QPR progress >= 100
    extension_count         INTEGER,
    alteration_count        INTEGER,               -- An/AnM markers in the registration number (alteration approvals)
    latest_progress_pct     REAL,
    latest_progress_quarter TEXT,
    latest_progress_date    TEXT,
    financial_progress_pct  REAL,                  -- cost incurred / estimated cost (latest Form 2)
    total_units             INTEGER,
    total_units_state       TEXT,
    booked_units            INTEGER,
    unbooked_units          INTEGER,
    inventory_state         TEXT,
    residential_units       INTEGER,
    commercial_units        INTEGER,
    other_units             INTEGER,
    total_blocks            INTEGER,
    parking_for_sale        INTEGER,
    garages_for_sale        INTEGER,
    covered_parking         INTEGER,
    total_land_area_sqm     REAL,
    total_carpet_area_sqm   REAL,
    total_covered_area_sqm  REAL,
    total_open_area_sqm     REAL,
    min_unit_carpet_sqm     REAL,
    max_unit_carpet_sqm     REAL,
    min_unit_cost           REAL,
    max_unit_cost           REAL,
    cost_of_land            REAL,
    estimated_construction_cost REAL,
    total_project_cost      REAL,
    approving_authority     TEXT,
    months_delayed_declared INTEGER,               -- noOfMonthDelayed as published
    last_qpr_submitted_at   TEXT,
    portal_last_submitted_at TEXT,                 -- GujRERA's latest-submission timestamp for the project (a new filing moves it)
    details_updated_on      TEXT,                  -- projectDetail.updatedOn: when the promoter last saved the project details
    engineer_cert_submitted_on TEXT,               -- latest engineer's certificate (Form 2) submission date
    -- unit layer (parser/unit_parser.py). unit_data_state: complete | partial | not_published | error | not_fetched.
    -- Only 'complete' means every expected block returned its unit rows; anything else must never be shown as "unsold".
    unit_data_state         TEXT,
    unit_data_note          TEXT,
    units_scraped_at        TEXT,
    units_row_count         INTEGER,
    units_form3_submitted_on TEXT,                 -- "Submitted On" date of the Form 3 the unit rows come from
    source_endpoint         TEXT,
    list_row_hash           TEXT,                  -- hash of the project's row in the official list (change detection)
    first_seen_at           TEXT,
    last_seen_at            TEXT,
    last_changed_at         TEXT,
    last_scraped_at         TEXT
);
CREATE INDEX IF NOT EXISTS ix_projects_serial ON projects(reg_serial);

CREATE TABLE IF NOT EXISTS project_locations (
    project_id      INTEGER PRIMARY KEY REFERENCES projects(project_id),
    address_line1   TEXT,
    address_line2   TEXT,
    full_address    TEXT,
    district        TEXT,
    district_code   TEXT,
    taluka          TEXT,                  -- sub-district
    village_moje    TEXT,
    city            TEXT,
    pincode         TEXT,
    planning_authority TEXT,
    tp_number       TEXT,
    tp_name         TEXT,
    fp_number       TEXT,
    survey_number   TEXT,
    city_survey_number TEXT,
    block_number    TEXT,
    sub_plot_number TEXT,
    latitude        REAL,
    longitude       REAL,
    polygon_points  INTEGER,
    source_endpoint TEXT
);

CREATE TABLE IF NOT EXISTS project_developers (
    project_id      INTEGER NOT NULL REFERENCES projects(project_id),
    developer_id    INTEGER NOT NULL REFERENCES developers(developer_id),
    role            TEXT NOT NULL DEFAULT 'promoter',
    source_endpoint TEXT,
    PRIMARY KEY (project_id, developer_id, role)
);

-- Professionals (architects / engineers / contractors / CAs / agents) attached to a project.
CREATE TABLE IF NOT EXISTS project_professionals (
    project_id      INTEGER NOT NULL REFERENCES projects(project_id),
    role            TEXT NOT NULL,
    name            TEXT,
    licence_no      TEXT,
    PRIMARY KEY (project_id, role, name)
);

-- One row per block/tower. booked/unbooked from the portal's block chart (QPR-fed),
-- planned counts from registration inventory, progress from the block progress endpoint.
CREATE TABLE IF NOT EXISTS project_blocks (
    project_id      INTEGER NOT NULL REFERENCES projects(project_id),
    block_name      TEXT NOT NULL,
    planned_units   INTEGER,
    booked_units    INTEGER,
    unbooked_units  INTEGER,
    booked_pct_source REAL,
    progress_pct    REAL,
    engineer_progress_pct REAL,
    work_done_pct_form2 REAL,
    estimated_cost_form2 REAL,
    cost_incurred_form2 REAL,
    dev_start_date  TEXT,
    dev_end_date    TEXT,
    source_endpoint TEXT,
    -- block master (project_reg/project-app/getproject-blocklist)
    block_id_src    INTEGER,
    height          REAL,
    fsi             REAL,
    builtup_area_sqm REAL,
    -- unit-row counts of the latest Form 3 (project_unit_inventory); booked_units/unbooked_units above stay the
    -- portal block-chart counts. The two sources can disagree (different submission dates): both are kept.
    unit_rows       INTEGER,
    unit_booked     INTEGER,
    unit_unbooked   INTEGER,
    unit_other      INTEGER,
    -- Form 1 part A (NULL when the architect did not fill them in)
    floors          INTEGER,
    lifts           INTEGER,
    PRIMARY KEY (project_id, block_name)
);

-- Inventory by unit type as declared at registration/QPR.
CREATE TABLE IF NOT EXISTS project_units (
    project_id          INTEGER NOT NULL REFERENCES projects(project_id),
    unit_type_raw       TEXT NOT NULL,
    unit_class          TEXT,              -- residential | commercial | other
    units_planned       INTEGER,
    units_booked_declared INTEGER,         -- as declared in the registration inventory (often 0 / NULL)
    units_completed_declared INTEGER,
    carpet_area_sqm     REAL,
    exclusive_area_sqm  REAL,
    open_terrace_area_sqm REAL,            -- areaOfExclusiveOpenTerrace (per unit type, registration inventory)
    source_endpoint     TEXT,
    PRIMARY KEY (project_id, unit_type_raw)
);

------------------------------------------------------------------------------
-- Unit layer: Form 3 header, one row per unit (latest Form 3), change history, per-block fetch outcome
------------------------------------------------------------------------------
-- Header of every Form 3 seen for a project (a new quarterly Form 3 has a new id -> a new row).
CREATE TABLE IF NOT EXISTS project_form3 (
    project_id          INTEGER NOT NULL REFERENCES projects(project_id),
    form_three_id       INTEGER NOT NULL,
    form_status         TEXT,
    submitted_on        TEXT,              -- submissionOn as published (the portal's "Submitted On")
    number_of_units     INTEGER,           -- declared in the header; can disagree with the unit rows
    booked_declared     INTEGER,
    unbooked_declared   INTEGER,           -- NOTE: copies bookedUnit for some projects (source bug); never trusted alone
    total_residential   INTEGER,
    total_shop          INTEGER,
    total_office        INTEGER,
    total_others        INTEGER,
    is_latest           INTEGER,           -- 1 for the Form 3 id currently published by qtr_form_ids
    first_seen_at       TEXT,
    last_seen_at        TEXT,
    source_endpoint     TEXT,
    PRIMARY KEY (project_id, form_three_id)
);

-- One row per unit as published in the latest Form 3. Source values are kept as published; only the *_norm / *_class
-- columns are derived. Personal fields (allottee name, KYC, mobile) and the masked amounts are never stored.
-- Identity: (project, block, normalised unit label, duplicate sequence) - the source row id changes per Form 3.
CREATE TABLE IF NOT EXISTS project_unit_inventory (
    project_id          INTEGER NOT NULL REFERENCES projects(project_id),
    block_name          TEXT NOT NULL,
    unit_no_norm        TEXT NOT NULL,
    dup_seq             INTEGER NOT NULL DEFAULT 0,
    block_id_src        TEXT,
    unit_no_raw         TEXT,
    unit_kind           TEXT,              -- single | range | list
    usage_raw           TEXT,
    usage_class         TEXT,              -- residential | shop | office | plot | other
    carpet_area_sqm     REAL,
    area_excl_balcony_terrace_sqm REAL,    -- ONE source field: exclusive balcony/veranda AND open terrace, not splittable
    status_source       TEXT,              -- exactly as published (BOOKED / UNBOOKED / ...)
    status_norm         TEXT,              -- booked | unbooked | other | unknown
    agreement_date      TEXT,              -- date part of the published agreement date (YYYY-MM-DD)
    agreement_date_raw  TEXT,
    encumbrance_status  TEXT,
    redeveloped_flag    TEXT,
    source_row_id       INTEGER,
    form_three_id       INTEGER,
    form3_submitted_on  TEXT,
    is_current          INTEGER NOT NULL DEFAULT 1,
    first_seen_at       TEXT,
    last_seen_at        TEXT,
    last_changed_at     TEXT,
    PRIMARY KEY (project_id, block_name, unit_no_norm, dup_seq)
);
CREATE INDEX IF NOT EXISTS ix_unitinv_status ON project_unit_inventory(project_id, block_name, status_norm);
CREATE INDEX IF NOT EXISTS ix_unitinv_project ON project_unit_inventory(project_id, is_current);

-- Append-only: written when a unit appears, disappears or its status / agreement date changes between scrapes.
-- GujRERA publishes only the latest Form 3, so this history starts at our first scrape of a project.
CREATE TABLE IF NOT EXISTS project_unit_history (
    id                  INTEGER PRIMARY KEY,
    project_id          INTEGER NOT NULL,
    block_name          TEXT NOT NULL,
    unit_no_norm        TEXT NOT NULL,
    dup_seq             INTEGER NOT NULL DEFAULT 0,
    observed_at         TEXT NOT NULL,
    change_type         TEXT NOT NULL,     -- appeared | status_changed | agreement_date_changed | disappeared
    status_old          TEXT,
    status_new          TEXT,
    agreement_date_old  TEXT,
    agreement_date_new  TEXT,
    form_three_id       INTEGER
);
CREATE INDEX IF NOT EXISTS ix_unithist_unit ON project_unit_history(project_id, block_name, unit_no_norm, dup_seq);

-- Outcome of the unit-list request of each block (drives projects.unit_data_state).
CREATE TABLE IF NOT EXISTS project_block_fetch (
    project_id          INTEGER NOT NULL,
    block_name          TEXT NOT NULL,
    outcome             TEXT NOT NULL,     -- ok | empty | error | not_fetched
    row_count           INTEGER,
    http_status         INTEGER,
    form_three_id       INTEGER,
    in_expected         INTEGER,           -- 1 if the name comes from the trusted block-name source of the project
    fetched_at          TEXT,
    PRIMARY KEY (project_id, block_name)
);

-- External development / services / near-by facilities declared at registration (not a gym/pool style amenity list:
-- the public API has no such structured list).
CREATE TABLE IF NOT EXISTS project_infrastructure (
    project_id          INTEGER NOT NULL REFERENCES projects(project_id),
    category            TEXT NOT NULL,     -- external_development | water_sewage | nearby | other_development
    name                TEXT NOT NULL,
    value               TEXT,
    source_endpoint     TEXT,
    PRIMARY KEY (project_id, category, name)
);

-- Facilities / amenities from the latest Form 1 (part B): common facilities with YES/NO applicability, % work done and
-- dates, plus facilities named by the promoter (e.g. CLUB HOUSE). Items that are not listed are "not declared".
CREATE TABLE IF NOT EXISTS project_amenities (
    project_id          INTEGER NOT NULL REFERENCES projects(project_id),
    item_key            TEXT NOT NULL,     -- Form 1B stem (internalRoad, fireFighting, ...) or other:<id>
    label               TEXT,
    category            TEXT,              -- roads | water | sewage_waste | electrical | energy | fire_safety | safety_security |
                                           -- landscaping | community | structure | other | named_by_promoter
    source              TEXT,              -- form_one_common | form_one_other
    applicable          TEXT,              -- YES | NO | NULL (not stated)
    progress_pct        REAL,              -- % of the work done as certified (meaningful when applicable = YES)
    planned_start       TEXT,
    planned_end         TEXT,
    actual_start        TEXT,
    actual_end          TEXT,
    remarks             TEXT,
    form_one_id         INTEGER,
    form1_submitted_on  TEXT,
    PRIMARY KEY (project_id, item_key)
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version             TEXT PRIMARY KEY,
    applied_at          TEXT NOT NULL,
    description         TEXT
);

-- QPR physical-progress history (one row per reported quarter).
CREATE TABLE IF NOT EXISTS project_progress (
    project_id      INTEGER NOT NULL REFERENCES projects(project_id),
    seq             INTEGER NOT NULL,
    quarter_label   TEXT NOT NULL,         -- e.g. 'March 2018' as published
    period_date     TEXT,                  -- first day of that month
    physical_progress_pct REAL,
    source_endpoint TEXT,
    PRIMARY KEY (project_id, seq)
);

-- QPR filing calendar & compliance (one row per quarter slot incl. Q-E and BWA entries).
CREATE TABLE IF NOT EXISTS project_quarters (
    quarter_id      INTEGER PRIMARY KEY,   -- GujRERA qusrterId
    project_id      INTEGER NOT NULL REFERENCES projects(project_id),
    quarter_name    TEXT,                  -- Q-1, Q-2, ..., Q-E, BWA-n
    quarter_index   INTEGER,
    period_start    TEXT,
    period_end      TEXT,
    due_date        TEXT,                  -- extendedDate (deadline)
    submitted_on    TEXT,
    status          TEXT,                  -- SUBMITTED | EXEMPT | INACTIVE | ...
    defaulter_code  TEXT,                  -- defauterId as published (e.g. D-2): flagged as late/defaulted by portal
    penalty_amount  REAL,
    auth_remarks    TEXT,
    reg_no_at_time  TEXT,
    source_endpoint TEXT
);
CREATE INDEX IF NOT EXISTS ix_quarters_project ON project_quarters(project_id);

CREATE TABLE IF NOT EXISTS project_extensions (
    project_id              INTEGER NOT NULL REFERENCES projects(project_id),
    extension_seq           INTEGER NOT NULL,      -- 1,2,3... in approval order
    application_id          INTEGER,               -- appRegId
    extension_reg_no        TEXT,
    ex_markers              TEXT,                  -- e.g. 'EX2/EX3'
    submitted_on            TEXT,
    approved_on             TEXT,
    status                  TEXT,                  -- approved | pending | unknown
    previous_completion_date TEXT,
    extended_completion_date TEXT,
    extended_completion_date_source TEXT,          -- reg_no_suffix | certificate | project_list | NULL
    extension_days          INTEGER,
    reason                  TEXT,                  -- NULL unless a source publishes it
    certificate_uid         TEXT,
    source_endpoint         TEXT,
    PRIMARY KEY (project_id, extension_seq)
);

-- Every application row published for the project (registration, extension, annual compliance,
-- complaints, challans, BCR...). Kept verbatim for traceability.
CREATE TABLE IF NOT EXISTS project_applications (
    project_id          INTEGER NOT NULL REFERENCES projects(project_id),
    app_reg_id          INTEGER NOT NULL,
    application_type    TEXT,
    application_number  TEXT,
    submission_date     TEXT,
    approval_date       TEXT,
    final_status        TEXT,
    complaint_number    TEXT,
    complaint_status    TEXT,
    challan_number      TEXT,
    challan_amount      REAL,
    challan_status      TEXT,
    challan_payment_date TEXT,
    certificate_uid     TEXT,
    PRIMARY KEY (project_id, app_reg_id, application_type)
);

CREATE TABLE IF NOT EXISTS annual_compliance (
    project_id          INTEGER NOT NULL REFERENCES projects(project_id),
    financial_year      TEXT NOT NULL,
    status              TEXT,              -- SUBMITTED | EXEMPT | ...
    pdf_uid             TEXT,
    PRIMARY KEY (project_id, financial_year)
);

------------------------------------------------------------------------------
-- Complaints, regulatory actions, appeals
------------------------------------------------------------------------------
-- Complainant-initiated matters (Form A/B online & offline, review, ratification, conciliation).
CREATE TABLE IF NOT EXISTS complaints (
    complaint_key       TEXT PRIMARY KEY,  -- acknowledgement / application number
    complaint_number    TEXT,              -- CMP... internal number where published
    comp_id             TEXT,
    category            TEXT,              -- rera_complaint | adjudication_complaint | review | ratification | conciliation | unknown
    form_type           TEXT,              -- as published (FORM_A, OFF_COMPLAINT_A, ...)
    complaint_type_source TEXT,            -- comptype as published
    project_id          INTEGER REFERENCES projects(project_id),
    project_link_method TEXT,              -- reg_serial | project_applications | unlinked
    proj_reg_no_source  TEXT,
    project_name_source TEXT,
    district            TEXT,
    respondent_name     TEXT,              -- usually the promoter
    complainant_hash    TEXT,              -- salted hash; names are not stored
    filed_date          TEXT,
    status              TEXT,              -- latest status published (e.g. CLOSED, HEARING_SCHEDULE) or 'order_published'
    status_source       TEXT,
    order_date          TEXT,
    order_type          TEXT,              -- judge_type (Final / Interim ...)
    outcome_text        TEXT,              -- remarks as published (verbatim; not interpreted)
    penalty_flag        TEXT,              -- YES/NO as published
    penalty_amount      REAL,
    order_doc_uid       TEXT,
    source_endpoint     TEXT,
    first_seen_at       TEXT,
    last_seen_at        TEXT
);
CREATE INDEX IF NOT EXISTS ix_complaints_project ON complaints(project_id);

-- Authority-initiated actions: suo-motu proceedings, challans/penalties.
CREATE TABLE IF NOT EXISTS regulatory_actions (
    action_key          TEXT PRIMARY KEY,
    action_category     TEXT NOT NULL,     -- suo_motu_proceeding | penalty_challan
    action_subtype      TEXT,              -- e.g. NCQR, NCAR, AGREEMENTSALE (code as published)
    action_subtype_label TEXT,
    project_id          INTEGER REFERENCES projects(project_id),
    project_link_method TEXT,
    proj_reg_no_source  TEXT,
    related_complaint_number TEXT,
    district            TEXT,
    respondent_name     TEXT,
    initiated_date      TEXT,
    status              TEXT,
    order_date          TEXT,
    outcome_text        TEXT,
    penalty_flag        TEXT,
    penalty_amount      REAL,
    payment_status      TEXT,
    payment_date        TEXT,
    order_doc_uid       TEXT,
    source_endpoint     TEXT,
    first_seen_at       TEXT,
    last_seen_at        TEXT
);
CREATE INDEX IF NOT EXISTS ix_actions_project ON regulatory_actions(project_id);

CREATE TABLE IF NOT EXISTS appeals (
    appeal_key          TEXT PRIMARY KEY,
    forum               TEXT NOT NULL,     -- appellate_tribunal | high_court | supreme_court
    case_number         TEXT,
    subject             TEXT,
    respondent_text     TEXT,
    decision_date       TEXT,
    project_id          INTEGER REFERENCES projects(project_id),
    project_link_method TEXT,              -- name_match (low confidence) | unlinked
    project_link_confidence TEXT,
    doc_uids            TEXT,
    source_endpoint     TEXT
);

------------------------------------------------------------------------------
-- Documents
------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS project_documents (
    document_uid        TEXT PRIMARY KEY,  -- GujRERA vdms uid
    document_id         INTEGER,           -- vdms numeric id where published
    project_id          INTEGER,
    related_key         TEXT,              -- complaint/action/appeal key for non-project docs
    document_type       TEXT NOT NULL,     -- registration_certificate | extension_certificate | qe_form_four | complaint_order | ...
    document_name       TEXT,
    document_date       TEXT,
    source_endpoint     TEXT,
    download_url        TEXT,
    file_type           TEXT,
    download_priority   TEXT NOT NULL,     -- critical | metadata_only
    download_status     TEXT NOT NULL DEFAULT 'not_downloaded', -- not_downloaded | downloaded | failed | skipped
    local_path          TEXT,
    size_bytes          INTEGER,
    sha256              TEXT,
    text_status         TEXT,              -- extracted | ocr_needed | failed
    text_path           TEXT,
    parsed_fields       TEXT,              -- JSON of structured fields parsed from the document
    downloaded_at       TEXT
);
CREATE INDEX IF NOT EXISTS ix_docs_project ON project_documents(project_id);
CREATE INDEX IF NOT EXISTS ix_docs_status ON project_documents(download_priority, download_status);

------------------------------------------------------------------------------
-- Change tracking & data quality
------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS project_status_history (
    project_id      INTEGER NOT NULL,
    field           TEXT NOT NULL,
    old_value       TEXT,
    new_value       TEXT,
    changed_at      TEXT NOT NULL,
    run_id          TEXT
);
CREATE INDEX IF NOT EXISTS ix_status_hist ON project_status_history(project_id);

CREATE TABLE IF NOT EXISTS project_scrape_history (
    project_id      INTEGER NOT NULL,
    run_id          TEXT NOT NULL,
    scraped_at      TEXT NOT NULL,
    endpoints_ok    INTEGER,
    endpoints_failed INTEGER,
    changed_endpoints INTEGER,
    PRIMARY KEY (project_id, run_id)
);

CREATE TABLE IF NOT EXISTS field_provenance (
    entity_type     TEXT NOT NULL,
    entity_id       TEXT NOT NULL,
    field           TEXT NOT NULL,
    value           TEXT,
    value_state     TEXT NOT NULL,         -- REPORTED | NULL | UNKNOWN | NOT_APPLICABLE | DERIVED
    source          TEXT NOT NULL,         -- human-readable source (e.g. 'GujRERA block chart API')
    source_url      TEXT,
    source_document TEXT,
    extracted_at    TEXT NOT NULL,
    last_verified_at TEXT NOT NULL,
    confidence      TEXT NOT NULL,         -- high | medium | low
    PRIMARY KEY (entity_type, entity_id, field)
);

CREATE TABLE IF NOT EXISTS field_conflicts (
    entity_type     TEXT NOT NULL,
    entity_id       TEXT NOT NULL,
    field           TEXT NOT NULL,
    value_1         TEXT,
    source_1        TEXT,
    value_2         TEXT,
    source_2        TEXT,
    conflict_status TEXT NOT NULL,         -- resolved_by_rule | unresolved | explained
    interpreted_value TEXT,
    rule            TEXT,
    detected_at     TEXT NOT NULL,
    PRIMARY KEY (entity_type, entity_id, field, source_1, source_2)
);

CREATE TABLE IF NOT EXISTS data_quality_notes (
    id              INTEGER PRIMARY KEY,
    scope           TEXT NOT NULL,
    note            TEXT NOT NULL,
    detected_at     TEXT NOT NULL
);

------------------------------------------------------------------------------
-- Analytics outputs (rebuilt by `cli.py analyze`)
------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS project_metrics (
    project_id              INTEGER PRIMARY KEY,
    sales_pct               REAL,
    unsold_pct              REAL,
    completion_pct          REAL,
    previous_progress_pct   REAL,
    progress_velocity_pp_per_qtr REAL,
    months_since_last_qpr   REAL,
    stalled_flag            INTEGER,
    days_beyond_original    INTEGER,
    days_beyond_current     INTEGER,
    total_extension_days    INTEGER,
    complaints_total        INTEGER,
    complaints_pending      INTEGER,
    suo_motu_total          INTEGER,
    penalties_total         INTEGER,
    penalty_amount_total    REAL,
    qpr_defaults            INTEGER,
    risk_score              REAL,
    risk_components         TEXT,          -- JSON {component: score}
    risk_flags              TEXT,          -- JSON [{flag, evidence}]
    computed_at             TEXT
);

CREATE TABLE IF NOT EXISTS developer_metrics (
    developer_id            INTEGER PRIMARY KEY,
    metrics                 TEXT NOT NULL, -- JSON of all developer metrics (also exported flat)
    risk_score              REAL,
    risk_components         TEXT,
    risk_flags              TEXT,
    computed_at             TEXT
);
