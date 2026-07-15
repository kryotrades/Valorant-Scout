. (Join-Path $PSScriptRoot "common.ps1")

# This console is VISIBLE (start.bat runs us directly): show a clean, branded
# progress display and keep the internals in launcher.log. Startup NEVER
# installs anything - it validates fast (offline), auto-applies any pending
# update, then hands off to run.py detached+hidden and closes itself.

# ---- friendly one-line progress bar ----------------------------------------
# CP437-safe glyphs only (block chars exist in the OEM codepage), PS 5.1-safe
# syntax only. One line, redrawn in place with a carriage return.
$Script:PhaseTotal = 4
function Show-Phase([int]$step, [string]$text) {
    $width = 22
    $filled = [int][Math]::Round($width * ($step / $Script:PhaseTotal))
    if ($filled -gt $width) { $filled = $width }
    $bar = ("$([char]0x2588)" * $filled) + ("$([char]0x2591)" * ($width - $filled))
    $line = "  [$bar]  $text"
    # pad so a shorter message fully overwrites the previous one
    Write-Host ("`r" + $line.PadRight(70)) -NoNewline -ForegroundColor Gray
}
function Finish-Progress([string]$text) {
    Show-Phase $Script:PhaseTotal $text
    Write-Host ""
}

Write-Host ""
Write-Host "  VALORANT " -ForegroundColor Red -NoNewline
Write-Host "SCOUT" -ForegroundColor White
Write-Host ""

Write-ScoutLog -Log launcher -Message "startup requested (v$(Get-LocalVersion))"

# ---- 1/4 validate the installation (offline, fast) --------------------------
Show-Phase 1 "Checking your installation..."
$markers = Test-Markers
if (-not $markers.Ok) {
    Write-Host ""
    Write-ScoutLog -Log launcher -Level ERROR -Code VS-DEPS-001 -Message "startup blocked: $($markers.Reason)"
    Show-FatalDialog "Valorant Scout can't start: $($markers.Reason).`n`nRun install.bat to repair (your settings and data are kept)." "launcher"
    exit 1
}
$venv = Test-Venv
if (-not $venv.Ok) {
    Write-Host ""
    $code = "VS-DEPS-001"
    foreach ($r in $venv.Reasons) {
        if ($r -match 'python|venv') { $code = "VS-PY-001" }
        Write-ScoutLog -Log launcher -Level ERROR -Code $code -Message "startup blocked: $r"
    }
    Show-FatalDialog "Valorant Scout can't start: $($venv.Reasons[0]).`n`nRun install.bat to repair (your settings and data are kept)." "launcher"
    exit 1
}

# ---- 2/4 auto-update (always on) --------------------------------------------
# Every launch checks GitHub for a newer release and applies it BEFORE launching.
# Degrades safely: a failed check (offline) or failed update (rolled back) just
# launches the current version, which retries next launch. Skipped on a
# developer checkout (.git) - updates come from git there.
Show-Phase 2 "Checking for updates..."
if (-not (Test-Path (Join-Path $Root ".git"))) {
    try {
        $tag = Test-UpdateAvailable
        if ($tag) {
            Write-Host ""
            Write-Host ""
            Write-Host "  A new version ($tag) is available - updating now." -ForegroundColor Cyan
            Write-Host "  This takes a minute; your settings and match data are kept." -ForegroundColor DarkGray
            Write-Host ""
            Write-ScoutLog -Log launcher -Message "update $tag available - applying before launch"
            & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "update.ps1")
            if ($LASTEXITCODE -eq 0) {
                Write-ScoutLog -Log launcher -Message "auto-update finished - now on v$(Get-LocalVersion)"
                Write-Host "  Updated to v$(Get-LocalVersion)." -ForegroundColor Green
                Write-Host ""
            } else {
                # Rolled back or couldn't apply; launch anyway and let the UI nudge.
                $env:VS_UPDATE_AVAILABLE = $tag
                Write-ScoutLog -Log launcher -Level WARN -Message "auto-update to $tag failed (rc=$LASTEXITCODE); launching current version"
                Write-Host "  Couldn't install the update - starting your current version." -ForegroundColor Yellow
                Write-Host ""
            }
        }
    } catch {
        Write-ScoutLog -Log launcher -Message "update check/apply skipped: $($_.Exception.Message)"
    }
}

# ---- 3/4 take over from any previous instance --------------------------------
Show-Phase 3 "Starting Valorant Scout..."
Stop-RunningApp "launcher" | Out-Null

# ---- 4/4 hand off to run.py (detached + hidden) ------------------------------
# run.py owns the app lifecycle from here (instance mutex, crash dialogs, the
# scoreboard window). Detached so this console can close; hidden because the
# scoreboard IS the app's face. VS_PREVALIDATED skips run.py re-running the
# dependency probes Test-Venv just ran.
$env:VS_PREVALIDATED = "1"
$stateFile = Join-Path $ScoutDir "runtime-state.json"
$before = $null
if (Test-Path $stateFile) { $before = (Get-Item $stateFile).LastWriteTimeUtc }
Start-Process -FilePath $VenvPy -ArgumentList @("`"$(Join-Path $Root 'run.py')`"", "--prod") `
    -WorkingDirectory $Root -WindowStyle Hidden
Write-ScoutLog -Log launcher -Message "run.py launched detached"

# Hold the console just until run.py signs in (it writes runtime-state.json
# early, right around when the scoreboard window opens).
$deadline = (Get-Date).AddSeconds(12)
while ((Get-Date) -lt $deadline) {
    if (Test-Path $stateFile) {
        $now = (Get-Item $stateFile).LastWriteTimeUtc
        if ($null -eq $before -or $now -gt $before) { break }
    }
    Start-Sleep -Milliseconds 250
}
Finish-Progress "Scoreboard opening - this window closes itself."
Start-Sleep -Milliseconds 900
exit 0
