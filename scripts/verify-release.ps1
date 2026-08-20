param(
    [Parameter(Mandatory = $true)][string]$Zip
)






. (Join-Path $PSScriptRoot "common.ps1")

if (-not (Test-Path $Zip)) { Fail "missing: $Zip"; exit 1 }

$work = Join-Path $env:TEMP ("vs-verify-" + [Guid]::NewGuid().ToString("N"))
try {
    Step "Extracting the zip ..."
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($Zip, $work)


    $tree = $work
    if (-not (Test-Path (Join-Path $tree "VERSION"))) {
        Fail "zip must contain the app files at its root (VERSION not found)."; exit 1
    }
    $version = (Get-Content (Join-Path $tree "VERSION") -Raw).Trim()

    Step "Forbidden-content scan ..."

    $forbidden = @('(^|/)\.env$', '\.env\.local', '(^|/)frontend/', '(^|/)node_modules/',
        '(^|/)__pycache__/', '\.pyc$', '(^|/)\.venv/', '(^|/)\.scout/',
        '(^|/)backend/data/', '(^|/)\.next/', '(^|/)\.git/', '(^|/)ops/',
        '(^|/)vendor/', '(^|/)tests/', '(^|/)\.github/', '(^|/)\.claude/', 'client_id$')
    $bad = @()
    foreach ($file in (Get-ChildItem -Path $tree -Recurse -File)) {
        $rel = $file.FullName.Substring($tree.Length + 1) -replace '\\', '/'
        foreach ($fp in $forbidden) { if ($rel -match $fp) { $bad += "$rel ($fp)" } }
    }
    if ($bad.Count -gt 0) { foreach ($b in $bad) { Fail $b }; exit 1 }
    # The same scanner build-release.ps1 runs over the staged tree, so the
    # artifact is checked against one definition of "secret" rather than the
    # abridged copy this file used to carry.
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
    (Join-Path $PSScriptRoot "scan-secrets.ps1") -Path $tree
    if ($LASTEXITCODE -ne 0) { Fail "the artifact contains a secret."; exit 1 }
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

    Step "Comment-free public-source policy ..."
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $VenvPy (Join-Path $PSScriptRoot "strip_comments.py") --check $tree
        if ($LASTEXITCODE -ne 0) { Fail "Python comments/docstrings remain in the public artifact."; exit 1 }
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
        (Join-Path $PSScriptRoot "strip_script_comments.ps1") -Root $tree -Check
        if ($LASTEXITCODE -ne 0) { Fail "PowerShell or batch comments remain in the public artifact."; exit 1 }
    }
    finally { $ErrorActionPreference = $prevEap }
    Ok "public artifact code is comment-free."

    Write-Host ""
    Ok "Artifact verified: $Zip (v$version)"
    exit 0
}
finally {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
