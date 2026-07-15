param(
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$Output,  # folder to write the three assets into
    [switch]$AllowDirty                             # local testing only; publishing must stay clean
)

# Builds the public slim release artifact from an explicit ALLOWLIST of
# tracked files. Produces exactly the three assets the updater consumes:
#   valorant-scout-v<version>-windows-source.zip   (single root folder)
#   release-manifest.json                          (version, commit, file hashes)
#   SHA256SUMS.txt
# Then verifies its own output by extracting elsewhere and re-hashing.

. (Join-Path $PSScriptRoot "common.ps1")

Add-Type -AssemblyName System.IO.Compression.FileSystem

# ---- the public payload allowlist ------------------------------------------
$AllowExact = @(
    ".gitattributes", ".gitignore", "LICENSE", "README.md", "VERSION",
    "runtime.json", "install.bat", "start.bat", "UPDATE.bat",
    "run.py", "cli.py"
)
$AllowPrefix = @("assets/", "backend/", "docs/", "scripts/")

# Never allowed in the artifact regardless of the allowlist above.
$ForbiddenPatterns = @(
    '(^|/)\.env$', '\.env\.local', '(^|/)frontend/', '(^|/)node_modules/',
    '(^|/)__pycache__/', '\.pyc$', '(^|/)\.venv/', '(^|/)\.scout/',
    '(^|/)backend/data/', '(^|/)\.next/', '(^|/)\.git/', '(^|/)ops/',
    '(^|/)vendor/', '(^|/)tests/', '(^|/)\.github/', '(^|/)\.claude/'
)

# Text patterns that mean a secret or a developer-machine path leaked in.
$SecretScans = @(
    @{ Name = "private key";            Pattern = 'BEGIN (RSA|EC|OPENSSH) PRIVATE KEY' },
    @{ Name = "Ably root key";          Pattern = '\b[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}:[A-Za-z0-9_\-]{16,}\b' },
    @{ Name = "Supabase service key";   Pattern = 'eyJ[A-Za-z0-9_\-]{30,}\.[A-Za-z0-9_\-]{30,}' },
    @{ Name = "developer absolute path"; Pattern = '[A-Za-z]:\\Users\\(?!Public)[A-Za-z0-9._ -]+\\' },
    # built from parts so this scanner (which ships in the artifact) never
    # contains the canary string itself
    @{ Name = "canary marker";          Pattern = ('VS-CANARY' + '-SECRET') }
)

function Fail-Build($m) { Fail $m; exit 1 }

if ((Get-LocalVersion) -ne $Version) { Fail-Build "VERSION file is '$(Get-LocalVersion)' but you asked to build '$Version'." }
$mf = Get-RuntimeManifest
if ($mf.app.version -ne $Version) { Fail-Build "runtime.json app.version is '$($mf.app.version)' but you asked to build '$Version'." }

$commit = (& git -C $Root rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $commit.Length -ne 40) { Fail-Build "couldn't resolve the git commit." }
$dirty = [bool](& git -C $Root status --porcelain)
if ($dirty -and -not $AllowDirty) {
    Fail-Build "working tree is dirty. Commit/stash changes before a release build, or use -AllowDirty only for local testing."
}
if ($dirty) { Warn2 "working tree is DIRTY - this development artifact must not be published." }

Step "Selecting tracked files via the allowlist ..."
# --others --exclude-standard: also pick up allowlisted files that exist but
# aren't committed yet (pre-commit RC builds); .gitignore'd junk stays out.
$tracked = & git -C $Root ls-files --cached --others --exclude-standard
$payload = @()
foreach ($f in $tracked) {
    $take = $false
    if ($AllowExact -contains $f) { $take = $true }
    foreach ($p in $AllowPrefix) { if ($f.StartsWith($p)) { $take = $true } }
    if (-not $take) { continue }
    foreach ($fp in $ForbiddenPatterns) {
        if ($f -match $fp) { Fail-Build "allowlisted file matches a forbidden pattern: $f ($fp)" }
    }
    $payload += $f
}
if ($payload.Count -lt 30) { Fail-Build "suspiciously small payload ($($payload.Count) files) - allowlist broken?" }
Ok "$($payload.Count) files selected."

# ---- stage ------------------------------------------------------------------
$rootFolder = "valorant-scout-v$Version"
$work = Join-Path $env:TEMP ("vs-build-" + [Guid]::NewGuid().ToString("N"))
$stage = Join-Path $work $rootFolder
New-Item -ItemType Directory -Path $stage -Force | Out-Null

try {
    foreach ($f in $payload) {
        $src = Join-Path $Root ($f -replace '/', '\')
        if (-not (Test-Path $src)) { Fail-Build "tracked file missing from working tree: $f" }
        $dst = Join-Path $stage ($f -replace '/', '\')
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
        Copy-Item -Force $src $dst
    }

    Step "Scanning the staged tree for secrets, caches and developer paths ..."
    $textExt = @(".py", ".ps1", ".bat", ".md", ".txt", ".json", ".example", ".gitignore", ".gitattributes")
    foreach ($file in (Get-ChildItem -Path $stage -Recurse -File)) {
        $rel = $file.FullName.Substring($stage.Length + 1) -replace '\\', '/'
        foreach ($fp in $ForbiddenPatterns) {
            if ("/$rel" -match $fp) { Fail-Build "forbidden file in staging: $rel" }
        }
        if ($textExt -contains $file.Extension.ToLower() -or $file.Name -in @(".gitignore", ".gitattributes", "VERSION", "LICENSE")) {
            $content = Get-Content $file.FullName -Raw -Encoding UTF8
            foreach ($scan in $SecretScans) {
                if ($content -match $scan.Pattern) {
                    Fail-Build "possible $($scan.Name) in $rel - refusing to build."
                }
            }
        }
    }
    Ok "No secrets, caches or personal paths found."

    Step "Checking encodings (PS1 = UTF-8 BOM + CRLF, BAT = CRLF) ..."
    foreach ($file in (Get-ChildItem -Path $stage -Recurse -File -Include *.ps1, *.bat)) {
        $bytes = [System.IO.File]::ReadAllBytes($file.FullName)
        $text = [System.IO.File]::ReadAllText($file.FullName)
        if ($file.Extension -eq ".ps1") {
            if ($bytes.Length -lt 3 -or $bytes[0] -ne 0xEF -or $bytes[1] -ne 0xBB -or $bytes[2] -ne 0xBF) {
                Fail-Build "$($file.Name) is not UTF-8 with BOM."
            }
        }
        if (($text -replace "`r`n", "") -match "`n") { Fail-Build "$($file.Name) has LF-only line endings." }
    }
    Ok "Encodings OK."

    # ---- manifest + zip + sums ----------------------------------------------
    Step "Writing release-manifest.json ..."
    $files = [ordered]@{}
    foreach ($file in (Get-ChildItem -Path $stage -Recurse -File | Sort-Object FullName)) {
        $rel = $file.FullName.Substring($stage.Length + 1) -replace '\\', '/'
        $files[$rel] = (Get-FileHash -Algorithm SHA256 -Path $file.FullName).Hash.ToLower()
    }
    $manifest = [ordered]@{
        schemaVersion = 1
        version       = $Version
        commit        = $commit
        dirty         = $dirty
        builtAt       = [DateTime]::UtcNow.ToString("o")
        rootFolder    = $rootFolder
        python        = @{ version = $mf.python.version; arch = $mf.python.arch }
        protocol      = $mf.protocol.version
        files         = $files
    }
    New-Item -ItemType Directory -Force -Path $Output | Out-Null
    $manifestPath = Join-Path $Output "release-manifest.json"
    Write-FileNoBom $manifestPath (($manifest | ConvertTo-Json -Depth 5))

    # the zip carries a copy of the manifest so installed trees know their file list
    Copy-Item -Force $manifestPath (Join-Path $stage "release-manifest.json")

    $zipName = "valorant-scout-v$Version-windows-source.zip"
    $zipPath = Join-Path $Output $zipName
    if (Test-Path $zipPath) { Remove-Item -Force $zipPath }
    Step "Zipping $zipName ..."
    [System.IO.Compression.ZipFile]::CreateFromDirectory($stage, $zipPath,
        [System.IO.Compression.CompressionLevel]::Optimal, $true)

    $sums = @()
    foreach ($asset in @($zipPath, $manifestPath)) {
        $sums += ((Get-FileHash -Algorithm SHA256 -Path $asset).Hash.ToLower() + "  " + (Split-Path -Leaf $asset))
    }
    Write-FileNoBom (Join-Path $Output "SHA256SUMS.txt") (($sums -join "`n") + "`n")

    # ---- self-verify: extract elsewhere and compare -------------------------
    Step "Verifying the built artifact (extract + compare to manifest) ..."
    $verifyDir = Join-Path $work "verify"
    $probe = Join-Path $PSScriptRoot "update_verify.py"
    $pyExe = $VenvPy
    if (-not (Test-Path $pyExe)) {
        $cand = Find-ExactPython
        if (-not $cand) { Fail-Build "no Python available to run the artifact verification." }
        $pyExe = $cand.Exe
    }
    $verifyArgs = @($probe, "--zip", $zipPath, "--sums", (Join-Path $Output "SHA256SUMS.txt"),
        "--manifest", $manifestPath, "--dest", $verifyDir, "--expect-version", $Version,
        "--expect-python", [string]$mf.python.version, "--expect-arch", [string]$mf.python.arch,
        "--supported-protocol", [string]$mf.protocol.version)
    if ($AllowDirty) { $verifyArgs += "--allow-dirty" }
    & $pyExe @verifyArgs
    if ($LASTEXITCODE -ne 0) { Fail-Build "artifact failed its own verification." }

    Write-Host ""
    Ok "Release artifact built and verified:"
    Note "  $zipPath"
    Note "  $manifestPath"
    Note "  $(Join-Path $Output 'SHA256SUMS.txt')"
    Note "  commit $commit$(if ($dirty) { ' (DIRTY TREE)' })"
    exit 0
} finally {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
