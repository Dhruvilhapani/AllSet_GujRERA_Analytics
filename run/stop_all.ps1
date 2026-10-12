# Stops the crawler, data refresher and web app (and their restart loops).
# The crawl keeps its checkpoint: run start_all.ps1 later to continue where it stopped.
$targets = Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -match "python") -and ($_.CommandLine -match "cli\.py (crawl|refresh-app)|uvicorn webapp\.server\.app")
}
$loops = Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -match "powershell") -and ($_.CommandLine -match "run_(crawler|refresher|webapp)\.ps1")
}
foreach ($p in @($loops) + @($targets)) {
    if ($p) {
        Write-Host "Stopping $($p.ProcessId): $($p.CommandLine.Substring(0, [Math]::Min(90, $p.CommandLine.Length)))"
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
}
Write-Host "Stopped."
