# AllSet GujRERA Analytics

Data pipeline and web app for GujRERA (Gujarat Real Estate Regulatory Authority) project records: a crawler and
parser for the public GujRERA portal, developer and project analytics, and an Ahmedabad developer-intelligence web app.

| Folder | Contents |
|---|---|
| [`gujrera_scraper/`](gujrera_scraper/README.md) | crawler, parser, analytics, exports, tests |
| [`webapp/`](webapp/README.md) | FastAPI server + React (Vite) frontend: Dashboard, Developers, Projects |
| `run/` | PowerShell launchers for the crawler, the data refresher and the web app |

## Data included in this repository

The web app reads one of two app snapshots in `gujrera_scraper/data/app/`:

- **`gujrera_ahmedabad_gandhinagar.db`** (SQLite, ~240 MB, **not in git**): the current database. Ahmedabad + Gandhinagar,
  Residential / Mixed / Commercial / Plotted, 7,178 projects, unit-wise booking rows, generated 2026-10-22. It is over
  GitHub's 100 MB file limit, so get it from the shared link (or rebuild it with `run\run_scrape_all.ps1`) and place it
  in that folder. The web app uses it whenever it is present.
- **`Gujrara_old_data_15_10_26.db`** (SQLite, ~10 MB, tracked in git; formerly `gujrera_ahmedabad.db`): the old Ahmedabad-only
  snapshot, used as a fallback so a fresh clone still runs:

- **4,520 projects** in Ahmedabad district (2,260 Residential/Group Housing + 2,260 Mixed Development); this snapshot
  pre-dates the Commercial / Plotted / Gandhinagar / unit-wise data, which the app supports as soon as the crawl has
  run (see *Unit-wise booking data* below)
- 3,880 developers, 33,654 construction-progress records, 11,853 blocks, 820 extensions, 3,681 legal records,
  13,502 project events
- generated 2026-10-09 from the public portal https://gujrera.gujarat.gov.in/ (see the `meta` table)

The raw crawl databases (`data/gujrera.db` and its working copies, 0.2–0.35 GB each) are **not** included. They are
larger than GitHub's file limit and contain personal contact data from raw portal responses. Rebuild them with the
crawler (`cli.py crawl`, see [gujrera_scraper/README.md](gujrera_scraper/README.md)). Personal emails, phone
numbers, PANs and Aadhaar numbers in the recorded test fixtures (`gujrera_scraper/tests/fixtures`) have been replaced
with fake values of the same shape.

## Unit-wise booking data, Commercial / Plotted projects, Gandhinagar

The pipeline also collects GujRERA's **Unit Wise Booking Status / Block Booking Details** (one row per unit: unit number,
usage, carpet area, the combined exclusive balcony/veranda + open-terrace area, booking status as published, date of
agreement) for Ahmedabad and Gandhinagar, across Residential, Mixed, Commercial and Plotted projects. Limits that come
from the source: only the latest filing is published (no booking history), balcony and terrace areas are one figure,
there is no per-unit floor (floors and lifts per block come from Form 1), and amounts / buyer details are not public and are never stored. Amenities come from Form 1 (common facilities with status and progress, plus any facility the promoter names, such as a club house); GujRERA has no other amenity list.

All app data, including the unit-wise booking rows (table `unit_inventory`), is in ONE database: `gujrera_scraper/data/app/gujrera_ahmedabad_gandhinagar.db`. It is git-ignored (over GitHub's 100 MB limit; see *Data included in this repository*). Commands and rules:
[gujrera_scraper/docs/scraping_strategy.md](gujrera_scraper/docs/scraping_strategy.md) and
[data_dictionary.md](gujrera_scraper/docs/data_dictionary.md).

## Setup

Requires Python 3.11+ and, for the frontend, Node.js 18+.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Run the web app on the included data

```powershell
powershell -ExecutionPolicy Bypass -File .\run\run_webapp.ps1    # builds the frontend on first run, then http://localhost:8600
```

Without the PowerShell launcher (any OS):

```bash
cd webapp/frontend && npm install && npm run build && cd ../..
.venv/bin/python -m uvicorn webapp.server.app:app --host 127.0.0.1 --port 8600   # Windows: .venv\Scripts\python.exe
```

The server reads `gujrera_scraper/data/app/gujrera_ahmedabad_gandhinagar.db` when it exists, otherwise
`Gujrara_old_data_15_10_26.db`; set `RERA_APP_DB` to use another snapshot. Crawling and refreshing the data are described in [webapp/README.md](webapp/README.md) and
[gujrera_scraper/README.md](gujrera_scraper/README.md).

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q webapp/tests gujrera_scraper/tests
```

## Responsible use

Only public GujRERA data is used. The risk score is an analytical heuristic, not an official RERA rating; read
[gujrera_scraper/docs/risk_methodology.md](gujrera_scraper/docs/risk_methodology.md) and verify against the official
portal before drawing conclusions about any promoter or project.
