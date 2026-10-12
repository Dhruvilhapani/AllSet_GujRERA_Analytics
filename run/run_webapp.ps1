# Serves the web app (Dashboard + Developers) and its API on http://localhost:8600.
# Builds the frontend first if it has not been built yet. Restarts the server if it stops.
param([int]$Port = 8600)
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
$Host.UI.RawUI.WindowTitle = "RERA web app :$Port"

if (-not (Test-Path (Join-Path $root "webapp\frontend\dist\index.html"))) {
    Push-Location (Join-Path $root "webapp\frontend")
    npm install --no-audit --no-fund
    npm run build
    Pop-Location
}

Set-Location $root
while ($true) {
    Write-Host "Web app: http://localhost:$Port"
    & $py -m uvicorn webapp.server.app:app --host 127.0.0.1 --port $Port
    Write-Host "Web server stopped (exit $LASTEXITCODE). Restarting in 10 seconds..."
    Start-Sleep -Seconds 10
}
