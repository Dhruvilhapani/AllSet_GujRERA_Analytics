# Scraping strategy

## Principles

* Public, unauthenticated GET endpoints only — the same calls the official web app makes for anonymous visitors.
  No login, no captcha, no access-control bypass, no bank-account endpoints.
* Polite: global rate limit (default 2.5 requests/s across all workers), descriptive User-Agent, exponential
  back-off on 429/5xx/timeouts (`Retry-After` honoured), at most 6 requests in flight.
* Two decoupled stages: **crawl** stores raw responses; **parse** rebuilds the normalized tables from them offline.
  Parsers can be fixed and re-run without touching the server.

## Stages

| Command | What it does |
|---|---|
| `cli.py discover` | dashboard grid + full Residential and Mixed lists; prints coverage / duplicate check |
| `cli.py poc` | end-to-end proof on a stratified sample of 10 projects into `data/poc.db` |
| `cli.py crawl --mode full` | universe → queue of (endpoint × project) tasks → dependent tasks (Form 1/2 ids, promoter) as soon as each project's core record arrives → complaint / order lists derived from the summary grid |
| `cli.py parse` | raw → normalized tables, provenance, conflicts, complaint joins, developer groups |
| `cli.py docs` | downloads analytics-critical documents, extracts text, parses certificates, cross-validates |
| `cli.py analyze` | metrics + Analytical Risk Score |
| `cli.py export` | CSVs, Excel, executive report, run summary |

## Completeness

* The list endpoints return the entire result set in one response (no pagination, no lazy loading). Completeness is
  verified by comparing unique `projectRegId`s with the dashboard grid totals (Residential 7,880, Mixed 6,004 on
  2026-10-07: 100 % coverage, 0 duplicate ids, 0 duplicate registration numbers).
* Complaint lists: every (form type × district column) cell present in the summary grid, plus the suo-motu
  non-compliance lists (NCQR / NCQER / NCAR) that the grid rows map to.

## Robustness

| Failure | Handling |
|---|---|
| timeouts, connection resets | retried with back-off; the HTTP client is rebuilt after transport errors |
| HTTP 429 / 5xx | retried (1, 2, 4, 8 s … or `Retry-After`) |
| HTTP 400 / 404 for an entity | task marked `empty` (e.g. no map polygon) — not retried, not a failure |
| task still failing after retries | attempt counter += 1; after `max_task_attempts` the task lands in the **failure queue** (`status='failed'`) — `crawl --retry-failed` re-queues them |
| process killed / machine restart | tasks are marked `done` only after their response is committed; `crawl --resume` continues the latest unfinished run from the checkpoint (in-flight requests are simply re-sent) |
| malformed / non-JSON payloads | stored raw; the parser records a data-quality note and continues with the next project |
| malformed PDFs / images | `text_status = failed` / `ocr_needed`; the original file is kept |
| API shape changes | parsers read fields defensively (`.get`), unknown values stay NULL, and every raw payload is kept for re-parsing |

Logs: `data/pipeline.log` (structured `time level module: message`), progress every 500 tasks with queue
and client statistics. `cli.py status` shows queue state and failure samples per endpoint.

## Incremental runs

`cli.py crawl --mode incremental`:

1. re-fetches the dashboard grid and both lists;
2. projects that are **new**, whose **list row changed** (hash stored in `projects.list_row_hash`), or that were not
   fully refreshed for `full_refresh_days` (90) get all endpoints;
3. all other projects re-fetch only the volatile endpoints (core, applications, QPR, Q-E, booking chart, unit
   summary, Form 5);
4. complaint lists are re-fetched.

`raw_responses` tracks `first_fetched_at`, `fetched_at` and `changed_at` (SHA-256 of the body), the queue records
`changed` per task, and parsing logs changes of tracked project fields (status, completion date, extension count,
progress, unit counts, Q-E status, reg. no.) in `project_status_history` with `last_changed_at`.
`first_seen_at`, `last_seen_at`, `last_scraped_at` are maintained on projects, developers, complaints and actions.

## Volume (full run)

≈ 13,884 projects × 15 endpoints + ≈ 2 dependent calls + unique promoters + ~200 complaint lists
≈ 230 k requests ≈ 24–26 h at 2.5 requests/s. Critical documents: ≈ 16 k certificates + Q-E / completion uploads
+ ≈ 10 k orders.
