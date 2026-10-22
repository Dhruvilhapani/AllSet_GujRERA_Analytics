# GujRERA Residential & Mixed-use Intelligence Pipeline

A research-grade dataset of every **Residential** and **Mixed Development** project registered with the Gujarat
Real Estate Regulatory Authority (GujRERA): projects, promoters, construction progress (QPR history), extensions,
booked / unbooked inventory, complaints, suo-motu proceedings, penalties, appeals and documents — plus developer
analytics and a transparent **Analytical Risk Score (not an official RERA rating)**.

Data comes only from the public GujRERA portal APIs, discovered by observing the official web app's own network
traffic (see [docs/gujrera_architecture.md](docs/gujrera_architecture.md)).

## Setup

```powershell
python -m venv ..\.venv
..\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Optional: install Tesseract OCR and put it on PATH to OCR scanned orders (otherwise they are flagged `ocr_needed`).
Set `GUJRERA_HASH_SALT` to your own secret before the first parse (used to hash complainant names / PANs).

## Run

```powershell
python cli.py discover                 # universe + completeness check (~1 min)
python cli.py poc                      # proof of concept on 10 projects -> data/poc.db
python cli.py crawl --mode full        # full crawl (~24 h at 2.5 req/s), resumable
python cli.py crawl --resume           # continue after an interruption
python cli.py crawl --retry-failed     # retry the failure queue
python cli.py parse                    # raw -> normalized tables
python cli.py docs                     # download + parse analytics-critical PDFs
python cli.py analyze                  # metrics + risk score
python cli.py export                   # CSV / Excel / executive report
python cli.py status                   # progress, failures, table counts
python cli.py crawl --mode incremental # later runs: only new / changed projects get a full refresh
python cli.py backfill-units           # unit-wise booking layer (Ahmedabad + Gandhinagar), resumable
python cli.py coverage-report          # CSV: unit-data state per project and why it is incomplete
python cli.py verify-units --sample 30 # compare random complete projects with the live portal
python cli.py refresh-app --districts Ahmedabad Gandhinagar   # the one app database (units included) + coverage CSV
python -m pytest tests                 # unit tests (offline, use saved fixtures)
```

One-shot full scrape of Ahmedabad + Gandhinagar (all four categories, units, facilities), resumable, ~10 h at 2.5 req/s:
`powershell -ExecutionPolicy Bypass -File run
un_scrape_all.ps1` (backs up the crawl DB, fetches only what is missing,
retries failures, then publishes the app snapshot + unit rows + `exports/unit_data_coverage.csv`). Run it again after an
interruption; progress: `python cli.py status` and `data/pipeline.log`.

Scope (`config/settings.yaml`): Residential and Mixed are crawled for all of Gujarat; Commercial and Plotted only for
`target_districts`; the unit layer only for `units.districts`. See `docs/scraping_strategy.md` for moving an existing
crawl onto the new scope and `docs/data_dictionary.md` for the unit-wise fields and their limits.

`parse`, `analyze` and `export` can be run at any time during a crawl; projects not yet crawled appear with list
data only (`detail_crawled = 0`, counts NULL, status `unknown`).

## Outputs (`exports/`)

| File | Content |
|---|---|
| `csv/projects_master.csv` | one row per project: identity, status (source + derived with basis), dates, extensions, progress, inventory, complaints, actions, risk score & flags |
| `csv/developers_master.csv` | one row per promoter account: master data, aliases, portfolio / completion / inventory / delay / complaint metrics, risk |
| `csv/project_developers.csv` | project ↔ developer relationship |
| `csv/inventory_blocks.csv`, `csv/inventory_unit_types.csv` | block-level booked/unbooked/progress; declared unit types |
| `csv/progress_history.csv`, `csv/qpr_filings.csv` | QPR physical-progress history; QPR filing calendar & defaulter codes |
| `csv/complaints.csv`, `csv/regulatory_actions.csv`, `csv/appeals.csv` | complaint-level, action-level and appeal-level records |
| `csv/extensions.csv` | every extension with previous / extended completion dates |
| `csv/documents.csv` | every discovered document (uid, type, URL, download / text status, parsed fields) |
| `csv/field_conflicts.csv`, `csv/field_provenance.csv`, `csv/data_quality_notes.csv` | data-quality layer |
| `csv/developer_groups.csv`, `csv/developer_group_metrics.csv` | PAN-confirmed and review-only groupings of promoter accounts |
| `excel/gujrera_dataset.xlsx` | the main tables in one workbook |
| `reports/executive_report.md` / `.html` | Gujarat overview, top developers, evidence-backed high-risk projects, coverage |
| `reports/run_summary.json` | completion statistics of the run (discovered / scraped / failed / missing / duplicates / conflicts …) |

## Layout

```text
gujrera_scraper/
├── cli.py                 entry point
├── settings.py            loads config/settings.yaml
├── config/                settings.yaml, risk_scoring.yaml
├── docs/                  architecture, data dictionary, scraping strategy, risk methodology
├── scraper/               network_client (legacy-TLS, throttle, retries), endpoints registry, project_listing,
│                          project_detail, promoter, qpr, extensions, complaints, documents, orchestrator (queue)
├── parser/                common, project_parser, qpr_parser, complaint_parser, promoter_parser,
│                          document_parser, pipeline (raw -> tables)
├── database/              schema.sql, db.py (connection + raw store + migrations), models.py, repositories.py
├── analytics/             project, developer, inventory, delay, risk analysis; run.py
├── exports/               exporter.py, report.py  (+ csv/, excel/, reports/ outputs)
├── scripts/               dump_fixtures.py (developer tool)
└── tests/                 pytest suite + recorded API fixtures
```

## Responsible use

Only public data is collected, at a throttled rate. Complainant names and PANs are never stored (salted hashes
only); individual promoters' personal contact data is not stored. The risk score is an analytical heuristic —
read [docs/risk_methodology.md](docs/risk_methodology.md) before using it, and verify against the official portal
before drawing conclusions about any promoter or project.
