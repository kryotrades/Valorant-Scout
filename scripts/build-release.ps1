param(
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$Output,
    [switch]$AllowDirty
)






. (Join-Path $PSScriptRoot "common.ps1")

Add-Type -AssemblyName System.IO.Compression.FileSystem


$AllowExact = @(
    ".gitattributes", ".gitignore", "LICENSE", "README.md", "VERSION",
    "runtime.json", "install.bat", "start.bat", "UPDATE.bat",
    "run.py", "cli.py"
)
$AllowPrefix = @("assets/", "backend/", "docs/")

# Only the scripts a user actually runs. The "scripts/" prefix also shipped
# this file, the release verifier, both comment strippers and the lint suite -
# build tooling in every user's folder, which is noise and a map of the checks.
$AllowScripts = @(
    "scripts/common.ps1", "scripts/install.ps1", "scripts/start.ps1",
    "scripts/update.ps1", "scripts/diagnose.ps1", "scripts/import_smoke.py",
    "scripts/python_probe.py", "scripts/verify_installed.py"
)


$ForbiddenPatterns = @(
    '(^|/)\.env$', '\.env\.local', '(^|/)frontend/', '(^|/)node_modules/',
    '(^|/)__pycache__/', '\.pyc$', '(^|/)\.venv/', '(^|/)\.scout/',
    '(^|/)backend/data/', '(^|/)\.next/', '(^|/)\.git/', '(^|/)ops/',
    '(^|/)vendor/', '(^|/)tests/', '(^|/)\.github/', '(^|/)\.claude/'
)



function Stop-Build($m) { Fail $m; exit 1 }

if ((Get-LocalVersion) -ne $Version) { Stop-Build "VERSION file is '$(Get-LocalVersion)' but you asked to build '$Version'." }
$mf = Get-RuntimeManifest
if ($mf.app.version -ne $Version) { Stop-Build "runtime.json app.version is '$($mf.app.version)' but you asked to build '$Version'." }

$commit = (& git -C $Root rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $commit.Length -ne 40) { Stop-Build "couldn't resolve the git commit." }
$dirty = [bool](& git -C $Root status --porcelain)
if ($dirty -and -not $AllowDirty) {
    Stop-Build "working tree is dirty. Commit/stash changes before a release build, or use -AllowDirty only for local testing."
}
if ($dirty) { Warn2 "working tree is DIRTY - this development artifact must not be published." }

Step "Selecting tracked files via the allowlist ..."


$tracked = & git -C $Root ls-files --cached --others --exclude-standard
$payload = @()
foreach ($f in $tracked) {
    $take = $false
    if ($AllowExact -contains $f) { $take = $true }
    if ($AllowScripts -contains $f) { $take = $true }
    foreach ($p in $AllowPrefix) { if ($f.StartsWith($p)) { $take = $true } }
    if (-not $take) { continue }
    foreach ($fp in $ForbiddenPatterns) {
        if ($f -match $fp) { Stop-Build "allowlisted file matches a forbidden pattern: $f ($fp)" }
    }
    $payload += $f
}
if ($payload.Count -lt 45) { Stop-Build "suspiciously small payload ($($payload.Count) files) - allowlist broken?" }
Ok "$($payload.Count) files selected."


$rootFolder = "valorant-scout-v$Version"
$work = Join-Path $env:TEMP ("vs-build-" + [Guid]::NewGuid().ToString("N"))
$stage = Join-Path $work $rootFolder
New-Item -ItemType Directory -Path $stage -Force | Out-Null

try {
    foreach ($f in $payload) {
        $src = Join-Path $Root ($f -replace '/', '\')
        if (-not (Test-Path $src)) { Stop-Build "tracked file missing from working tree: $f" }
        $dst = Join-Path $stage ($f -replace '/', '\')
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
        Copy-Item -Force $src $dst
    }






    Step "Stripping comments + docstrings from staged code ..."


    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $VenvPy (Join-Path $PSScriptRoot "strip_comments.py") $stage
        if ($LASTEXITCODE -ne 0) { Stop-Build "comment stripping failed - refusing to build." }
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
        (Join-Path $PSScriptRoot "strip_script_comments.ps1") -Root $stage
        if ($LASTEXITCODE -ne 0) { Stop-Build "script comment stripping failed - refusing to build." }

        & $VenvPy -m compileall -q $stage 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { Stop-Build "staged Python does not byte-compile after stripping." }
    }
    finally { $ErrorActionPreference = $prevEap }

    Get-ChildItem -Path $stage -Recurse -Directory -Filter "__pycache__" |
        Remove-Item -Recurse -Force
    Ok "Python, PowerShell and batch comments stripped; staged Python byte-compiles."

    Step "Scanning the staged tree for secrets, caches and developer paths ..."
    foreach ($file in (Get-ChildItem -Path $stage -Recurse -File)) {
        $rel = $file.FullName.Substring($stage.Length + 1) -replace '\\', '/'
        foreach ($fp in $ForbiddenPatterns) {
            if ("/$rel" -match $fp) { Stop-Build "forbidden file in staging: $rel" }
        }
    }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
    (Join-Path $PSScriptRoot "scan-secrets.ps1") -Path $stage
    if ($LASTEXITCODE -ne 0) { Stop-Build "the staged tree contains a secret - refusing to build." }
    Ok "No secrets, caches or personal paths found."

    Step "Checking encodings (PS1 = UTF-8 BOM + CRLF, BAT = CRLF) ..."
    foreach ($file in (Get-ChildItem -Path $stage -Recurse -File -Include *.ps1, *.bat)) {
        $bytes = [System.IO.File]::ReadAllBytes($file.FullName)
        $text = [System.IO.File]::ReadAllText($file.FullName)
        if ($file.Extension -eq ".ps1") {
            if ($bytes.Length -lt 3 -or $bytes[0] -ne 0xEF -or $bytes[1] -ne 0xBB -or $bytes[2] -ne 0xBF) {
                Stop-Build "$($file.Name) is not UTF-8 with BOM."
            }
        }
        if (($text -replace "`r`n", "") -match "`n") { Stop-Build "$($file.Name) has LF-only line endings." }
    }
    Ok "Encodings OK."








    New-Item -ItemType Directory -Force -Path $Output | Out-Null
    $zipName = "valorant-scout-v$Version.zip"
    $zipPath = Join-Path $Output $zipName
    if (Test-Path $zipPath) { Remove-Item -Force $zipPath }
    Step "Zipping $zipName ..."
    [System.IO.Compression.ZipFile]::CreateFromDirectory($stage, $zipPath,
        [System.IO.Compression.CompressionLevel]::Optimal, $false)


    Step "Verifying the built artifact (extract + check) ..."
    $verifyDir = Join-Path $work "verify"
    [System.IO.Compression.ZipFile]::ExtractToDirectory($zipPath, $verifyDir)
    if ((Get-Content (Join-Path $verifyDir "VERSION") -Raw).Trim() -ne $Version) { Stop-Build "zip VERSION != $Version." }
    foreach ($req in @("run.py", "cli.py", "start.bat", "install.bat", "UPDATE.bat",
            "runtime.json", "backend\app.py", "backend\requirements.txt",
            "scripts\common.ps1", "scripts\start.ps1", "scripts\update.ps1")) {
        if (-not (Test-Path (Join-Path $verifyDir $req))) { Stop-Build "zip missing required file: $req" }
    }

    Write-Host ""
    Ok "Release artifact built and verified:"
    Note "  $zipPath"
    Note "  commit $commit$(if ($dirty) { ' (DIRTY TREE)' })"
    exit 0
}
finally {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
