# Publishes fresh data to the web app every 10 minutes while the crawl runs.
# Copies the crawl database (the crawler is not blocked), parses it and swaps in a new app snapshot.
# The web app notices the new snapshot within a minute and reloads its data automatically.
# Writes the single app database data\app\gujrera_<cities>.db (unit-wise booking rows included) for the given cities;
# the web app picks gujrera_ahmedabad_gandhinagar.db automatically when it exists.
param([int]$Minutes = 10, [string[]]$Districts = @("Ahmedabad", "Gandhinagar"))
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
Set-Location (Join-Path $root "gujrera_scraper")
$Host.UI.RawUI.WindowTitle = "RERA data refresher"

while ($true) {
    & $py cli.py refresh-app --districts $Districts --every $Minutes
    if ($LASTEXITCODE -eq 2) { Write-Host "A refresher is already running in another window."; break }
    Write-Host "Refresher stopped (exit $LASTEXITCODE). Restarting in 30 seconds..."
    Start-Sleep -Seconds 30
}
