. (Join-Path $PSScriptRoot "common.ps1")










$Script:PhaseTotal = 4
function Show-Phase([int]$step, [string]$text) {
    $width = 22
    $filled = [int][Math]::Round($width * ($step / $Script:PhaseTotal))
    if ($filled -gt $width) { $filled = $width }
    $bar = ("$([char]0x2588)" * $filled) + ("$([char]0x2591)" * ($width - $filled))
    $line = "  [$bar]  $text"

    Write-Host ("`r" + $line.PadRight(70)) -NoNewline -ForegroundColor Gray
}
function Complete-Progress([string]$text) {
    Show-Phase $Script:PhaseTotal $text
    Write-Host ""
}

Write-Host ""
Write-Host "  VALORANT " -ForegroundColor Red -NoNewline
Write-Host "SCOUT" -ForegroundColor White
Write-Host ""

Write-ScoutLog -Log launcher -Message "startup requested (v$(Get-LocalVersion))"


Show-Phase 1 "Checking your installation..."
$markers = Test-Markers
if (-not $markers.Ok) {
    Write-Host ""
    Write-ScoutLog -Log launcher -Level ERROR -Code VS-DEPS-001 -Message "startup blocked: $($markers.Reason)"
    Show-FatalDialog "Valorant Scout can't start: $($markers.Reason).`n`nRun install.bat to repair (your settings and data are kept)." "launcher"
    exit 1
}
$venv = Test-Venv -Quick
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
            }
            else {

                $env:VS_UPDATE_AVAILABLE = $tag
                Write-ScoutLog -Log launcher -Level WARN -Message "auto-update to $tag failed (rc=$LASTEXITCODE); launching current version"
                Write-Host "  Couldn't install the update - starting your current version." -ForegroundColor Yellow
                Write-Host ""
            }
        }
    }
    catch {
        Write-ScoutLog -Log launcher -Message "update check/apply skipped: $($_.Exception.Message)"
    }
}


Show-Phase 3 "Starting Valorant Scout..."
Stop-RunningApp "launcher" | Out-Null







Complete-Progress "Opening the scoreboard..."
$env:VS_PREVALIDATED = "1"
$env:VS_ATTACHED_CLI = "1"
Write-ScoutLog -Log launcher -Message "handing this console to run.py (attached single-window mode)"
& $VenvPy (Join-Path $Root "run.py") --prod
$code = $LASTEXITCODE
Write-ScoutLog -Log launcher -Message "run.py exited with code $code"
exit $code
