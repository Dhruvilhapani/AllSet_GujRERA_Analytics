# One-shot full scrape for Ahmedabad + Gandhinagar (Residential, Mixed, Commercial, Plotted):
# project details, developers, documents, blocks, unit-wise booking rows, facilities / amenities.
#   1. backs up the crawl database once (data\gujrera_before_cities_scrape.db)
#   2. crawls only what is still missing (URLs already stored are not fetched again), restarting after a crash
#   3. retries failed tasks once
#   4. publishes the ONE app database (data\app\gujrera_ahmedabad_gandhinagar.db, unit rows included) and the coverage report
# Resumable: close the window / reboot and run it again; the crawl continues from its checkpoint.
# Progress: python cli.py status    and    data\pipeline.log
param([string[]]$Districts = @("Ahmedabad", "Gandhinagar"))
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
Set-Location (Join-Path $root "gujrera_scraper")
$Host.UI.RawUI.WindowTitle = "RERA full scrape"

$backup = "data\gujrera_before_cities_scrape.db"
if (-not (Test-Path $backup)) {
    Write-Host "Backing up data\gujrera.db -> $backup"
    & $py -c "import sqlite3; s=sqlite3.connect('data/gujrera.db'); d=sqlite3.connect(r'$backup'); s.backup(d); d.close(); s.close()"
}

# Step 2: new run on the first pass, --resume of that run after a crash
$first = $true
while ($true) {
    if ($first) { & $py cli.py crawl --mode full --cities-only --missing-only --districts @Districts }
    else { & $py cli.py crawl --resume --districts @Districts }
    $code = $LASTEXITCODE
    $first = $false
    if ($code -eq 0) { break }
    if ($code -eq 2) { Write-Host "A crawler is already running in another window."; exit 2 }
    Write-Host "Crawler stopped unexpectedly (exit $code). Restarting in 30 seconds..."
    Start-Sleep -Seconds 30
}

# Step 3: one retry pass for tasks that failed (HTTP errors after all retries). The run id is passed explicitly:
# without it --retry-failed resumes the OLDEST unfinished run, which may be an earlier all-Gujarat crawl.
$runId = & $py -c "import sqlite3; print(sqlite3.connect('data/gujrera.db').execute('select run_id from crawl_runs order by started_at desc limit 1').fetchone()[0])"
& $py cli.py crawl --retry-failed --run-id $runId --districts @Districts

# Step 4: parse a temporary copy of the crawl database, publish the app database (unit rows included) and write
# exports\unit_data_coverage.csv; the temporary copy is deleted afterwards
& $py cli.py refresh-app --districts @Districts
Write-Host "Done. Start the web app with run\run_webapp.ps1"
