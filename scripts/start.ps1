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

# Auto-update (always on — not a toggle): every launch checks GitHub for a newer
# release and, if there is one, applies it as a verified transaction BEFORE
# launching, so users are always on the latest version. It degrades safely — a
# failed check (offline, GitHub down) or a failed update (rolled back) just falls
# through to launching the current version, which then retries next launch. The
# GitHub check is bounded (8s); the update itself only runs when one exists.
# Skipped on a developer checkout (.git present): there, updates come from git,
# and update.ps1 refuses to overwrite a checkout anyway.
if (-not (Test-Path (Join-Path $Root ".git"))) {
    try {
        $tag = Test-UpdateAvailable
        if ($tag) {
            Write-ScoutLog -Log launcher -Message "update $tag available - applying before launch"
            Step "A new version ($tag) is available - updating before launch ..."
            & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "update.ps1")
            if ($LASTEXITCODE -eq 0) {
                Write-ScoutLog -Log launcher -Message "auto-update finished - now on v$(Get-LocalVersion)"
                Ok "Updated to the latest version (v$(Get-LocalVersion))."
            } else {
                # Rolled back or couldn't apply; launch anyway and let the UI nudge.
                $env:VS_UPDATE_AVAILABLE = $tag
                Write-ScoutLog -Log launcher -Level WARN -Message "auto-update to $tag failed (rc=$LASTEXITCODE); launching current version"
                Warn2 "Couldn't install the update automatically - starting your current version. It'll try again next launch."
            }
        }
    } catch {
        Write-ScoutLog -Log launcher -Message "update check/apply skipped: $($_.Exception.Message)"
    }
}

# Single-instance: if a previous copy is still running, close it and take over
# (relaunching replaces the old instance instead of refusing to start).
Stop-RunningApp "launcher" | Out-Null

Step "Launching Valorant Scout ..."
Note "Close the Valorant Scout scoreboard window to stop the app."
& $VenvPy (Join-Path $Root "run.py") --prod
$code = $LASTEXITCODE
Write-ScoutLog -Log launcher -Message "run.py exited with code $code"
exit $code
