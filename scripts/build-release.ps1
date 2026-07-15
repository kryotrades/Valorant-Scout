param(
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$Output,  # folder to write the three assets into
    [switch]$AllowDirty                             # local testing only; publishing must stay clean
)

# Builds the public slim release artifact from an explicit ALLOWLIST of
# tracked files. Produces a single asset the updater consumes:
#   valorant-scout-v<version>-windows-source.zip   (single root folder)
# Then verifies its own output by extracting elsewhere and checking it.

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

    # ---- zip ----------------------------------------------------------------
    # The release is a single source zip — no separate manifest or checksum
    # assets. GitHub serves it over HTTPS and the updater boot-checks + rolls
    # back, so a self-referential checksum added no real protection.
    New-Item -ItemType Directory -Force -Path $Output | Out-Null
    $zipName = "valorant-scout-v$Version-windows-source.zip"
    $zipPath = Join-Path $Output $zipName
    if (Test-Path $zipPath) { Remove-Item -Force $zipPath }
    Step "Zipping $zipName ..."
    [System.IO.Compression.ZipFile]::CreateFromDirectory($stage, $zipPath,
        [System.IO.Compression.CompressionLevel]::Optimal, $true)

    # ---- self-verify: extract elsewhere and sanity-check --------------------
    Step "Verifying the built artifact (extract + check) ..."
    $verifyDir = Join-Path $work "verify"
    [System.IO.Compression.ZipFile]::ExtractToDirectory($zipPath, $verifyDir)
    $vroot = Join-Path $verifyDir $rootFolder
    if (-not (Test-Path $vroot)) { Fail-Build "zip does not contain the expected root folder '$rootFolder'." }
    if ((Get-Content (Join-Path $vroot "VERSION") -Raw).Trim() -ne $Version) { Fail-Build "zip VERSION != $Version." }
    foreach ($req in @("run.py", "cli.py", "start.bat", "install.bat", "UPDATE.bat",
                       "runtime.json", "backend\app.py", "backend\requirements.txt",
                       "scripts\common.ps1", "scripts\start.ps1", "scripts\update.ps1")) {
        if (-not (Test-Path (Join-Path $vroot $req))) { Fail-Build "zip missing required file: $req" }
    }

    Write-Host ""
    Ok "Release artifact built and verified:"
    Note "  $zipPath"
    Note "  commit $commit$(if ($dirty) { ' (DIRTY TREE)' })"
    exit 0
} finally {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
