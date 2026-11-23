. (Join-Path $PSScriptRoot "common.ps1")










$Script:PhaseTotal = 4
function Show-Phase([int]$step, [string]$text) {
    $width = 22
    $filled = [int][Math]::Round($width * ($step / $Script:PhaseTotal))
    if ($filled -gt $width) { $filled = $width }


    Write-Host ("`r   " + ("$([char]0x2588)" * $filled)) -NoNewline -ForegroundColor Red
    Write-Host ("$([char]0x2591)" * ($width - $filled)) -NoNewline -ForegroundColor DarkGray
    Write-Host ("  $step/$Script:PhaseTotal  $text").PadRight(46) -NoNewline -ForegroundColor Gray
}
function Finish-Progress([string]$text) {
    Show-Phase $Script:PhaseTotal $text
    Write-Host ""
}

Show-Banner ""
Write-Host ""

Write-ScoutLog -Log launcher -Message "startup requested (v$(Get-LocalVersion))"


Show-Phase 1 "Checking your installation..."


if (Test-Path $UpdateStateFile) {
    Write-Host ""
    $maintenanceMutex = $null
    $appMutex = $null
    try {
        $maintenanceMutex = New-ScoutMutex "Maintenance" "an install/update is still running. Wait for it to finish, then start again"
        Stop-RunningApp "launcher" | Out-Null
        $appMutex = New-ScoutMutex "App" "Valorant Scout is still running and couldn't be closed automatically"
        Resume-InterruptedUpdate | Out-Null
    } catch {
        Write-ScoutLog -Log launcher -Level ERROR -Code VS-UPDATE-001 -Message "startup blocked: interrupted update: $($_.Exception.Message)"
        Show-FatalDialog "Valorant Scout can't start: $($_.Exception.Message)" "launcher"
        exit 1
    } finally {
        Close-ScoutMutex $appMutex
        Close-ScoutMutex $maintenanceMutex
    }
}
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
            Finish-Progress "A new version is out - updating first."
            Write-ScoutLog -Log launcher -Message "update $tag available - applying before launch"
            $env:VS_FROM_LAUNCHER = "1"
            & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "update.ps1")
            $env:VS_FROM_LAUNCHER = $null
            if ($LASTEXITCODE -eq 0) {
                Write-ScoutLog -Log launcher -Message "auto-update finished - now on v$(Get-LocalVersion)"
                Write-Host ""
            } else {




                if ($LASTEXITCODE -ne 2 -and (Test-Path $UpdateStateFile)) {
                    Write-Host ""
                    Write-ScoutLog -Log launcher -Level ERROR -Code VS-UPDATE-001 -Message "startup blocked: auto-update to $tag failed and its rollback did not finish"
                    Show-FatalDialog "Valorant Scout can't start: the update to $tag failed and couldn't be rolled back automatically.`n`nClose VALORANT if it is open and start Valorant Scout again to retry the rollback. If that keeps failing, re-extract the last release over this folder, delete .scout\update-state.json, then run install.bat." "update"
                    exit 1
                }

                $env:VS_UPDATE_AVAILABLE = $tag
                Write-ScoutLog -Log launcher -Level WARN -Message "auto-update to $tag failed (rc=$LASTEXITCODE); launching current version"
                Warn2 "Couldn't install the update - starting your current version instead."
                Write-Host ""
            }
        }
    } catch {
        Write-ScoutLog -Log launcher -Message "update check/apply skipped: $($_.Exception.Message)"
    }
}


Show-Phase 3 "Starting Valorant Scout..."
$tookOver = Stop-RunningApp "launcher"








$env:VS_PREVALIDATED = "1"
$env:VS_ATTACHED_CLI = "1"






if (-not $tookOver -and (Test-Path (Join-Path $ScoutDir "hide-window"))) {
    Finish-Progress "Running in the background - opening the dashboard..."
    Write-ScoutLog -Log launcher -Message "starting run.py windowless (background mode)"
    Start-Process -FilePath $VenvPy -WorkingDirectory $Root -WindowStyle Hidden `
        -ArgumentList @(('"' + (Join-Path $Root "run.py") + '"'), "--prod", "--no-cli")
    Start-Sleep -Seconds 2
    exit 0
}
Finish-Progress "Opening the scoreboard..."
Write-ScoutLog -Log launcher -Message "handing this console to run.py (attached single-window mode)"
& $VenvPy (Join-Path $Root "run.py") --prod
$code = $LASTEXITCODE
Write-ScoutLog -Log launcher -Message "run.py exited with code $code"
exit $code
