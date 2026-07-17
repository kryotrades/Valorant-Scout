param(
    [switch]$Bundle,           # also write a sanitized support ZIP to the Desktop
    [string]$BundlePath = ""   # override the ZIP location (tests)
)

# Diagnostics must work even when .venv is broken — everything here is plain
# PowerShell; Python is only probed, never required.

. (Join-Path $PSScriptRoot "common.ps1")

$report = New-Object System.Collections.Generic.List[string]
function DiagLine([string]$line) { $report.Add($line); Write-Host "  $line" }
function RSection([string]$t) { $report.Add(""); $report.Add("== $t =="); Write-Host ""; Write-Host "==> $t" -ForegroundColor Cyan }

$exitCode = 0
function Bad([string]$line) { $script:exitCode = 1; DiagLine "[X] $line" }
function Good([string]$line) { DiagLine "[OK] $line" }

Write-Host ""
Write-Host "  VALORANT SCOUT - DIAGNOSTICS" -ForegroundColor Red

# ---- App ------------------------------------------------------------------
RSection "App"
DiagLine "version: $(Get-LocalVersion)"
try {
    $mf = Get-RuntimeManifest
    DiagLine "channel: $($mf.app.channel), supported runtime: CPython $($mf.python.version) $($mf.python.arch), protocol: $($mf.protocol.version)"
    if ($mf.app.version -ne (Get-LocalVersion)) { Bad "runtime.json version ($($mf.app.version)) != VERSION ($(Get-LocalVersion))" }
} catch { Bad "runtime.json: $($_.Exception.Message)" }
$relMf = Join-Path $Root "release-manifest.json"
if (Test-Path $relMf) {
    try { DiagLine "release commit: $((Get-Content $relMf -Raw | ConvertFrom-Json).commit)" } catch { }
} else { DiagLine "release commit: n/a (developer or pre-manifest tree)" }

# ---- Windows / shells -------------------------------------------------------
RSection "Windows"
$os = [Environment]::OSVersion.Version
DiagLine "windows: $($os.Major).$($os.Minor) build $($os.Build), 64-bit OS: $([Environment]::Is64BitOperatingSystem)"
$arch = $env:PROCESSOR_ARCHITECTURE
if ($env:PROCESSOR_ARCHITEW6432) { $arch = $env:PROCESSOR_ARCHITEW6432 }
if ($arch -eq "AMD64") { Good "architecture: x64" } else { Bad "architecture: $arch (only x64 is supported)" }
DiagLine "windows powershell: $($PSVersionTable.PSVersion)"
$pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
if ($pwsh) {
    try { DiagLine "powershell 7: $((& pwsh -NoProfile -Command '$PSVersionTable.PSVersion.ToString()' 2>$null))" } catch { DiagLine "powershell 7: present" }
} else { DiagLine "powershell 7: not installed (fine - not required)" }

# ---- Install path -----------------------------------------------------------
RSection "Install path"
$special = @()
if ($Root -match '\s') { $special += "spaces" }
if ($Root -match "['&()%!]") { $special += "special characters" }
if ($Root -match '[^\x00-\x7F]') { $special += "non-ASCII" }
if ($Root.Length -gt 180) { $special += "very long path ($($Root.Length) chars)" }
$cat = "normal"
if ($Root -match 'OneDrive') { $cat = "OneDrive-synced" }
elseif ($Root -match '\\Downloads\\') { $cat = "Downloads" }
elseif ($Root -match '\.zip[\\/]|\\Temp1_') { $cat = "INSIDE A ZIP (must extract first)" }
DiagLine "path category: $cat$(if ($special) { ' (' + ($special -join ', ') + ')' })"
if ($cat -like "INSIDE*") { Bad "running from inside a ZIP" }
try {
    $t = Join-Path $Root (".vs-diag-" + [Guid]::NewGuid().ToString("N"))
    [System.IO.File]::WriteAllText($t, "x"); Remove-Item $t -Force
    Good "install folder writable"
} catch { Bad "install folder NOT writable" }
try {
    $t = Join-Path $env:TEMP (".vs-diag-" + [Guid]::NewGuid().ToString("N"))
    [System.IO.File]::WriteAllText($t, "x"); Remove-Item $t -Force
    Good "TEMP writable"
} catch { Bad "TEMP NOT writable" }
try {
    $drive = (Get-Item $Root).PSDrive
    DiagLine ("free disk space: {0:N1} GB" -f ($drive.Free / 1GB))
    if ($drive.Free -lt 2GB) { Bad "less than 2 GB free" }
} catch { }

# ---- Python / venv ----------------------------------------------------------
RSection "Python"
try {
    $py = Find-ExactPython
    if ($py) {
        $id = Get-PythonIdentity $py.Exe $py.Args
        Good "supported python found: $($id.executable) ($($id.version) $($id.machine) $($id.bits)-bit)"
    } else {
        Bad "no CPython $((Get-RuntimeManifest).python.version) x64 found on this PC (install.bat installs it)"
    }
} catch { Bad "python discovery failed: $($_.Exception.Message)" }

if (Test-Path $VenvPy) {
    $venv = Test-Venv
    if ($venv.Ok) { Good ".venv healthy (python, pip $(Get-VenvPipVersion), packages, imports)" }
    else { foreach ($r in $venv.Reasons) { Bad ".venv: $r" } }
} else {
    Bad ".venv missing - run install.bat"
}

$markers = Test-Markers
if ($markers.Ok) { Good "install markers valid (schema $MarkerSchemaVersion)" }
else { Bad "install markers: $($markers.Reason)" }

# ---- Ports ------------------------------------------------------------------
RSection "Ports"
function Get-PortOwner([int]$port) {
    # Get-NetTCPConnection over netstat: netstat's state column is localized
    # ("ABHÖREN", "À L'ÉCOUTE"), so matching "LISTENING" finds nothing on
    # non-English Windows — and its stderr would abort under the global EAP=Stop.
    $conns = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
    foreach ($c in $conns) {
        $portPid = $c.OwningProcess
        try {
            $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$portPid" -ErrorAction Stop
            $exe = $proc.ExecutablePath
            $rootLower = $Root.ToLower()
            # Boundary match: a bare StartsWith/Contains also claims sibling folders
            # like "...ValorantScout-old" as ours. Require exact equality or the
            # root followed by a path separator.
            if ($exe) {
                $exeLower = $exe.ToLower()
                if ($exeLower -eq $rootLower -or $exeLower.StartsWith($rootLower + '\')) { return "ours (Valorant Scout, PID $portPid)" }
            }
            if ($proc.CommandLine) {
                $cmdLower = $proc.CommandLine.ToLower()
                if ($cmdLower -eq $rootLower -or $cmdLower.Contains($rootLower + '\')) { return "ours (Valorant Scout, PID $portPid)" }
            }
            return "foreign: $(Split-Path -Leaf ($exe + '')) (PID $portPid)"
        } catch { return "unknown process (PID $portPid)" }
    }
    return "free"
}
$backendPort = 5000; $wsPort = 7878
if (Test-Path $EnvFile) {
    $bp = Get-Content $EnvFile -Encoding UTF8 | Select-String '^\s*BACKEND_PORT\s*=\s*(\d+)'
    if ($bp) { $backendPort = [int]$bp.Matches[0].Groups[1].Value }
    $wp = Get-Content $EnvFile -Encoding UTF8 | Select-String '^\s*WS_PORT\s*=\s*(\d+)'
    if ($wp) { $wsPort = [int]$wp.Matches[0].Groups[1].Value }
}
$runtimeState = Join-Path $ScoutDir "runtime-state.json"
if (Test-Path $runtimeState) {
    try {
        $rs = Get-Content $runtimeState -Raw -Encoding UTF8 | ConvertFrom-Json
        $rp = Get-CimInstance Win32_Process -Filter "ProcessId=$($rs.pid)" -ErrorAction Stop
        $identity = (($rp.ExecutablePath + " " + $rp.CommandLine) + "").ToLowerInvariant()
        $rootLower = $Root.ToLowerInvariant()
        # Boundary match so a sibling folder ("...-old") can't masquerade as ours.
        if ($identity -eq $rootLower -or $identity.Contains($rootLower + '\')) {
            $backendPort = [int]$rs.backendPort
            $wsPort = [int]$rs.wsPort
            DiagLine "active launcher selected backend=$backendPort, websocket=$wsPort"
        } else {
            DiagLine "runtime-state.json is stale (PID now belongs to another program)"
        }
    } catch { DiagLine "runtime-state.json is stale (launcher is not running)" }
}
DiagLine "backend port $backendPort`: $(Get-PortOwner $backendPort)"
DiagLine "websocket port $wsPort`: $(Get-PortOwner $wsPort)"

# ---- Running app health -----------------------------------------------------
RSection "Running app"
try {
    $h = Invoke-RestMethod -Uri "http://127.0.0.1:$backendPort/api/health" -TimeoutSec 3
    if ($h.service -ne "valorant-scout") {
        Bad "port $backendPort answered, but it is not Valorant Scout"
    } elseif (-not $h.wsReady -or [int]$h.wsPort -ne $wsPort) {
        Bad "backend is up but its authenticated WebSocket self-check is not ready"
    } else {
        Good "backend + authenticated WebSocket healthy (v$($h.appVersion), protocol $($h.protocol), ws $($h.wsPort), client: $($h.clientStatus))"
    }
} catch { DiagLine "backend: not running (start it with start.bat)" }

# ---- Riot / Discord ---------------------------------------------------------
RSection "Riot & Discord"
$lockfile = Join-Path $env:LOCALAPPDATA "Riot Games\Riot Client\Config\lockfile"
if (Test-Path $lockfile) {
    try {
        $null = [System.IO.File]::ReadAllText($lockfile)
        Good "Riot lockfile present and readable (Riot Client is running)"
    } catch {
        # VS-RIOT-001: present but unreadable — permissions or an exotic lock.
        Bad "VS-RIOT-001 Riot lockfile exists but can't be read - try restarting the Riot Client"
    }
} else {
    DiagLine "Riot lockfile: not found (VALORANT/Riot Client not running - live data needs the game open)"
}
$discord = $false
foreach ($i in 0..3) { if (Test-Path "\\.\pipe\discord-ipc-$i") { $discord = $true; break } }
DiagLine "Discord desktop: $(if ($discord) { 'running (Rich Presence possible)' } else { 'not detected (Rich Presence off)' })"

# ---- Network ----------------------------------------------------------------
RSection "Network"
$frontend = "https://valorantscout.com"
if (Test-Path $EnvFile) {
    $fu = Get-Content $EnvFile -Encoding UTF8 | Select-String '^\s*FRONTEND_URL\s*=\s*(\S+)'
    if ($fu) { $frontend = $fu.Matches[0].Groups[1].Value }
}
try {
    $r = Invoke-WebRequest -Uri $frontend -UseBasicParsing -TimeoutSec 8 -Method Head
    Good "hosted dashboard reachable ($frontend)"
} catch { Bad "hosted dashboard NOT reachable ($frontend): $($_.Exception.Message)" }
try {
    $null = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" `
        -Headers @{ "User-Agent" = "valorant-scout" } -TimeoutSec 8
    Good "update endpoint reachable"
} catch { DiagLine "update endpoint: not reachable (updates would be skipped)" }

# ---- Recent errors ----------------------------------------------------------
RSection "Recent errors (sanitized)"
$any = $false
foreach ($log in @("launcher", "install", "update", "backend", "backend-console", "websocket", "cli", "crash")) {
    $f = Join-Path $ScoutDir "$log.log"
    if (-not (Test-Path $f)) { continue }
    $errs = Get-Content $f -Encoding UTF8 -ErrorAction SilentlyContinue |
        Where-Object { $_ -match '\bERROR\b|\bVS-[A-Z]+-\d+\b|Traceback' } |
        Select-Object -Last 5
    foreach ($e in $errs) { $any = $true; DiagLine "[$log] $(Protect-ScoutText $e)" }
}
if (-not $any) { DiagLine "none found" }

# ---- Support bundle ---------------------------------------------------------
if ($Bundle) {
    RSection "Support bundle"
    $dest = $BundlePath
    if (-not $dest) {
        $dest = Join-Path ([Environment]::GetFolderPath("Desktop")) `
            ("valorant-scout-support-" + [DateTime]::UtcNow.ToString("yyyyMMdd-HHmmss") + ".zip")
    }
    $work = Join-Path $env:TEMP ("vs-bundle-" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $work | Out-Null
    try {
        # STRICT allowlist. Everything copied in is redacted line by line —
        # never raw log files, never .env, never backend/data.
        Write-FileNoBom (Join-Path $work "diagnose-report.txt") (($report | ForEach-Object { Protect-ScoutText $_ }) -join "`r`n")
        foreach ($rel in @("VERSION", "runtime.json", "release-manifest.json")) {
            $p = Join-Path $Root $rel
            if (Test-Path $p) { Copy-Item $p (Join-Path $work (Split-Path -Leaf $rel)) }
        }
        foreach ($name in @("installed.json", "deps.json")) {
            $p = Join-Path $ScoutDir $name
            if (Test-Path $p) {
                Write-FileNoBom (Join-Path $work $name) (Protect-ScoutText (Get-Content $p -Raw -Encoding UTF8))
            }
        }
        foreach ($log in @("launcher", "install", "update", "backend", "backend-console", "websocket", "cli", "crash")) {
            $f = Join-Path $ScoutDir "$log.log"
            if (Test-Path $f) {
                $lines = Get-Content $f -Encoding UTF8 | Select-Object -Last 400 | ForEach-Object { Protect-ScoutText $_ }
                Write-FileNoBom (Join-Path $work "$log.log") ($lines -join "`r`n")
            }
        }
        if (Test-Path $dest) { Remove-Item -Force $dest }
        Compress-Archive -Path (Join-Path $work "*") -DestinationPath $dest
        Good "support bundle written: $dest"
    } finally {
        Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
    }
}

Write-Host ""
if ($exitCode -eq 0) { Ok "Diagnostics finished - no blocking problems found." }
else { Warn2 "Diagnostics finished - fix the [X] items above (install.bat repairs most of them)." }
exit $exitCode
