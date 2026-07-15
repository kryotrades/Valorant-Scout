. (Join-Path $PSScriptRoot "common.ps1")

# This window may briefly appear during startup (Windows 11 does not always
# honor a hidden launch), so say what is happening until the scoreboard opens.
Write-Host ""
Write-Host "  Starting Valorant Scout..." -ForegroundColor Cyan
Write-Host "  This window closes on its own once the scoreboard opens." -ForegroundColor DarkGray

# Startup NEVER installs anything. It validates the installation fast (offline),
# then launches. Anything broken points the user at install.bat to repair.

Write-ScoutLog -Log launcher -Message "startup requested (v$(Get-LocalVersion))"

$markers = Test-Markers
if (-not $markers.Ok) {
    Write-ScoutLog -Log launcher -Level ERROR -Code VS-DEPS-001 -Message "startup blocked: $($markers.Reason)"
    Show-FatalDialog "Valorant Scout can't start: $($markers.Reason).`n`nRun install.bat to repair (your settings and data are kept)." "launcher"
    exit 1
}

$venv = Test-Venv
if (-not $venv.Ok) {
    $code = "VS-DEPS-001"
    foreach ($r in $venv.Reasons) {
        if ($r -match 'python|venv') { $code = "VS-PY-001" }
        Write-ScoutLog -Log launcher -Level ERROR -Code $code -Message "startup blocked: $r"
    }
    Show-FatalDialog "Valorant Scout can't start: $($venv.Reasons[0]).`n`nRun install.bat to repair (your settings and data are kept)." "launcher"
    exit 1
}

# Bounded update CHECK — notify only, never applies. Offline-safe (8s cap).
try {
    $tag = Test-UpdateAvailable
    if ($tag) {
        Write-ScoutLog -Log launcher -Message "update available: $tag (run UPDATE.bat to apply)"
        $env:VS_UPDATE_AVAILABLE = $tag
    }
} catch { }

# Single-instance: if a previous copy is still running, close it and take over
# (relaunching replaces the old instance instead of refusing to start).
Stop-RunningApp "launcher" | Out-Null

Step "Launching Valorant Scout ..."
Note "Close the Valorant Scout scoreboard window to stop the app."
& $VenvPy (Join-Path $Root "run.py") --prod
$code = $LASTEXITCODE
Write-ScoutLog -Log launcher -Message "run.py exited with code $code"
exit $code
