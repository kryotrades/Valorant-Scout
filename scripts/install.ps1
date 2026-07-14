. (Join-Path $PSScriptRoot "common.ps1")

Write-Host ""
Write-Host "  VALORANT SCOUT - SETUP" -ForegroundColor Red
Write-Host "  This installs everything you need. It only has to run once." -ForegroundColor DarkGray

try {
    $py = Ensure-Python
    Ensure-Venv $py
    Install-PyDeps
    if ($HasFrontend) {
        Ensure-Node
        Install-NodeDeps
        Build-Frontend
    } else {
        Note "Slim install - no local frontend bundled; the app uses the hosted dashboard."
    }

    $regions = @(
        @{ n = "North America"; k = "na" },
        @{ n = "Europe";        k = "eu" },
        @{ n = "Asia Pacific";  k = "ap" },
        @{ n = "Korea";         k = "kr" },
        @{ n = "Latin America"; k = "latam" },
        @{ n = "Brazil";        k = "br" }
    )
    Step "Select your region (so we talk to the right Riot servers):"
    for ($i = 0; $i -lt $regions.Count; $i++) {
        Write-Host ("     [{0}] {1}  ({2})" -f ($i + 1), $regions[$i].n, $regions[$i].k)
    }
    $choice = $null
    $blanks = 0
    while (-not $choice) {
        $r = (Read-Host "  Enter a number (1-$($regions.Count))").Trim()
        if ($r -match '^\d+$' -and [int]$r -ge 1 -and [int]$r -le $regions.Count) {
            $choice = $regions[[int]$r - 1]
        } elseif (-not $r) {
            if (++$blanks -ge 5) { throw "No region was selected. Run install.bat again and enter a number (1-$($regions.Count))." }
        } else { $blanks = 0; Warn2 "Please enter a number between 1 and $($regions.Count)." }
    }
    Set-Region $choice.k
    Ok "Region saved: $($choice.n) ($($choice.k))."

    New-DesktopShortcut
    Save-Markers $choice.k

    Write-Host ""
    Ok "Setup complete!"
    Write-Host "  Launch the app any time with start.bat (or the Valorant Scout desktop shortcut)." -ForegroundColor Green
} catch {
    Write-Host ""
    Fail "Setup failed: $($_.Exception.Message)"
    Write-Host "  Fix the issue above and run install.bat again." -ForegroundColor Yellow
    exit 1
}

