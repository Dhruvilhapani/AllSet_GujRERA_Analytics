# Runs the GujRERA crawler and keeps it running.
# - Resumes the unfinished crawl from its checkpoint (nothing is fetched twice).
# - Internet drops / laptop sleep: the crawler pauses and continues by itself.
# - If the process crashes it is restarted after 30 s. Ends when the crawl is finished.
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
Set-Location (Join-Path $root "gujrera_scraper")
$Host.UI.RawUI.WindowTitle = "RERA crawler"

while ($true) {
    & $py cli.py crawl --resume
    $code = $LASTEXITCODE
    if ($code -eq 0) { Write-Host "Crawl finished (or nothing left to resume)."; break }
    if ($code -eq 2) { Write-Host "A crawler is already running in another window."; break }
    Write-Host "Crawler stopped unexpectedly (exit $code). Restarting in 30 seconds..."
    Start-Sleep -Seconds 30
}
