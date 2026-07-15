$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # PS5.1 progress bar makes Invoke-WebRequest look frozen (and slows it)

[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$Root     = Split-Path -Parent $PSScriptRoot
$Repo     = "kryotrades/valorant-scout"
$VenvDir  = Join-Path $Root ".venv"
$VenvPy   = Join-Path $VenvDir "Scripts\python.exe"
$ScoutDir = Join-Path $Root ".scout"
$EnvFile  = Join-Path $Root "backend\.env"

$HasFrontend = Test-Path (Join-Path $Root "frontend\package.json")

$MarkerSchemaVersion = 2

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

# ---------------------------------------------------------------------------
# Runtime manifest (runtime.json) — the single machine-readable contract for
# what this release supports. Everything that checks a version reads it.
# ---------------------------------------------------------------------------
function Get-RuntimeManifest {
    # [IO.File]::Exists + -LiteralPath: Test-Path/-Path treat [ ] in the folder
    # name as wildcards and would report this file "missing" while it exists.
    $p = Join-Path $Root "runtime.json"
    if (-not [System.IO.File]::Exists($p)) { throw "runtime.json is missing — this checkout is incomplete. Re-download the release." }
    return (Get-Content -LiteralPath $p -Raw -Encoding UTF8 | ConvertFrom-Json)
}

function Get-LocalVersion {
    $f = Join-Path $Root "VERSION"
    if ([System.IO.File]::Exists($f)) { return ((Get-Content -LiteralPath $f -Raw).Trim()) }
    return "0.0.0"
}

# Semver-ish compare that understands prerelease tags ("1.1.2-rc.1").
# Returns -1 / 0 / 1 for a<b / a=b / a>b. Release beats its own prerelease.
function Compare-ScoutVersion([string]$a, [string]$b) {
    $a = ($a.Trim()) -replace '^[vV]', ''
    $b = ($b.Trim()) -replace '^[vV]', ''
    $pa = $a -split '-', 2; $pb = $b -split '-', 2
    $na = @($pa[0] -split '\.' | ForEach-Object { [int]$_ })
    $nb = @($pb[0] -split '\.' | ForEach-Object { [int]$_ })
    for ($i = 0; $i -lt 3; $i++) {
        $x = 0; $y = 0
        if ($i -lt $na.Count) { $x = $na[$i] }
        if ($i -lt $nb.Count) { $y = $nb[$i] }
        if ($x -lt $y) { return -1 }
        if ($x -gt $y) { return 1 }
    }
    $ra = ""; $rb = ""
    if ($pa.Count -gt 1) { $ra = $pa[1] }
    if ($pb.Count -gt 1) { $rb = $pb[1] }
    if (-not $ra -and -not $rb) { return 0 }
    if (-not $ra) { return 1 }   # release > prerelease
    if (-not $rb) { return -1 }
    if ($ra -eq $rb) { return 0 }
    $sa = $ra -split '\.'; $sb = $rb -split '\.'
    $n = [Math]::Max($sa.Count, $sb.Count)
    for ($i = 0; $i -lt $n; $i++) {
        if ($i -ge $sa.Count) { return -1 }
        if ($i -ge $sb.Count) { return 1 }
        $ia = 0; $ib = 0
        $aNum = [int]::TryParse($sa[$i], [ref]$ia)
        $bNum = [int]::TryParse($sb[$i], [ref]$ib)
        if ($aNum -and $bNum) {
            if ($ia -lt $ib) { return -1 }
            if ($ia -gt $ib) { return 1 }
        } else {
            $c = [string]::CompareOrdinal($sa[$i], $sb[$i])
            if ($c -lt 0) { return -1 }
            if ($c -gt 0) { return 1 }
        }
    }
    return 0
}

function HashOf($rel) {
    $p = Join-Path $Root $rel
    if (Test-Path $p) { return (Get-FileHash -Algorithm SHA256 -Path $p).Hash }
    return ""
}

# ---------------------------------------------------------------------------
# Logging — same format, rotation and redaction rules as backend/scoutlog.py.
# ---------------------------------------------------------------------------
$Script:RedactionRules = @(
    @{ Pattern = '([?&](?:s|t|token|key)=)[^&\s"'']+';                                              Replace = '$1[REDACTED]' },
    @{ Pattern = '\b([st]=)[A-Za-z0-9._~-]{8,}';                                                    Replace = '$1[REDACTED]' },
    @{ Pattern = '("(?:token|password|apiKey|api_key|key|secret|authorization)"\s*:\s*")[^"]+(")';  Replace = '$1[REDACTED]$2' },
    @{ Pattern = '\b(Basic|Bearer)\s+[A-Za-z0-9+/=_\-.]{8,}';                                       Replace = '$1 [REDACTED]' },
    @{ Pattern = '\b(password|token|secret|api_key|apikey|authorization)\s*[=:]\s*\S+';             Replace = '$1=[REDACTED]' },
    @{ Pattern = '\b[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}:[A-Za-z0-9_\-]{16,}\b';                  Replace = '[REDACTED-ABLY-KEY]' },
    @{ Pattern = '\b([0-9a-fA-F]{8})[0-9a-fA-F\-]{24,}\b';                                          Replace = '$1...[REDACTED]' },
    @{ Pattern = '\b(\d{6})\d{11,}\b';                                                              Replace = '$1...[REDACTED]' }
)

function Protect-ScoutText([string]$text) {
    foreach ($r in $Script:RedactionRules) {
        $text = [regex]::Replace($text, $r.Pattern, $r.Replace, 'IgnoreCase')
    }
    return $text
}

$Script:LogMaxBytes = 2MB
$Script:LogBackups  = 5

function Write-ScoutLog {
    param([string]$Log, [string]$Level = "INFO", [string]$Code = "", [string]$Message)
    try {
        if (-not (Test-Path $ScoutDir)) { New-Item -ItemType Directory -Path $ScoutDir | Out-Null }
        $file = Join-Path $ScoutDir "$Log.log"
        if ((Test-Path $file) -and ((Get-Item $file).Length -gt $Script:LogMaxBytes)) {
            for ($i = $Script:LogBackups - 1; $i -ge 1; $i--) {
                $src = "$file.$i"; $dst = "$file.$($i + 1)"
                if (Test-Path $src) { Move-Item -Force $src $dst }
            }
            Move-Item -Force $file "$file.1"
        }
        $ts = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
        $codePart = ""
        if ($Code) { $codePart = "$Code " }
        $line = "$ts [$Log] $Level $codePart$(Protect-ScoutText $Message)"
        [System.IO.File]::AppendAllText($file, $line + "`r`n", (New-Object System.Text.UTF8Encoding($false)))
    } catch { }  # logging must never take the installer down
}

# Hidden startup still needs a visible fatal error. Points at the exact log.
function Show-FatalDialog([string]$message, [string]$logName) {
    $full = "$message`n`nDetails: $(Join-Path $ScoutDir "$logName.log")"
    try {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show($full, "Valorant Scout",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Error) | Out-Null
    } catch {
        Fail $full
    }
}

# ---------------------------------------------------------------------------
# Exclusive locks — one installer / one updater at a time. The handle is held
# open (FileShare None) until Close(); the OS releases it if we die.
# ---------------------------------------------------------------------------
function New-ScoutLock([string]$name) {
    if (-not (Test-Path $ScoutDir)) { New-Item -ItemType Directory -Path $ScoutDir | Out-Null }
    $path = Join-Path $ScoutDir "$name.lock"
    try {
        return [System.IO.File]::Open($path, [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
    } catch {
        throw "Another Valorant Scout $name operation is already running. Wait for it to finish and try again."
    }
}

# Cross-language named mutexes coordinate the Python launcher with PowerShell
# maintenance. File locks and Python byte-range locks do not reliably block one
# another on Windows, so install/update/start all use these names.
function Get-ScoutMutexName([string]$purpose) {
    return "Local\ValorantScout-$purpose-$(Get-PathFingerprint)"
}

function New-ScoutMutex([string]$purpose, [string]$busyMessage) {
    $created = $false
    $mutex = [System.Threading.Mutex]::new(
        $false, (Get-ScoutMutexName $purpose), [ref]$created)
    $acquired = $false
    try {
        $acquired = $mutex.WaitOne(0)
    } catch [System.Threading.AbandonedMutexException] {
        $acquired = $true
    }
    if (-not $acquired) {
        $mutex.Dispose()
        throw $busyMessage
    }
    return $mutex
}

function Close-ScoutMutex($mutex) {
    if (-not $mutex) { return }
    try { $mutex.ReleaseMutex() } catch { }
    try { $mutex.Dispose() } catch { }
}

# install/update terminate a running app and continue instead of refusing.
# The launcher records its own pid in runtime-state.json; we kill THAT process
# tree only after confirming the pid is genuinely our launcher (a reused pid
# must never be killed). Returns $true if it stopped a running app.
function Stop-RunningApp([string]$LogName = "launcher") {
    $stateFile = Join-Path $ScoutDir "runtime-state.json"
    if (-not (Test-Path $stateFile)) { return $false }
    $appPid = 0
    try { $appPid = [int]((Get-Content $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json).pid) } catch { $appPid = 0 }
    if ($appPid -le 0) { return $false }

    if (-not (Get-CimInstance Win32_Process -Filter "ProcessId=$appPid" -ErrorAction SilentlyContinue)) {
        Remove-Item $stateFile -Force -ErrorAction SilentlyContinue  # stale pid, app already gone
        return $false
    }
    # Confirm the pid is genuinely OUR launcher before killing it. We match on
    # this install's venv path ("<root>\.venv\") — specific enough that a reused
    # pid whose ancestor merely lives under the root is NOT killed. On a
    # Store-Python venv the recorded pid re-execs to the Store base interpreter,
    # so that path only shows up on a parent; walk up a few hops (like run.py's
    # _is_ours) to find it.
    $venvLower = $VenvDir.ToLowerInvariant()
    $cur = $appPid
    $ours = $false
    for ($hop = 0; $hop -lt 4; $hop++) {
        if ($cur -le 0) { break }
        $cp = Get-CimInstance Win32_Process -Filter "ProcessId=$cur" -ErrorAction SilentlyContinue
        if (-not $cp) { break }
        $identity = (($cp.ExecutablePath + " " + $cp.CommandLine) + "").ToLowerInvariant()
        if ($identity.Contains($venvLower + '\')) { $ours = $true; break }
        $cur = [int]$cp.ParentProcessId
    }
    if (-not $ours) { return $false }

    Note "Closing the running Valorant Scout (PID $appPid) so we can continue ..."
    Write-ScoutLog -Log $LogName -Message "closing running app pid=$appPid before maintenance"
    # EAP=Continue: taskkill writes to stderr when a tree child resists closing,
    # and under the file-global Stop that stderr becomes a terminating error.
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & taskkill /PID $appPid /T 2>&1 | Out-Null        # graceful (Ctrl-Break to the tree)
        $deadline = (Get-Date).AddSeconds(3)
        while ((Get-Date) -lt $deadline -and (Get-CimInstance Win32_Process -Filter "ProcessId=$appPid" -ErrorAction SilentlyContinue)) {
            Start-Sleep -Milliseconds 200
        }
        if (Get-CimInstance Win32_Process -Filter "ProcessId=$appPid" -ErrorAction SilentlyContinue) {
            & taskkill /PID $appPid /T /F 2>&1 | Out-Null  # force the whole tree
        }
    } finally { $ErrorActionPreference = $prevEap }

    # Wait for the process — and thus the App mutex it owned — to be fully gone
    # before the caller acquires that mutex.
    $deadline = (Get-Date).AddSeconds(5)
    while ((Get-Date) -lt $deadline -and (Get-CimInstance Win32_Process -Filter "ProcessId=$appPid" -ErrorAction SilentlyContinue)) {
        Start-Sleep -Milliseconds 200
    }
    Remove-Item $stateFile -Force -ErrorAction SilentlyContinue
    Ok "Closed the running app — continuing."
    return $true
}

# ---------------------------------------------------------------------------
# Preflight — fail fast with a human-readable reason instead of half-installing.
# ---------------------------------------------------------------------------
function Test-Preflight {
    $problems = @()

    $os = [Environment]::OSVersion.Version
    if ($os.Major -lt 10) { $problems += "Windows 10 or 11 is required (this is Windows $($os.Major).$($os.Minor))." }
    if (-not [Environment]::Is64BitOperatingSystem) { $problems += "64-bit Windows is required." }
    $arch = $env:PROCESSOR_ARCHITECTURE
    if ($env:PROCESSOR_ARCHITEW6432) { $arch = $env:PROCESSOR_ARCHITEW6432 }
    if ($arch -ne "AMD64") { $problems += "Only x64 PCs are supported in this release (detected: $arch). ARM64 is not supported yet." }

    # Running from inside an unextracted ZIP (Explorer preview) breaks everything.
    if ($Root -match '\.zip[\\/]' -or $Root -match '\\Temp1_[^\\]*\\') {
        $problems += "You are running from inside the ZIP file. Extract it first (right-click -> Extract All), then run install.bat from the extracted folder."
    }

    # PowerShell -Path parameters treat [ ] as wildcards, which breaks file
    # checks all over with misleading errors. Refuse up front with the real fix.
    if ($Root -match '[\[\]]') {
        $problems += "The folder name contains square brackets [ ]: '$Root'. Rename the folder to remove them (e.g. 'valorant-scout [1]' -> 'valorant-scout'), then run install.bat again."
    }

    foreach ($dir in @($Root, $env:TEMP)) {
        try {
            $t = Join-Path $dir (".vs-write-test-" + [Guid]::NewGuid().ToString("N"))
            [System.IO.File]::WriteAllText($t, "x")
            Remove-Item $t -Force
        } catch {
            $problems += "The folder '$dir' is not writable. Move Valorant Scout to a folder you can write to (e.g. Documents)."
        }
    }

    try {
        $drive = (Get-Item $Root).PSDrive
        if ($drive -and $null -ne $drive.Free -and $drive.Free -lt 2GB) {
            $problems += "Less than 2 GB free on drive $($drive.Name): — free up space and retry."
        }
    } catch { }

    return $problems
}

function Test-StdinInteractive {
    try { return -not [Console]::IsInputRedirected } catch { return $false }
}

# ---------------------------------------------------------------------------
# Exact Python selection. Never trust the executable's name: run it and check
# what it says it is. Only CPython <manifest version> 64-bit x64 is accepted.
# ---------------------------------------------------------------------------
function Get-PythonIdentity([string]$exe, [string[]]$exeArgs) {
    $probe = Join-Path $PSScriptRoot "python_probe.py"
    try {
        # EAP=Continue: under the file-global Stop, redirected native stderr
        # (a sitecustomize print, PYTHONWARNINGS) would throw and misclassify
        # a healthy interpreter as broken.
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try { $out = & $exe @($exeArgs + @($probe)) 2>$null }
        finally { $ErrorActionPreference = $prevEAP }
        if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
        return (($out | Select-Object -Last 1) | ConvertFrom-Json)
    } catch { return $null }
}

function Test-PythonExact($identity, $manifest) {
    if (-not $identity) { return $false }
    return ($identity.implementation -eq $manifest.python.implementation) -and
           ($identity.version -eq $manifest.python.version) -and
           ($identity.bits -eq $manifest.python.bits) -and
           ($identity.machine -eq "AMD64")
}

function Get-PythonCandidates {
    $cands = @()
    $mf = Get-RuntimeManifest
    $mm = ($mf.python.version -split '\.')[0..1] -join '.'   # "3.12"
    if (Has-Cmd "py")      { $cands += @{ Exe = "py"; Args = @("-$mm") } }
    if (Has-Cmd "python")  { $cands += @{ Exe = "python";  Args = @() } }
    if (Has-Cmd "python3") { $cands += @{ Exe = "python3"; Args = @() } }
    $regRoots = @(
        "HKCU:\Software\Python\PythonCore\$mm\InstallPath",
        "HKLM:\Software\Python\PythonCore\$mm\InstallPath",
        "HKLM:\Software\WOW6432Node\Python\PythonCore\$mm\InstallPath"
    )
    foreach ($rk in $regRoots) {
        try {
            $ip = (Get-ItemProperty -Path $rk -ErrorAction Stop).'(default)'
            if ($ip) {
                $exe = Join-Path $ip "python.exe"
                if (Test-Path $exe) { $cands += @{ Exe = $exe; Args = @() } }
            }
        } catch { }
    }
    $default = Join-Path $env:LocalAppData ("Programs\Python\Python" + ($mm -replace '\.', '') + "\python.exe")
    if (Test-Path $default) { $cands += @{ Exe = $default; Args = @() } }
    return $cands
}

function Find-ExactPython {
    $mf = Get-RuntimeManifest
    foreach ($c in Get-PythonCandidates) {
        $id = Get-PythonIdentity $c.Exe $c.Args
        if (Test-PythonExact $id $mf) {
            Write-ScoutLog -Log install -Message "accepted python: $($c.Exe) $($c.Args -join ' ') -> $($id.version) $($id.machine) $($id.bits)-bit at $($id.executable)"
            return $c
        }
        if ($id) {
            Write-ScoutLog -Log install -Level WARN -Message "rejected python candidate $($c.Exe): $($id.implementation) $($id.version) $($id.machine) $($id.bits)-bit"
        } else {
            Write-ScoutLog -Log install -Level WARN -Message "rejected python candidate $($c.Exe): does not run (Store alias or broken launcher)"
        }
    }
    return $null
}

# Installs the exact python.org runtime, per-user. Pinned URL + SHA-256 +
# Authenticode — winget is deliberately not used (its package family can't be
# pinned to a patch version we've tested).
function Install-ExactPython {
    $mf = Get-RuntimeManifest
    Step "Installing Python $($mf.python.version) (64-bit, from python.org) ..."
    $tmp = Join-Path $env:TEMP ("vs-python-" + [Guid]::NewGuid().ToString("N") + ".exe")
    try {
        Note "Downloading $($mf.python.installerUrl)"
        Invoke-WebRequest -Uri $mf.python.installerUrl -OutFile $tmp -TimeoutSec 600
        $hash = (Get-FileHash -Algorithm SHA256 -Path $tmp).Hash
        if ($hash -ne $mf.python.installerSha256.ToUpper()) {
            throw "Python installer checksum mismatch (expected $($mf.python.installerSha256), got $hash). Refusing to run it."
        }
        $sig = Get-AuthenticodeSignature $tmp
        if ($sig.Status -ne "Valid" -or $sig.SignerCertificate.Subject -ne $mf.python.installerSubject) {
            throw "Python installer signature check failed (status: $($sig.Status)). Refusing to run it."
        }
        Ok "Installer verified (SHA-256 + Authenticode)."
        $p = Start-Process -FilePath $tmp -Wait -PassThru -ArgumentList `
            "/quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_launcher=1"
        if ($p.ExitCode -ne 0) { throw "Python installer failed (exit code $($p.ExitCode))." }
    } finally {
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    }
    Refresh-Path
}

function Ensure-ExactPython {
    $mf = Get-RuntimeManifest
    $py = Find-ExactPython
    if (-not $py) {
        Note "No CPython $($mf.python.version) x64 found on this PC."
        Install-ExactPython
        $py = Find-ExactPython
    }
    if (-not $py) {
        throw "VS-PY-001 CPython $($mf.python.version) 64-bit could not be found or installed. Install it manually from $($mf.python.installerUrl) and run install.bat again."
    }
    Ok "Python $($mf.python.version) x64 ready."
    return $py
}

# ---------------------------------------------------------------------------
# Venv validation & repair. "python.exe exists" is not a health check.
# ---------------------------------------------------------------------------
function Assert-IsRepoVenv([string]$path) {
    $resolved = [System.IO.Path]::GetFullPath($path)
    $expected = [System.IO.Path]::GetFullPath((Join-Path $Root ".venv"))
    if ($resolved -ne $expected) {
        throw "Refusing to touch '$resolved' — it is not this installation's .venv ($expected)."
    }
    return $resolved
}

function Get-VenvPipVersion {
    try {
        # -join: console-width wrapping can split the output into an array,
        # and array -match filters instead of populating $Matches
        # EAP=Continue: a pip deprecation line on stderr must not read as "no pip".
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try { $out = (& $VenvPy -m pip --version 2>$null) -join " " }
        finally { $ErrorActionPreference = $prevEAP }
        if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
        if ($out -match 'pip\s+(\S+)') { return $Matches[1] }
    } catch { }
    return $null
}

# -Quick (startup path): file/marker checks only — NO python launches. The five
# cold python starts below (identity probe, pip --version, pip check,
# verify_installed, import_smoke) cost 15-25s on a cold machine (Windows
# Sandbox, Defender scanning), which dwarfs the app's own boot. Install/update/
# diagnose still run the full battery; a venv quick misses fails loudly at
# backend start anyway.
function Test-Venv([switch]$Quick) {
    $mf = Get-RuntimeManifest
    $reasons = @()

    if (-not (Test-Path $VenvPy)) { return @{ Ok = $false; Reasons = @("no python.exe in .venv") } }

    # Python virtual environments are not portable. sys.prefix normally
    # follows the executable after a folder move, so also inspect the creation
    # command recorded in pyvenv.cfg and the install marker's path fingerprint.
    $cfgPath = Join-Path $VenvDir "pyvenv.cfg"
    if (-not (Test-Path $cfgPath)) {
        $reasons += "venv has no pyvenv.cfg (incomplete or corrupt environment)"
    } else {
        try {
            $cfg = Get-Content $cfgPath -Raw -Encoding UTF8
            if ($cfg -match '(?im)^command\s*=\s*(.+)$') {
                $command = $Matches[1].Trim()
                $marker = '-m venv '
                $at = $command.LastIndexOf($marker, [StringComparison]::OrdinalIgnoreCase)
                if ($at -ge 0) {
                    $createdAt = $command.Substring($at + $marker.Length).Trim().Trim('"').Trim("'")
                    if (-not [System.IO.Path]::IsPathRooted($createdAt)) {
                        $createdAt = Join-Path $Root $createdAt
                    }
                    $createdResolved = [System.IO.Path]::GetFullPath($createdAt).TrimEnd('\')
                    $expectedResolved = [System.IO.Path]::GetFullPath($VenvDir).TrimEnd('\')
                    if ($createdResolved -ne $expectedResolved) {
                        $reasons += "venv was created for another folder ('$createdResolved' vs '$expectedResolved')"
                    }
                }
            }
        } catch {
            $reasons += "pyvenv.cfg is unreadable"
        }
    }
    $installedFile = Join-Path $ScoutDir "installed.json"
    if (Test-Path $installedFile) {
        try {
            $installedMarker = Get-Content $installedFile -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($installedMarker.pathFingerprint -and
                    $installedMarker.pathFingerprint -ne (Get-PathFingerprint)) {
                $reasons += "installation folder moved since the venv was created"
            }
        } catch { }
    }

    if ($Quick) {
        $depsFileQ = Join-Path $ScoutDir "deps.json"
        $recordedQ = ""
        if (Test-Path $depsFileQ) {
            try { $recordedQ = (Get-Content $depsFileQ -Raw | ConvertFrom-Json).requirements } catch { }
        }
        if (-not $recordedQ -or $recordedQ -ne (HashOf "backend\requirements.txt")) {
            $reasons += "requirements.txt changed since packages were installed (hash mismatch)"
        }
        return @{ Ok = ($reasons.Count -eq 0); Reasons = $reasons }
    }

    $id = Get-PythonIdentity $VenvPy @()
    if (-not $id) {
        $reasons += "venv python does not run (moved or corrupt venv)"
    } else {
        if (-not (Test-PythonExact $id $mf)) {
            $reasons += "venv python is $($id.implementation) $($id.version) $($id.machine) $($id.bits)-bit, need CPython $($mf.python.version) x64"
        }
        if (-not $id.isVenv) { $reasons += "python in .venv is not a virtual environment" }
        $expected = [System.IO.Path]::GetFullPath($VenvDir).TrimEnd('\')
        $actual = ""
        if ($id.prefix) { $actual = [System.IO.Path]::GetFullPath($id.prefix).TrimEnd('\') }
        if ($actual -ne $expected) { $reasons += "venv prefix mismatch ('$actual' vs '$expected') — venv was moved" }
        if ($id.basePrefix -and -not (Test-Path (Join-Path $id.basePrefix "python.exe"))) {
            $reasons += "venv base interpreter is gone ($($id.basePrefix))"
        }
    }
    if ($reasons.Count -gt 0) { return @{ Ok = $false; Reasons = $reasons } }

    $pipVer = Get-VenvPipVersion
    if ($pipVer -ne $mf.pip.version) { $reasons += "pip is '$pipVer', pinned version is $($mf.pip.version)" }

    $depsFile = Join-Path $ScoutDir "deps.json"
    $recorded = ""
    if (Test-Path $depsFile) {
        try { $recorded = (Get-Content $depsFile -Raw | ConvertFrom-Json).requirements } catch { }
    }
    if (-not $recorded -or $recorded -ne (HashOf "backend\requirements.txt")) {
        $reasons += "requirements.txt changed since packages were installed (hash mismatch)"
    }

    # Under the file-global EAP=Stop, PS5.1 turns redirected native stderr into a
    # terminating NativeCommandError, so these probes would THROW instead of
    # contributing reasons. Let stderr flow; only the exit codes matter.
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $VenvPy -m pip check 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { $reasons += "pip check reports broken/missing dependencies" }

        & $VenvPy (Join-Path $PSScriptRoot "verify_installed.py") `
            --requirements (Join-Path $Root "backend\requirements.txt") 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { $reasons += "installed package versions do not exactly match requirements.txt" }

        & $VenvPy (Join-Path $PSScriptRoot "import_smoke.py") 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { $reasons += "required packages fail to import" }
    } finally { $ErrorActionPreference = $prevEap }

    return @{ Ok = ($reasons.Count -eq 0); Reasons = $reasons }
}

function Repair-Venv($py) {
    $resolved = Assert-IsRepoVenv $VenvDir
    if (Test-Path $resolved) {
        Step "Rebuilding the Python environment (.venv) ..."
        try {
            Remove-Item -Recurse -Force $resolved
        } catch {
            throw "Couldn't remove the old .venv ($($_.Exception.Message)). Close any running Valorant Scout windows (and any antivirus quarantine on that folder), then run install.bat again."
        }
    } else {
        Step "Creating the Python environment (.venv) ..."
    }
    & $py.Exe @($py.Args + @("-m", "venv", $resolved))
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $VenvPy)) { throw "Failed to create .venv (exit code $LASTEXITCODE)." }
}

function Install-PyDeps {
    $mf = Get-RuntimeManifest
    Step "Installing Python packages (pinned) ..."
    & $VenvPy -m pip install --quiet --no-warn-script-location "pip==$($mf.pip.version)"
    if ($LASTEXITCODE -ne 0) { throw "Couldn't install the pinned pip $($mf.pip.version) (check your internet connection)." }
    $req = Join-Path $Root "backend\requirements.txt"
    $done = $false
    for ($i = 1; $i -le 3; $i++) {
        if ($i -gt 1) { Warn2 "pip install hiccup - retrying ($i/3) ..."; Start-Sleep -Seconds 3 }
        & $VenvPy -m pip install -r $req
        if ($LASTEXITCODE -eq 0) { $done = $true; break }
    }
    if (-not $done) { throw "VS-DEPS-001 pip install failed (check your internet connection, then run install.bat again)." }

    & $VenvPy -m pip check
    if ($LASTEXITCODE -ne 0) { throw "VS-DEPS-001 pip check failed after install — the dependency set is inconsistent." }
    & $VenvPy (Join-Path $PSScriptRoot "verify_installed.py") `
        --requirements (Join-Path $Root "backend\requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "VS-DEPS-001 installed package versions do not exactly match requirements.txt." }
    & $VenvPy (Join-Path $PSScriptRoot "import_smoke.py")
    if ($LASTEXITCODE -ne 0) { throw "VS-DEPS-001 import smoke test failed after install." }
    Ok "Python packages installed and verified."
}

# ---------------------------------------------------------------------------
# Install markers — written ONLY after every required step passed.
# ---------------------------------------------------------------------------
function Get-PathFingerprint {
    # MUST stay byte-identical to _path_fingerprint in run.py: normalize the path,
    # then lowercase ASCII A-Z ONLY (every other char untouched). ToLowerInvariant
    # is culture-aware and maps chars like Turkish İ (U+0130) / ẞ (U+1E9E) that
    # Python's str.lower would map differently — an ASCII-only fold keeps both
    # sides in lockstep. Pure-ASCII paths hash identically to the old algorithm,
    # so existing installed.json markers stay valid.
    $normalized = [System.IO.Path]::GetFullPath($Root).TrimEnd('\')
    $chars = $normalized.ToCharArray()
    for ($i = 0; $i -lt $chars.Length; $i++) {
        $c = [int]$chars[$i]
        if ($c -ge 0x41 -and $c -le 0x5A) { $chars[$i] = [char]($c + 0x20) }
    }
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $bytes = [System.Text.Encoding]::UTF8.GetBytes((-join $chars))
    return ([BitConverter]::ToString($sha.ComputeHash($bytes)) -replace '-', '').Substring(0, 16)
}

function Save-Markers($region) {
    $mf = Get-RuntimeManifest
    if (-not (Test-Path $ScoutDir)) { New-Item -ItemType Directory -Path $ScoutDir | Out-Null }
    $installed = @{
        schemaVersion   = $MarkerSchemaVersion
        version         = (Get-LocalVersion)
        region          = $region
        python          = @{ version = $mf.python.version; arch = $mf.python.arch }
        pip             = $mf.pip.version
        requirementsHash = (HashOf "backend\requirements.txt")
        pathFingerprint = (Get-PathFingerprint)
        installedAt     = [DateTime]::UtcNow.ToString("o")
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

function Test-Markers {
    $f = Join-Path $ScoutDir "installed.json"
    if (-not (Test-Path $f)) { return @{ Ok = $false; Reason = "not installed (no marker)" } }
    try { $m = Get-Content $f -Raw | ConvertFrom-Json } catch { return @{ Ok = $false; Reason = "install marker is corrupt" } }
    if ([int]$m.schemaVersion -ne $MarkerSchemaVersion) { return @{ Ok = $false; Reason = "install marker is from an older setup" } }
    if ($m.version -ne (Get-LocalVersion)) { return @{ Ok = $false; Reason = "install marker version '$($m.version)' does not match app version '$(Get-LocalVersion)'" } }
    if ($m.requirementsHash -ne (HashOf "backend\requirements.txt")) { return @{ Ok = $false; Reason = "app files changed since install (requirements hash mismatch)" } }
    if ($m.pathFingerprint -ne (Get-PathFingerprint)) { return @{ Ok = $false; Reason = "the folder was moved since install" } }
    return @{ Ok = $true; Marker = $m }
}

function Is-Installed {
    return ((Test-Markers).Ok -and (Test-Path $VenvPy))
}

# ---------------------------------------------------------------------------
# Region + shortcut (unchanged behaviour)
# ---------------------------------------------------------------------------
function Set-Region($region) {
    $lines = @()
    if (Test-Path $EnvFile) {
        # -Encoding UTF8: PS5.1 defaults BOM-less files to ANSI and mojibakes any non-ASCII on the rewrite
        $lines = Get-Content $EnvFile -Encoding UTF8 | Where-Object { $_ -notmatch '^\s*RIOT_REGION\s*=' }
    }
    $lines += "RIOT_REGION=$region"
    Write-FileNoBom $EnvFile (($lines -join "`r`n") + "`r`n")
}

function Get-SavedRegion {
    if (Test-Path $EnvFile) {
        $m = (Get-Content $EnvFile -Encoding UTF8 | Select-String -Pattern '^\s*RIOT_REGION\s*=\s*(.+)$')
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

# ---------------------------------------------------------------------------
# Frontend (private/dev tree only — public slim builds never touch Node)
# ---------------------------------------------------------------------------
function Find-Node {
    if (Has-Cmd "node") {
        try {
            $v = (& node -v) -replace '^v', ''
            if ([version]$v -ge [version]"18.17.0") { return $true }
        } catch {}
    }
    return $false
}

function Run-Npm([string[]]$npmArgs) {
    Push-Location (Join-Path $Root "frontend")
    try {
        cmd /c ("npm " + ($npmArgs -join " ") + " 2>&1") | Out-Host
        return $LASTEXITCODE
    } finally { Pop-Location }
}

function Install-NodeDeps {
    Step "Installing frontend packages (npm ci) ..."
    if ((Run-Npm @("ci", "--no-fund", "--no-audit")) -ne 0) {
        throw "npm ci failed (check your internet connection and Node version, then retry)."
    }
    Ok "Frontend packages installed."
}

function Build-Frontend {
    Step "Building the website (npm run build) ... (first build can take a minute)"
    if ((Run-Npm @("run", "build")) -ne 0) { throw "npm run build failed" }
    Ok "Website built."
}

# ---------------------------------------------------------------------------
# Update CHECK only (never applies). UPDATE.bat runs the transactional updater.
# ---------------------------------------------------------------------------
function Get-LatestRelease([int]$timeoutSec = 8) {
    try {
        return Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" `
            -Headers @{ "User-Agent" = "valorant-scout" } -TimeoutSec $timeoutSec
    } catch { return $null }
}

function Test-UpdateAvailable {
    $rel = Get-LatestRelease
    if (-not $rel -or -not $rel.tag_name) { return $null }
    try {
        if ((Compare-ScoutVersion $rel.tag_name (Get-LocalVersion)) -gt 0) { return $rel.tag_name }
    } catch { }
    return $null
}
