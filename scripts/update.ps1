param(
    [switch]$DevOverride,          # required to update over a .git developer checkout
    [string]$LocalAssets = "",     # test hook: folder holding the three release assets (skips download)
    [string]$ExpectVersion = "",   # test hook: version expected inside -LocalAssets
    [switch]$AllowDirtyAssets      # test hook only; never accepted for downloaded releases
)

. (Join-Path $PSScriptRoot "common.ps1")

# Staged, verified, transactional update:
#   download -> verify (checksums, manifest, safe archive) -> backup -> apply
#   -> validate (deps, backend+WS boot) -> commit, with rollback on any failure
# and crash recovery from the .scout/update-state.json journal.

$StateFile = Join-Path $ScoutDir "update-state.json"

function Write-UpdateState($state) {
    $temp = "$StateFile.tmp"
    Write-FileNoBom $temp ($state | ConvertTo-Json)
    if (Test-Path $StateFile) {
        # [NullString]::Value, not $null: PS 5.1 coerces $null to "" for the
        # string backup-path parameter, and "" throws "path is not of a legal form".
        [System.IO.File]::Replace($temp, $StateFile, [NullString]::Value)
    } else {
        [System.IO.File]::Move($temp, $StateFile)
    }
}

# User data is NEVER part of an update transaction.
$PreservePrefixes = @("backend\.env", "backend\data", ".scout", ".venv", ".git",
                      "frontend\.env.local", "frontend\node_modules", "frontend\.next")

function Test-Preserved([string]$rel) {
    $rel = $rel -replace '/', '\'
    foreach ($p in $PreservePrefixes) {
        if ($rel.Equals($p, [StringComparison]::OrdinalIgnoreCase) -or
                $rel.StartsWith("$p\", [StringComparison]::OrdinalIgnoreCase)) { return $true }
    }
    return $false
}

function Resolve-RepoPath([string]$rel) {
    $rel = $rel -replace '/', '\'
    if ([System.IO.Path]::IsPathRooted($rel)) { throw "absolute update path is forbidden: '$rel'" }
    $rootFull = [System.IO.Path]::GetFullPath($Root).TrimEnd('\')
    $full = [System.IO.Path]::GetFullPath((Join-Path $rootFull $rel))
    if (-not $full.StartsWith($rootFull + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "update path escapes the installation folder: '$rel'"
    }
    return $full
}

function Get-FreePort {
    $l = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
    $l.Start()
    $port = $l.LocalEndpoint.Port
    $l.Stop()
    return $port
}

function Remove-LegacyCaches {
    # Pre-manifest releases accidentally included Python bytecode. It is never
    # user data and can load stale code after an otherwise successful update.
    $targets = @()
    $top = Join-Path $Root "__pycache__"
    if (Test-Path $top) { $targets += Get-Item $top }
    foreach ($codeDir in @((Join-Path $Root "backend"), (Join-Path $Root "scripts"))) {
        if (Test-Path $codeDir) {
            $targets += Get-ChildItem -Path $codeDir -Directory -Filter "__pycache__" -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    foreach ($target in @($targets | Sort-Object FullName -Unique)) {
        Remove-Item -LiteralPath $target.FullName -Recurse -Force -ErrorAction SilentlyContinue
    }
    # Releases no longer ship a manifest or the checksum verifier; drop the
    # copies an older install left behind so the tree matches what we ship.
    foreach ($stale in @("release-manifest.json", "scripts\update_verify.py")) {
        $p = Join-Path $Root $stale
        if (Test-Path $p) { Remove-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue }
    }
}

function Restore-FromBackup($state) {
    Step "Rolling back the interrupted/failed update ..."
    $backup = $state.backupDir
    if (-not $backup -or -not (Test-Path $backup)) {
        throw "update rollback backup is missing; refusing to guess at the previous tree. Reinstall the last release over this folder."
    }
    Get-ChildItem -Path $backup -Recurse -File | ForEach-Object {
        $rel = $_.FullName.Substring($backup.Length).TrimStart('\')
        $dst = Resolve-RepoPath $rel
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
        Copy-Item -Force $_.FullName $dst
    }
    foreach ($rel in @($state.addedFiles)) {
        if (-not $rel) { continue }
        if (Test-Preserved $rel) { continue }
        $p = Resolve-RepoPath $rel
        if (Test-Path $p) { Remove-Item -Force $p }
    }
    if ($state.venvTouched -and $state.venvBackupDir -and (Test-Path $state.venvBackupDir)) {
        $resolved = Assert-IsRepoVenv $VenvDir
        if (Test-Path $resolved) { Remove-Item -Recurse -Force $resolved }
        Move-Item -LiteralPath $state.venvBackupDir -Destination $resolved
    }
    # The failure may have happened after the new install markers were written.
    # Re-derive them from the restored code/dependencies so rollback is complete.
    Save-Markers (Get-SavedRegion)
    if ($backup -and (Test-Path $backup)) { Remove-Item -Recurse -Force $backup -ErrorAction SilentlyContinue }
    Remove-Item -Force $StateFile -ErrorAction SilentlyContinue
    Write-ScoutLog -Log update -Level WARN -Code VS-UPDATE-001 -Message "rolled back to v$(Get-LocalVersion)"
    Ok "Rolled back — you are on v$(Get-LocalVersion)."
}

Write-Host ""
Write-Host "  VALORANT SCOUT - UPDATE" -ForegroundColor Red

$lock = $null
$maintenanceMutex = $null
$appMutex = $null
$staging = $null
try {
    $lock = New-ScoutLock "update"
    $maintenanceMutex = New-ScoutMutex "Maintenance" "Another Valorant Scout install/update operation is already running. Wait for it to finish and retry."
    Stop-RunningApp "update" | Out-Null
    $appMutex = New-ScoutMutex "App" "Valorant Scout is still running and couldn't be closed automatically. Close the scoreboard window before updating."

    # Recover before checking the venv: an interrupted dependency transaction
    # may intentionally have moved .venv into its rollback location.
    if (Test-Path $StateFile) {
        try { $prev = Get-Content $StateFile -Raw | ConvertFrom-Json } catch { $prev = $null }
        if ($prev -and $prev.phase -in @("apply", "validate")) {
            Warn2 "A previous update was interrupted mid-apply."
            Restore-FromBackup $prev
        } else {
            throw "update-state.json is unreadable or has an unknown phase; refusing to continue without a safe rollback plan. Re-extract the last release over this folder."
        }
    }

    if (-not (Is-Installed)) {
        Warn2 "Not set up yet - run install.bat first."
        exit 1
    }
    $venvHealth = Test-Venv
    if (-not $venvHealth.Ok) {
        throw "The current Python environment is unhealthy ($($venvHealth.Reasons -join '; ')). Run install.bat to repair it before updating."
    }
    $currentRuntime = Get-RuntimeManifest
    $oldReqHash = HashOf "backend\requirements.txt"
    $oldPipVersion = [string]$currentRuntime.pip.version
    if ($AllowDirtyAssets -and (-not $LocalAssets -or -not $DevOverride)) {
        throw "-AllowDirtyAssets is restricted to local developer-override tests."
    }

    if ((Test-Path (Join-Path $Root ".git")) -and -not $DevOverride) {
        throw "This is a developer checkout (.git present). The updater refuses to overwrite it — use git pull, or re-run with -DevOverride if you really mean it."
    }

    # ---- Locate the release zip ----------------------------------------
    $zipName = ""; $zip = ""; $newVersion = ""
    if ($LocalAssets) {
        if (-not $ExpectVersion) { throw "-LocalAssets requires -ExpectVersion." }
        $newVersion = $ExpectVersion
        $zipName  = "valorant-scout-v$newVersion-windows-source.zip"
        $zip      = Join-Path $LocalAssets $zipName
        if (-not (Test-Path $zip)) { throw "missing local asset: $zip" }
        Note "Using local assets from $LocalAssets (v$newVersion)."
    } else {
        Step "Checking for updates ..."
        $rel = Get-LatestRelease
        if (-not $rel) { Note "No update info (offline, or no releases yet) - nothing to do."; exit 0 }
        $newVersion = ($rel.tag_name.Trim()) -replace '^[vV]', ''
        if ((Compare-ScoutVersion $newVersion (Get-LocalVersion)) -le 0) {
            Ok "You're on the latest version (v$(Get-LocalVersion))."
            exit 0
        }
        Step "Updating v$(Get-LocalVersion) -> v$newVersion ..."
        # Exact asset name only — never the auto-generated GitHub source zipball.
        $zipName = "valorant-scout-v$newVersion-windows-source.zip"
        $zipUrl = $null
        foreach ($a in $rel.assets) { if ($a.name -eq $zipName) { $zipUrl = $a.browser_download_url } }
        if (-not $zipUrl) {
            throw "release v$newVersion has no '$zipName' asset — refusing to update from an incomplete release."
        }
        $staging = Join-Path $env:TEMP ("vs-update-" + [Guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Path $staging | Out-Null
        $zip = Join-Path $staging $zipName
        $okDl = $false
        for ($i = 1; $i -le 3; $i++) {
            try {
                Invoke-WebRequest -Uri $zipUrl -OutFile $zip `
                    -Headers @{ "User-Agent" = "valorant-scout" } -TimeoutSec 300
                $okDl = $true; break
            } catch {
                Warn2 "download hiccup ($($_.Exception.Message)) - retrying ($i/3) ..."
                Start-Sleep -Seconds 3
            }
        }
        if (-not $okDl) { throw "couldn't download '$zipName' after 3 attempts." }
    }

    if (-not $staging) {
        $staging = Join-Path $env:TEMP ("vs-update-" + [Guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Path $staging | Out-Null
    }

    # Free space: zip x3 (extracted tree + backup) plus slack.
    $needBytes = ((Get-Item $zip).Length * 3) + 200MB
    $drive = (Get-Item $Root).PSDrive
    if ($drive -and $null -ne $drive.Free -and $drive.Free -lt $needBytes) {
        throw "not enough free disk space to update safely (need ~$([Math]::Ceiling($needBytes / 1MB)) MB)."
    }

    # ---- Extract into staging -------------------------------------------
    # Expand-Archive uses .NET ZipFile, which rejects path-traversal entries and
    # fails on a corrupt/truncated zip. The release ships only the source zip,
    # served over HTTPS — no separate checksum/manifest step.
    Step "Extracting the download ..."
    $extract = Join-Path $staging "tree"
    Expand-Archive -Path $zip -DestinationPath $extract -Force
    $newRoot = Join-Path $extract "valorant-scout-v$newVersion"
    if (-not (Test-Path (Join-Path $newRoot "backend")) -or -not (Test-Path (Join-Path $newRoot "VERSION"))) {
        throw "the downloaded release does not contain the expected app files."
    }
    if ((Get-Content (Join-Path $newRoot "VERSION") -Raw).Trim() -ne $newVersion) {
        throw "the downloaded release's VERSION does not match v$newVersion."
    }
    $newRuntime = Get-Content (Join-Path $newRoot "runtime.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    $newReqHash = (Get-FileHash -Algorithm SHA256 -Path (Join-Path $newRoot "backend\requirements.txt")).Hash.ToLowerInvariant()
    $needsVenv = ($newReqHash -ne $oldReqHash -or [string]$newRuntime.pip.version -ne $oldPipVersion)
    if ($needsVenv) {
        $venvBytes = (Get-ChildItem -Path $VenvDir -Recurse -File -ErrorAction SilentlyContinue |
            Measure-Object -Property Length -Sum).Sum
        if ($null -eq $venvBytes) { $venvBytes = 0 }
        $transactionBytes = ([long]$venvBytes * 2) + 500MB
        $drive = (Get-Item $Root).PSDrive
        if ($drive -and $null -ne $drive.Free -and $drive.Free -lt $transactionBytes) {
            throw "not enough free disk space for a transactional dependency update (need ~$([Math]::Ceiling($transactionBytes / 1MB)) MB)."
        }
    }
    # The app's files = every file in the extracted release tree (relative
    # paths). We replace exactly those; user data is never in the zip.
    $newFiles = @(Get-ChildItem -Path $newRoot -Recurse -File | ForEach-Object {
        $_.FullName.Substring($newRoot.Length).TrimStart('\')
    })
    if ($newFiles.Count -lt 20) { throw "the downloaded release looks incomplete ($($newFiles.Count) files)." }
    Ok "Download extracted ($($newFiles.Count) files, v$newVersion)."

    # ---- Plan the transaction ------------------------------------------
    foreach ($rel in $newFiles) {
        $null = Resolve-RepoPath $rel
        if (Test-Preserved $rel) { throw "the release tries to overwrite protected user data ('$rel') — refusing." }
    }
    # No manifest: the release replaces its own files in place. A file dropped
    # between versions is left as harmless dead weight (the app loads code by
    # known names); Remove-LegacyCaches still clears stale bytecode.

    $backupDir = Join-Path $ScoutDir ("update-backup-" + (Get-LocalVersion))
    if (Test-Path $backupDir) { Remove-Item -Recurse -Force $backupDir }
    New-Item -ItemType Directory -Path $backupDir | Out-Null

    $added = @()
    Step "Backing up the current version ..."
    foreach ($rel in $newFiles) {
        $src = Resolve-RepoPath $rel
        if (Test-Path $src) {
            $dst = Join-Path $backupDir $rel
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
            Copy-Item -Force $src $dst
        } else {
            $added += $rel
        }
    }

    $state = @{
        phase          = "apply"
        version        = $newVersion
        backupDir      = $backupDir
        addedFiles     = $added
        venvTouched    = $false
        venvBackupDir  = ""
    }
    Write-UpdateState $state
    Write-ScoutLog -Log update -Message "applying v$newVersion (backup at $backupDir)"

    # ---- Apply -----------------------------------------------------------
    Step "Applying update v$newVersion (your settings and data are preserved) ..."
    try {
        foreach ($rel in $newFiles) {
            $src = Join-Path $newRoot ($rel -replace '\\', '/')
            $dst = Resolve-RepoPath $rel
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
            Copy-Item -Force $src $dst
        }

        $state.phase = "validate"
        Write-UpdateState $state

        # ---- Validate the activated tree --------------------------------
        Step "Validating the updated installation ..."
        if ((Get-LocalVersion) -ne $newVersion) { throw "VERSION file mismatch after apply." }

        if ($needsVenv) {
            $venvBackupDir = Join-Path $ScoutDir ("update-venv-backup-" + (Get-LocalVersion))
            if (Test-Path $venvBackupDir) { Remove-Item -Recurse -Force $venvBackupDir }
            $state.venvTouched = $true
            $state.venvBackupDir = $venvBackupDir
            Write-UpdateState $state
            $resolvedVenv = Assert-IsRepoVenv $VenvDir
            Move-Item -LiteralPath $resolvedVenv -Destination $venvBackupDir
            $exactPython = Ensure-ExactPython
            Repair-Venv $exactPython
            Install-PyDeps
        } else {
            # EAP=Continue: under the file-global Stop, PS5.1 turns a native
            # command's stderr into a terminating error, which would pre-empt
            # these friendly exit-code checks. Only $LASTEXITCODE decides here.
            $prevEap = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            try {
                & $VenvPy -m pip check 2>&1 | Out-Null
                if ($LASTEXITCODE -ne 0) { throw "pip check failed after update." }
                & $VenvPy (Join-Path $PSScriptRoot "verify_installed.py") `
                    --requirements (Join-Path $Root "backend\requirements.txt") 2>&1 | Out-Null
                if ($LASTEXITCODE -ne 0) { throw "installed dependency versions do not match the release pins." }
                & $VenvPy (Join-Path $PSScriptRoot "import_smoke.py") 2>&1 | Out-Null
                if ($LASTEXITCODE -ne 0) { throw "import smoke failed after update." }
            } finally { $ErrorActionPreference = $prevEap }
        }

        # Boot check: backend up = HTTP healthy AND the WS bridge bound and
        # passed its own authenticated self-handshake (app.py exits 1 otherwise).
        $bport = Get-FreePort
        do { $wport = Get-FreePort } while ($wport -eq $bport)
        Note "Boot-checking the updated app (ports $bport/$wport) ..."
        $bootEnv = @{
            BACKEND_PORT = "$bport"; WS_PORT = "$wport"
            SCOUT_NO_BROWSER = "1"; SCOUT_SYNC = "false"; DATA_SOURCE = "demo"
        }
        $oldBootEnv = @{}
        foreach ($k in $bootEnv.Keys) {
            $existing = Get-Item -Path "Env:$k" -ErrorAction SilentlyContinue
            $oldBootEnv[$k] = @{ existed = [bool]$existing; value = $(if ($existing) { $existing.Value } else { "" }) }
            Set-Item -Path "Env:$k" -Value $bootEnv[$k]
        }
        $proc = Start-Process -FilePath $VenvPy -ArgumentList "app.py" `
            -WorkingDirectory (Join-Path $Root "backend") -WindowStyle Hidden -PassThru
        try {
            $healthy = $false
            $deadline = (Get-Date).AddSeconds(45)
            while ((Get-Date) -lt $deadline) {
                if ($proc.HasExited) { break }
                try {
                    $r = Invoke-RestMethod -Uri "http://127.0.0.1:$bport/api/health" -TimeoutSec 2
                    if ($r.ok -and $r.service -eq "valorant-scout" -and $r.wsReady -and
                            [int]$r.wsPort -eq $wport) { $healthy = $true; break }
                } catch { Start-Sleep -Milliseconds 700 }
            }
            if (-not $healthy) { throw "updated backend failed its boot health/WS check." }
        } finally {
            # EAP=Continue: the graceful taskkill often can't kill a boot-check
            # child (that's why /F follows), and under the file-global Stop its
            # native stderr would become a terminating error that fails the whole
            # update. Teardown must never be fatal.
            $prevBootEap = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            try {
                if (-not $proc.HasExited) {
                    & taskkill /PID $proc.Id /T 2>&1 | Out-Null
                    try { [void]$proc.WaitForExit(5000) } catch { }
                    if (-not $proc.HasExited) {
                        Warn2 "boot-check process ignored graceful shutdown; forcing it closed."
                        & taskkill /PID $proc.Id /T /F 2>&1 | Out-Null
                    }
                }
            } finally { $ErrorActionPreference = $prevBootEap }
            foreach ($k in $bootEnv.Keys) {
                if ($oldBootEnv[$k].existed) {
                    Set-Item -Path "Env:$k" -Value $oldBootEnv[$k].value
                } else {
                    Remove-Item -Path "Env:$k" -ErrorAction SilentlyContinue
                }
            }
        }

        # ---- Commit ------------------------------------------------------
        Remove-LegacyCaches
        Save-Markers (Get-SavedRegion)
        Remove-Item -Force $StateFile -ErrorAction SilentlyContinue
        if ($state.venvBackupDir) {
            Remove-Item -Recurse -Force $state.venvBackupDir -ErrorAction SilentlyContinue
        }
        Remove-Item -Recurse -Force $backupDir -ErrorAction SilentlyContinue
        Write-ScoutLog -Log update -Message "updated to v$newVersion"
        Write-Host ""
        Ok "Update complete - now on v$newVersion. Launch with start.bat."
        exit 0
    } catch {
        Fail "Update failed: $($_.Exception.Message)"
        Write-ScoutLog -Log update -Level ERROR -Code VS-UPDATE-001 -Message $_.Exception.Message
        Restore-FromBackup ([pscustomobject]$state)
        exit 1
    }
} catch {
    Fail "Update failed: $($_.Exception.Message)"
    Write-ScoutLog -Log update -Level ERROR -Code VS-UPDATE-001 -Message $_.Exception.Message
    if (Test-Path $StateFile) {
        Write-Host "  Automatic rollback could not finish. Do not launch; re-extract the last release over this folder, then run install.bat." -ForegroundColor Yellow
    } else {
        Write-Host "  Nothing was changed. See $(Join-Path $ScoutDir 'update.log')." -ForegroundColor Yellow
    }
    exit 1
} finally {
    Close-ScoutMutex $appMutex
    Close-ScoutMutex $maintenanceMutex
    if ($lock) { $lock.Close() }
    if ($staging -and (Test-Path $staging)) { Remove-Item -Recurse -Force $staging -ErrorAction SilentlyContinue }
}
