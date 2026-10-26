# Metric definitions

Every number in the web app is computed on the server in `webapp/server/analytics.py` (pandas / NumPy) from the
published snapshot (`gujrera_scraper/data/app/gujrera_ahmedabad_gandhinagar.db`, or the old Ahmedabad-only `Gujrara_old_data_15_10_26.db`). The frontend only displays the results.
`webapp/tests/test_analytics.py` pins each rule below, and `webapp/qa/validate_metrics.py` recomputes the main
metrics independently and compares them with the API.

## General rules

- **Source.** Only public GujRERA records are used. Nothing is estimated, interpolated or imputed.
- **Missing is not zero.** A value GujRERA does not publish, or that has not been crawled yet, is `null` and shows
  as "N/A". It is left out of averages and counts and is never treated as 0.
- **Analysed project.** A project whose GujRERA detail record has been crawled (`detail_crawled = 1`). Status,
  progress, sales, extension and legal metrics use analysed projects only. Registration counts use every registered
  project.
- **Scope.** The cities and categories in the snapshot (`/api/meta`: Ahmedabad and Gandhinagar; Residential, Mixed-use,
  Commercial, Plotted). Dashboard and Developers accept `city` / `category`; developer-level figures always cover the
  developer's whole portfolio in the snapshot.
- **Not shown.** Revenue (GujRERA does not publish it), and portfolio-level unit totals (units / sold / unsold exist
  only at project level).

## Project level

| Metric | Definition | Missing data |
|---|---|---|
| **Status: Completed** | A completion filing (Q-E with Form 4) exists, or the portal marks the project completed. | — |
| **Status: Delayed** | Past the latest approved completion date, with no completion filing. | A missing date is never treated as a delay. |
| **Status: Ongoing** | Within the latest approved completion date. | — |
| **Status: Pending** | Analysed, but no quarterly progress report (QPR) filed yet. | — |
| **Status: Not crawled** | The detail record has not been crawled yet. | Excluded from analysed counts. |
| **Stalled** (flag) | An Ongoing project with no recent progress report. It is a flag, not a status. | — |
| **Extended** | At least one *approved* extension. Extensions are separate from delays: an extended project that is inside its new date is Ongoing. | Not crawled → `null` |
| **Extension duration** | Latest approved completion date − original completion date, in months (days ÷ 30.4375, one decimal). | Either date missing → `null` |
| **Units** | Total units declared to GujRERA. | Not published → `null` |
| **Sold** | Units booked, as last reported in the QPR. For completed projects this is frozen at the last report. | Not published → `null` |
| **Remaining** | Unsold (unbooked) units, as last reported. | Not published → `null` |
| **Sales %** | sold ÷ (sold + unsold) × 100. | Either missing, or both 0 → `null` |
| **Progress** | Physical construction progress in the latest QPR, capped at 100%. | No QPR → `null` |
| **Unit rows** (Blocks & units tab) | One row per unit of the latest Form 3, as published. Booked / unbooked counts are counted from these rows and shown only next to the status they came from; the portal's block booking chart (QPR-fed) is shown beside them because the two are filed separately and can differ. | Unit data not complete → state banner, no totals; never counted as unbooked |
| **Area (balcony / terrace)** | One published figure per unit: exclusive balcony/veranda and open terrace together. Not split. `0` is a published zero. | Not published → `null` ("N/A") |
| **Date of agreement** | As reported by the promoter; not necessarily the first-sale date. | Blank → "N/A" |

## Developer level

| Metric | Definition | Missing data |
|---|---|---|
| **Total projects** | All registered projects of the developer (merged GujRERA promoter accounts; see Identity). | — |
| **Completed / Ongoing / Delayed / Pending** | Counts of analysed projects by status. | No analysed projects → `null` |
| **Avg. progress** | Mean of the latest progress (capped at 100) of analysed projects that have a QPR. | None reported → `null` |
| **Complaints / cases** | All complaints filed with GujRERA against any of the developer's analysed projects, shown as one developer-wide list (newest first) on the developer page. The project page only links to that list. Complaints are attributed through the project GujRERA links them to. Complaints with no project link (mostly unregistered projects) are not attributed by name. | Not crawled → `null` |
| **Open / closed complaint** | Closed when the published status records an end of the case (closed, disposed, dismissed, withdrawn, rejected, order passed, decided, settled, deleted), or when a *final* order is published. Open for any other status, or an *interim* order. "Not published" when there is no status. | No status → "Not published" |
| **Regulatory actions** | Suo-motu proceedings and penalty challans initiated by GujRERA. Listed separately from complaints. | — |
| **Project progress by year** | See below. | — |
| **Sales % by project** | Sales % per project with booking data, highest first. With more than 20 projects the chart shows the 10 highest and 10 lowest, and the table lists all of them. The median is shown in the footer. | Projects without booking data are counted, not plotted. |
| **Highest / lowest sales** | Max / min Sales % among projects with booking data for at least 20 units. | Fewer than 2 eligible → hidden |
| **Highest / lowest progress** | Max / min latest progress among non-completed projects with a QPR. | Fewer than 2 eligible → hidden |
| **Projects with extensions / extension events** | Analysed projects with at least one approved extension / number of approved extensions. Average and longest duration in months. | GujRERA publishes no extension reason, so none is shown. |
| **Delayed projects** | Projects with status Delayed, longest past their approved date first. | Unknown days → listed last |
| **Locations** | Projects per GujRERA locality (*moje*), with completed count and average latest progress. | No locality → "Location not published" |
| **Portfolio growth** | Projects registered per year (GujRERA approval year), all registered projects. | No date → excluded |
| **Geographic expansion** | Cumulative number of distinct localities with a registered project, by year. | No locality → excluded, and the count is shown |

## Project progress by year (developer and market)

1. **Source.** `project_progress`: the physical progress % in each QPR, as published.
2. **Per project, per calendar year.** Take the **last** report of that year (latest period, then filing order) and
   cap it at 100%.
3. **Per year.** Take the **arithmetic mean** across projects that reported that year. `n_projects` is shown in the
   tooltip and in the table view. A units-weighted mean is also returned, for QA only.
4. **Excluded: future-labelled points.** Reports labelled with a period after today, such as a project-end filing
   labelled "January 2027", are dropped and counted (`excluded_future_points`).
5. **Excluded: thin market years.** On the market (dashboard) line, a year with fewer than **10** reporting projects
   is omitted and listed (`omitted_years`). Developer lines keep every year.
6. **No interpolation.** A year with no reports is absent.

Note that a project's progress can be revised downwards between reports. The value is used exactly as published,
and the QA script reports how many revisions exceed 30 percentage points.

## Dashboard (market)

| Metric | Definition |
|---|---|
| **Registered projects** | All projects in scope; "analysed" is shown underneath. |
| **Developers** | Distinct developers with at least one analysed project. |
| **Completed / Delayed / status bar** | Project status counts over analysed projects. The stalled count and the not-crawled count are shown as notes. |
| **Avg. project progress** | Mean latest progress of analysed projects. |
| **Complaints** | Complaints linked to analysed projects. |
| **Registrations by year** | All registered projects per approval year, split Residential / Mixed-use. |
| **Sales % across projects** | Number of projects in each Sales % band: 0–20, 20–40, 40–60, 60–80 and 80–99%, plus exactly 100%. |
| **Largest developers** | Top 15 developers by registered projects. |
| **Localities** | Top 15 localities by project count. Projects without a published locality are counted in the subtitle. |

## Identity

Promoter accounts are merged into one developer only when they share a PAN (stored as a salted hash, never in
plain text) or a valid CIN / LLPIN. Similar names that are not confirmed this way stay separate and are listed as
"similar names".
