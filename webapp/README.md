# Ahmedabad Developer Intelligence (web app)

Dashboard + Developers section over the GujRERA data collected by `../gujrera_scraper`.

```
gujrera_scraper  --crawl-->  data/gujrera.db  --refresh-app-->  data/app/gujrera_ahmedabad_gandhinagar.db  --read-->  webapp
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

`RERA_APP_DB` overrides the snapshot path (default: `gujrera_scraper/data/app/gujrera_ahmedabad_gandhinagar.db` when it
exists, otherwise the old Ahmedabad-only `Gujrara_old_data_15_10_26.db` tracked in git). That one file holds everything, including the unit-wise booking rows (table
`unit_inventory`); on an older snapshot without that table the app works and the Blocks & units tab says the data is not
part of the build.
Snapshots published before the multi-city layout keep working (`city` falls back to the district).

## Pages

- **Dashboard** `/dashboard`: 6 KPIs (projects, developers, completed, delayed, avg. progress, complaints), market
  project progress by year, registrations per year, status bar, sales % distribution, largest developers, localities,
  legal summary.
- **Developers** `/developers`: search, filters, server-side sorting and paging, compare 2–4 developers.
- **Developer** `/developers/:id`, in this order: header, 6 KPIs, project progress by year, project table (units / sold /
  remaining / sales % / progress per project), sales % by project + highlights, construction status, extensions and
  delayed projects (kept separate), legal (expandable records + timeline), locations, growth.
- **Global search** (top bar on every page, press `/` to focus): live suggestions for projects (name or RERA no.), developers
  (name or alias) and localities; every word must match, in any order; arrows + Enter or click to open, Enter alone lists
  every matching project (`/projects?q=…`).
- **Projects** `/projects`: all projects, filter by city / category / status / locality, search project or developer.
- **Project** `/projects/:id` (tabs, kept in `?tab=`): Overview · Developer · Location & map · Amenities & infrastructure (Form 1 facilities with status and progress, promoter-named amenities, parking, external development) ·
  Construction & approvals (extensions, progress, certificates, Form 3, complaints, timeline) · Blocks & units (block
  cards; a **block grid** of every unit as a booked / unbooked tile with its agreement date and a click-through detail panel;
  and the unit-wise booking table with unit-number search and status / usage filters, server-paginated).

Every metric is computed on the server (`webapp/server/analytics.py`, pandas / NumPy) and defined in
[METRICS.md](METRICS.md). The frontend only lays the numbers out.

## API

`GET /api/meta`, `/api/dashboard?city&category`, `/api/developers?city&category&…`, `/api/developers/{id}`,
`/api/search?q=&limit=` (projects, developers, localities), `/api/projects?q&city&category&status&developer&locality&sort_by&page&page_size` (page_size up to 10,000), `/api/projects/{id}`,
`/api/projects/{id}/blocks`, `/api/projects/{id}/units?block&q&status&usage&sort_by&page&page_size` (docs at `/api/docs`).

## Map

The Location & map tab uses Leaflet (loaded on demand) and puts a red pin at the project location from GujRERA's map record over
**satellite imagery (default; Esri World Imagery with place labels)** or the OpenStreetMap street map, switchable on the
map. The base maps are the only third-party requests the app makes: `server.arcgisonline.com` and
`tile.openstreetmap.org` see the viewer's IP address and the map area requested. Keep the on-map attributions; heavy or
commercial use needs your own tile provider / licence (Esri and OSM terms). Change the sources at the top of
`frontend/src/components/MapView.jsx`.

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
.\.venv\Scripts\python.exe webapp\qa\validate_metrics.py --snapshot gujrera_scraper\data\app\gujrera_ahmedabad_gandhinagar.db --pipeline-db gujrera_scraper\data\snapshot_app.db
```

Exit code 1 lists every mismatch. Matplotlib is a QA-only dependency; the server never imports it.
