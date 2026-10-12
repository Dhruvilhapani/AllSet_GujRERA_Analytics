# Ahmedabad Developer Intelligence (web app)

Dashboard + Developers section over the GujRERA data collected by `../gujrera_scraper`.

```
gujrera_scraper  --crawl-->  data/gujrera.db  --refresh-app-->  data/app/gujrera_ahmedabad.db  --read-->  webapp
```

## Run

From the project root (`AllSet_GujRERA_Analytics/`):

```powershell
powershell -ExecutionPolicy Bypass -File .\run\start_all.ps1   # crawler + data refresher + web app (3 windows)
powershell -ExecutionPolicy Bypass -File .\run\stop_all.ps1    # stop all three (crawl keeps its checkpoint)
```

Or each part on its own (each restarts itself if it crashes):

```powershell
powershell -ExecutionPolicy Bypass -File .\run\run_crawler.ps1     # resume the GujRERA crawl
powershell -ExecutionPolicy Bypass -File .\run\run_refresher.ps1   # publish new data to the app every 10 min
powershell -ExecutionPolicy Bypass -File .\run\run_webapp.ps1      # http://localhost:8600
```

The plain commands behind them:

```powershell
cd gujrera_scraper
..\.venv\Scripts\python.exe cli.py crawl --resume                                   # continue the crawl
..\.venv\Scripts\python.exe cli.py refresh-app --district Ahmedabad --every 10      # app data every 10 min
..\.venv\Scripts\python.exe cli.py status                                           # crawl progress
cd ..
.\.venv\Scripts\python.exe -m uvicorn webapp.server.app:app --host 127.0.0.1 --port 8600
```

- Only one crawler and one refresher can run at a time; a second copy exits with a message.
- No internet / laptop asleep: the crawler pauses and continues automatically; nothing is lost.
- The web app checks for new data every minute and updates the open page by itself.
- Frontend changes need a rebuild: `cd webapp\frontend; npm run build`.

Frontend development with hot reload: `npm run dev` in `webapp/frontend` (http://localhost:5180, proxies `/api` to :8600).

`RERA_APP_DB` overrides the snapshot path (default `gujrera_scraper/data/app/gujrera_ahmedabad.db`).

## Pages

- **Dashboard** `/dashboard`: 6 KPIs (projects, developers, completed, delayed, avg. progress, complaints), market
  project progress by year, registrations per year, status bar, sales % distribution, largest developers, localities,
  legal summary.
- **Developers** `/developers`: search, filters, server-side sorting and paging, compare 2–4 developers.
- **Developer** `/developers/:id`, in this order: header, 6 KPIs, project progress by year, project table (units / sold /
  remaining / sales % / progress per project), sales % by project + highlights, construction status, extensions and
  delayed projects (kept separate), legal (expandable records + timeline), locations, growth.
- **Project** `/projects/:id`: details, units, construction, approved extensions, progress history, legal records.

Every metric is computed on the server (`webapp/server/analytics.py`, pandas / NumPy) and defined in
[METRICS.md](METRICS.md). The frontend only lays the numbers out.

## API

`GET /api/meta`, `/api/dashboard`, `/api/developers`, `/api/developers/{id}`, `/api/projects/{id}` (docs at `/api/docs`).

## Data rules

- Every figure comes from GujRERA records; nothing is estimated. Missing values are `null` / "N/A", never 0.
- While the crawl runs the app labels the data a *partial dataset* and shows analysed vs pending counts.
- Units, sold and remaining appear at project level only (as last reported to GujRERA); there are no portfolio-level
  unit KPIs and no revenue figures.

## Checks

```powershell
.\.venv\Scripts\python.exe -m pytest -q webapp/tests gujrera_scraper/tests   # unit + API tests
ruff check webapp; ruff format --check webapp                                 # Python lint / format
cd webapp\frontend; npm run lint; npm run build                              # frontend lint / build
```

Offline metric QA (independent recomputation vs the API, plus Matplotlib plots in `webapp/qa/out/`):

```powershell
.\.venv\Scripts\python.exe webapp\qa\validate_metrics.py --snapshot gujrera_scraper\data\app\gujrera_ahmedabad.db --pipeline-db gujrera_scraper\data\snapshot_app.db
```

Exit code 1 lists every mismatch. Matplotlib is a QA-only dependency; the server never imports it.
