# Starts everything, each in its own window:
#   1. crawler        (resumes the GujRERA crawl)
#   2. data refresher (pushes new data to the web app every 10 minutes)
#   3. web app        (http://localhost:8600)
# Safe to run twice: the crawler and refresher refuse to start a second copy.
$here = $PSScriptRoot
foreach ($script in "run_crawler.ps1", "run_refresher.ps1", "run_webapp.ps1") {
    Start-Process powershell -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-File", "`"$(Join-Path $here $script)`""
}
Write-Host "Started crawler, data refresher and web app. Open http://localhost:8600"
