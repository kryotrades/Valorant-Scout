param(
    [Parameter(Mandatory = $true)][string]$Zip
)

# Independent check of a built release zip: safe archive structure, forbidden
# content, required files and launcher encodings. The release is a single zip
# (no manifest/checksum assets) served over HTTPS, so there is nothing to
# cross-check against - the updater boot-checks and rolls back at apply time.

. (Join-Path $PSScriptRoot "common.ps1")

if (-not (Test-Path $Zip)) { Fail "missing: $Zip"; exit 1 }

$work = Join-Path $env:TEMP ("vs-verify-" + [Guid]::NewGuid().ToString("N"))
try {
    Step "Extracting the zip ..."
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($Zip, $work)

    # Exactly one root folder: valorant-scout-v<version>
    $roots = @(Get-ChildItem -Path $work -Directory)
    if ($roots.Count -ne 1 -or $roots[0].Name -notmatch '^valorant-scout-v(.+)$') {
        Fail "zip must contain a single 'valorant-scout-v<version>' root folder."; exit 1
    }
    $tree = $roots[0].FullName
    $version = $Matches[1]
    if ((Get-Content (Join-Path $tree "VERSION") -Raw).Trim() -ne $version) {
        Fail "VERSION inside the zip != the root-folder version ($version)."; exit 1
    }

    Step "Forbidden-content scan ..."
    # Any-depth patterns, in lockstep with $ForbiddenPatterns in build-release.ps1.
    $forbidden = @('(^|/)\.env$', '\.env\.local', '(^|/)frontend/', '(^|/)node_modules/',
                   '(^|/)__pycache__/', '\.pyc$', '(^|/)\.venv/', '(^|/)\.scout/',
                   '(^|/)backend/data/', '(^|/)\.next/', '(^|/)\.git/', '(^|/)ops/',
                   '(^|/)vendor/', '(^|/)tests/', '(^|/)\.github/', '(^|/)\.claude/', 'client_id$')
    $bad = @()
    foreach ($file in (Get-ChildItem -Path $tree -Recurse -File)) {
        $rel = $file.FullName.Substring($tree.Length + 1) -replace '\\', '/'
        foreach ($fp in $forbidden) { if ($rel -match $fp) { $bad += "$rel ($fp)" } }
        if ($file.Extension -in @(".py", ".ps1", ".bat", ".md", ".json", ".txt", ".example")) {
            $content = Get-Content $file.FullName -Raw -Encoding UTF8
            if ($content -match ('VS-CANARY' + '-SECRET')) { $bad += "$rel (canary secret leaked!)" }
            if ($content -match '[A-Za-z]:\\Users\\(?!Public)[A-Za-z0-9._ -]+\\') { $bad += "$rel (developer absolute path)" }
        }
    }
    if ($bad.Count -gt 0) { foreach ($b in $bad) { Fail $b }; exit 1 }
    Ok "no forbidden files, secrets or personal paths."

    Step "Required files + encodings ..."
    foreach ($req in @("install.bat", "start.bat", "UPDATE.bat", "VERSION",
                       "runtime.json", "run.py", "cli.py", "backend/requirements.txt",
                       "backend/app.py", "scripts/common.ps1", "scripts/install.ps1",
                       "scripts/start.ps1", "scripts/update.ps1", "scripts/diagnose.ps1",
                       "scripts/import_smoke.py")) {
        if (-not (Test-Path (Join-Path $tree ($req -replace '/', '\')))) { Fail "required file missing: $req"; exit 1 }
    }
    foreach ($file in (Get-ChildItem -Path $tree -Recurse -File -Include *.ps1, *.bat)) {
        $bytes = [System.IO.File]::ReadAllBytes($file.FullName)
        $text = [System.IO.File]::ReadAllText($file.FullName)
        if ($file.Extension -eq ".ps1" -and ($bytes.Length -lt 3 -or $bytes[0] -ne 0xEF -or $bytes[1] -ne 0xBB -or $bytes[2] -ne 0xBF)) {
            Fail "$($file.Name) is not UTF-8 with BOM."; exit 1
        }
        if (($text -replace "`r`n", "") -match "`n") { Fail "$($file.Name) has LF-only line endings."; exit 1 }
    }
    Ok "required files present, encodings OK, VERSION agrees."

    Write-Host ""
    Ok "Artifact verified: $Zip (v$version)"
    exit 0
} finally {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
