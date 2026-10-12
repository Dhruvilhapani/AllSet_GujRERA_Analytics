# AllSet GujRERA Analytics

Data pipeline and web app for GujRERA (Gujarat Real Estate Regulatory Authority) project records: a crawler and
parser for the public GujRERA portal, developer and project analytics, and an Ahmedabad developer-intelligence web app.

| Folder | Contents |
|---|---|
| [`gujrera_scraper/`](gujrera_scraper/README.md) | crawler, parser, analytics, exports, tests |
| [`webapp/`](webapp/README.md) | FastAPI server + React (Vite) frontend: Dashboard, Developers, Projects |
| `run/` | PowerShell launchers for the crawler, the data refresher and the web app |

## Data included in this repository

`gujrera_scraper/data/app/gujrera_ahmedabad.db` (SQLite, ~10 MB) is the published app snapshot the web app reads:

- **4,520 projects** in Ahmedabad district (2,260 Residential/Group Housing + 2,260 Mixed Development)
- 3,880 developers, 33,654 construction-progress records, 11,853 blocks, 820 extensions, 3,681 legal records,
  13,502 project events
- generated 2026-10-09 from the public portal https://gujrera.gujarat.gov.in/ (see the `meta` table)

The raw crawl databases (`data/gujrera.db` and its working copies, 0.2–0.35 GB each) are **not** included. They are
larger than GitHub's file limit and contain personal contact data from raw portal responses. Rebuild them with the
crawler (`cli.py crawl`, see [gujrera_scraper/README.md](gujrera_scraper/README.md)). Personal emails, phone
numbers, PANs and Aadhaar numbers in the recorded test fixtures (`gujrera_scraper/tests/fixtures`) have been replaced
with fake values of the same shape.

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

The server reads `gujrera_scraper/data/app/gujrera_ahmedabad.db` by default; set `RERA_APP_DB` to use another
snapshot. Crawling and refreshing the data are described in [webapp/README.md](webapp/README.md) and
[gujrera_scraper/README.md](gujrera_scraper/README.md).

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q webapp/tests gujrera_scraper/tests
```

## Responsible use

Only public GujRERA data is used. The risk score is an analytical heuristic, not an official RERA rating; read
[gujrera_scraper/docs/risk_methodology.md](gujrera_scraper/docs/risk_methodology.md) and verify against the official
portal before drawing conclusions about any promoter or project.
