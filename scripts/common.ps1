$ErrorActionPreference = "Stop"

[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$Root     = Split-Path -Parent $PSScriptRoot
$Repo     = "kryotrades/valorant-scout"
$VenvDir  = Join-Path $Root ".venv"
$VenvPy   = Join-Path $VenvDir "Scripts\python.exe"
$ScoutDir = Join-Path $Root ".scout"
$EnvFile  = Join-Path $Root "backend\.env"

$HasFrontend = Test-Path (Join-Path $Root "frontend\package.json")

function Step($m) { Write-Host ""; Write-Host "==> $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  + $m" -ForegroundColor Green }
function Note($m) { Write-Host "  . $m" -ForegroundColor DarkGray }
function Warn2($m){ Write-Host "  ! $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "  x $m" -ForegroundColor Red }

function Has-Cmd($name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }

function Refresh-Path {

    $m = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $u = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = (@($m, $u) | Where-Object { $_ }) -join ";"
}

function Write-FileNoBom($path, $content) {
    $enc = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($path, $content, $enc)
}

function Parse-Ver($s) {
    if (-not $s) { return $null }
    $s = ($s.Trim()) -replace '^[vV]', ''
    try { return [version]$s } catch { return $null }
}

function Get-LocalVersion {
    $f = Join-Path $Root "VERSION"
    if (Test-Path $f) { return ((Get-Content $f -Raw).Trim()) }
    return "0.0.0"
}

function HashOf($rel) {
    $p = Join-Path $Root $rel
    if (Test-Path $p) { return (Get-FileHash -Algorithm SHA256 -Path $p).Hash }
    return ""
}

function Find-Python {

    $cands = @(
        @{ Exe = "py";      Args = @("-3") },
        @{ Exe = "python";  Args = @() },
        @{ Exe = "python3"; Args = @() }
    )
    foreach ($c in $cands) {
        if (Has-Cmd $c.Exe) {
            try {
                $v = & $c.Exe @($c.Args + @("-c", "import sys;print('%d.%d'%sys.version_info[:2])")) 2>$null
                if ($v -and ([version]$v -ge [version]"3.10")) { return $c }
            } catch {}
        }
    }
    return $null
}

function Find-Node {
    if (Has-Cmd "node") {
        try {
            $v = (& node -v) -replace '^v', ''
            if ([version]$v -ge [version]"18.0") { return $true }
        } catch {}
    }
    return $false
}

function Install-Python {
    Step "Installing Python 3.12 ..."
    if (Has-Cmd "winget") {
        winget install -e --id Python.Python.3.12 --scope user --silent `
            --accept-source-agreements --accept-package-agreements
    } else {
        $url = "https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe"
        $tmp = Join-Path $env:TEMP "python-3.12.7-amd64.exe"
        Note "winget unavailable; downloading $url"
        Invoke-WebRequest -Uri $url -OutFile $tmp
        Start-Process -FilePath $tmp -Wait -ArgumentList `
            "/quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_launcher=1"
    }
    Refresh-Path
}

function Install-Node {
    Step "Installing Node.js LTS ..."
    if (Has-Cmd "winget") {
        winget install -e --id OpenJS.NodeJS.LTS --scope user --silent `
            --accept-source-agreements --accept-package-agreements
    } else {
        $url = "https://nodejs.org/dist/v20.17.0/node-v20.17.0-x64.msi"
        $tmp = Join-Path $env:TEMP "node-v20.17.0-x64.msi"
        Note "winget unavailable; downloading $url"
        Invoke-WebRequest -Uri $url -OutFile $tmp
        Start-Process -FilePath "msiexec.exe" -Wait -ArgumentList "/i `"$tmp`" /qn /norestart"
    }
    Refresh-Path
}

function Ensure-Python {
    $py = Find-Python
    if (-not $py) { Install-Python; $py = Find-Python }
    if (-not $py) { throw "Python 3.10+ could not be installed automatically. Install it from python.org and re-run install.bat." }
    Ok "Python ready."
    return $py
}

function Ensure-Node {
    if (-not (Find-Node)) { Install-Node }
    if (-not (Find-Node)) { throw "Node.js 18+ could not be installed automatically. Install it from nodejs.org and re-run install.bat." }
    Ok "Node.js ready."
}

function Ensure-Venv($py) {
    if (-not (Test-Path $VenvPy)) {
        Step "Creating Python virtual environment (.venv) ..."
        & $py.Exe @($py.Args + @("-m", "venv", $VenvDir))
    }
    if (-not (Test-Path $VenvPy)) { throw "Failed to create .venv" }
}

function Install-PyDeps {
    Step "Installing Python packages ..."
    & $VenvPy -m pip install --upgrade pip --quiet
    $req = Join-Path $Root "backend\requirements.txt"
    for ($i = 1; $i -le 3; $i++) {
        if ($i -gt 1) { Warn2 "pip install hiccup - retrying ($i/3) ..."; Start-Sleep -Seconds 3 }
        & $VenvPy -m pip install -r $req
        if ($LASTEXITCODE -eq 0) { Ok "Python packages installed."; return }
    }
    throw "pip install failed (check your internet connection, then run install.bat again)."
}

function Run-Npm([string[]]$npmArgs) {
    Push-Location (Join-Path $Root "frontend")
    try {

        cmd /c ("npm " + ($npmArgs -join " ") + " 2>&1") | Out-Host
        return $LASTEXITCODE
    } finally { Pop-Location }
}

function Install-NodeDeps {
    Step "Installing frontend packages (npm install) ..."

    $tries = @(
        @("install", "--no-fund", "--no-audit"),
        @("install", "--no-fund", "--no-audit"),
        @("install", "--no-fund", "--no-audit", "--legacy-peer-deps")
    )
    for ($i = 0; $i -lt $tries.Count; $i++) {
        if ($i -gt 0) { Warn2 "npm install hiccup - retrying ($($i + 1)/$($tries.Count)) ..."; Start-Sleep -Seconds 3 }
        if ((Run-Npm $tries[$i]) -eq 0) { Ok "Frontend packages installed."; return }
    }
    throw "npm install failed (check your internet connection, then run install.bat again)."
}

function Build-Frontend {
    Step "Building the website (npm run build) ... (first build can take a minute)"
    if ((Run-Npm @("run", "build")) -ne 0) { throw "npm run build failed" }
    Ok "Website built."
}

function Save-Markers($region) {
    if (-not (Test-Path $ScoutDir)) { New-Item -ItemType Directory -Path $ScoutDir | Out-Null }
    $installed = @{
        version   = (Get-LocalVersion)
        region    = $region
        installedAt = (Get-Date).ToString("o")
    } | ConvertTo-Json
    Write-FileNoBom (Join-Path $ScoutDir "installed.json") $installed
    Save-DepHashes
}

function Save-DepHashes {
    if (-not (Test-Path $ScoutDir)) { New-Item -ItemType Directory -Path $ScoutDir | Out-Null }
    $deps = @{
        requirements = (HashOf "backend\requirements.txt")
        packageLock  = (HashOf "frontend\package-lock.json")
    } | ConvertTo-Json
    Write-FileNoBom (Join-Path $ScoutDir "deps.json") $deps
}

function Is-Installed {
    return (Test-Path (Join-Path $ScoutDir "installed.json")) -and (Test-Path $VenvPy)
}

function Set-Region($region) {
    $lines = @()
    if (Test-Path $EnvFile) {
        $lines = Get-Content $EnvFile | Where-Object { $_ -notmatch '^\s*RIOT_REGION\s*=' }
    }
    $lines += "RIOT_REGION=$region"
    Write-FileNoBom $EnvFile (($lines -join "`r`n") + "`r`n")
}

function Get-SavedRegion {
    if (Test-Path $EnvFile) {
        $m = (Get-Content $EnvFile | Select-String -Pattern '^\s*RIOT_REGION\s*=\s*(.+)$')
        if ($m) { return $m.Matches[0].Groups[1].Value.Trim() }
    }
    return $null
}

function New-DesktopShortcut {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $lnk = Join-Path $desktop "Valorant Scout.lnk"
    if (Test-Path $lnk) { Note "Desktop shortcut already exists."; return }
    try {
        $ws = New-Object -ComObject WScript.Shell
        $sc = $ws.CreateShortcut($lnk)
        $sc.TargetPath = (Join-Path $Root "start.bat")
        $sc.WorkingDirectory = $Root
        $ico = Join-Path $Root "assets\valorant-scout.ico"
        if (Test-Path $ico) { $sc.IconLocation = $ico }
        $sc.Description = "Launch Valorant Scout"
        $sc.Save()
        Ok "Desktop shortcut created - you can drag it onto your taskbar to pin it."
    } catch { Warn2 "Couldn't create the desktop shortcut ($($_.Exception.Message))." }
}

function Get-LatestRelease {
    try {
        return Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" `
            -Headers @{ "User-Agent" = "valorant-scout" } -TimeoutSec 12
    } catch { return $null }
}

function Invoke-ScoutUpdate {
    Step "Checking for updates ..."
    $rel = Get-LatestRelease
    if (-not $rel) { Note "No update info (offline, or repo still private) - skipping."; return $false }

    $latest = Parse-Ver $rel.tag_name
    $local  = Parse-Ver (Get-LocalVersion)
    if (-not $latest) { Note "Couldn't read the release version - skipping."; return $false }
    if ($local -and ($latest -le $local)) { Ok "You're on the latest version (v$local)."; return $false }

    Step "Updating to $($rel.tag_name) (you have v$local) ..."
    $zipUrl = $rel.zipball_url
    if ($rel.assets) {
        $asset = $rel.assets | Where-Object { $_.name -like "*.zip" } | Select-Object -First 1
        if ($asset) { $zipUrl = $asset.browser_download_url }
    }

    $tmp = Join-Path $env:TEMP ("vs-update-" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $tmp | Out-Null
    $zip = Join-Path $tmp "release.zip"
    try {
        Invoke-WebRequest -Uri $zipUrl -OutFile $zip -Headers @{ "User-Agent" = "valorant-scout" }
        Expand-Archive -Path $zip -DestinationPath $tmp -Force
    } catch {
        Fail "Download/extract failed: $($_.Exception.Message)"
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
        return $false
    }

    $srcPath = $tmp
    if (-not (Test-Path (Join-Path $tmp "backend"))) {
        $top = Get-ChildItem -Path $tmp -Directory | Select-Object -First 1
        if ($top) { $srcPath = $top.FullName }
    }
    if (-not (Test-Path (Join-Path $srcPath "backend"))) {
        Fail "Couldn't find the app files in the download."
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
        return $false
    }

    $reqBefore  = HashOf "backend\requirements.txt"
    $lockBefore = HashOf "frontend\package-lock.json"

    Step "Applying update (your settings, data and installed packages are preserved) ..."
    $excludeDirs = @(
        (Join-Path $Root ".git"), $VenvDir,
        (Join-Path $Root "frontend\node_modules"),
        (Join-Path $Root "backend\data"), $ScoutDir
    )
    robocopy $srcPath $Root /E /XD @excludeDirs /XF $EnvFile /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) {
        Fail "Applying the update failed (robocopy $LASTEXITCODE)."
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
        return $false
    }
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue

    if ((HashOf "backend\requirements.txt") -ne $reqBefore) { Install-PyDeps }
    if ($HasFrontend) {
        if ((HashOf "frontend\package-lock.json") -ne $lockBefore) { Install-NodeDeps }
        Build-Frontend
    }
    Save-DepHashes
    Ok "Update complete - now on $($rel.tag_name)."
    return $true
}

