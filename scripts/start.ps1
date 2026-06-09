# start.ps1 — everyday launcher for Valorant Scout (run via start.bat).
. (Join-Path $PSScriptRoot "common.ps1")

if (-not (Is-Installed)) {
    Write-Host ""
    Fail "Valorant Scout isn't set up on this PC yet."
    Write-Host "  Please run install.bat first (one-time setup), then use start.bat." -ForegroundColor Yellow
    exit 1
}

# Best-effort auto-update; never block launch if it can't reach GitHub.
try { Invoke-ScoutUpdate | Out-Null } catch { Warn2 "Update check skipped: $($_.Exception.Message)" }

Step "Launching Valorant Scout ..."
Note "Keep this window open while you play; close it to stop the app."
& $VenvPy (Join-Path $Root "run.py") --prod
