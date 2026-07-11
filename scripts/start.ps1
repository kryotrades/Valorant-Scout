. (Join-Path $PSScriptRoot "common.ps1")

if (-not (Is-Installed)) {
    Write-Host ""
    Fail "Valorant Scout isn't set up on this PC yet."
    Write-Host "  Please run install.bat first (one-time setup), then use start.bat." -ForegroundColor Yellow
    exit 1
}

try { Invoke-ScoutUpdate | Out-Null } catch { Warn2 "Update check skipped: $($_.Exception.Message)" }

Step "Launching Valorant Scout ..."
Note "Close the Valorant Scout scoreboard window to stop the app."
& $VenvPy (Join-Path $Root "run.py") --prod

