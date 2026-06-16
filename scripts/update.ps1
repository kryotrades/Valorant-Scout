# update.ps1 — manual full update (run via UPDATE.bat); also called by start.ps1.
. (Join-Path $PSScriptRoot "common.ps1")

Write-Host ""
Write-Host "  VALORANT SCOUT - UPDATE" -ForegroundColor Red

if (-not (Is-Installed)) {
    Warn2 "Not set up yet - run install.bat first."
    exit 1
}

try {
    $did = Invoke-ScoutUpdate
    Write-Host ""
    if ($did) { Ok "Done. Launch with start.bat (or the desktop shortcut)." }
    else { Note "Nothing to update." }
} catch {
    Fail "Update failed: $($_.Exception.Message)"
    exit 1
}
