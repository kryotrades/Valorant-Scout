param(
    [Parameter(Mandatory = $true)][string]$Path
)

# The single secret scanner. build-release.ps1 runs it over the staged tree,
# verify-release.ps1 over the extracted artifact and lint.ps1 over the working
# tree, so all three agree on what a leak looks like. There used to be three
# copies of this list and they had already drifted: only two of them knew about
# the developer-path pattern.
#
# Exits 1 and names the file on a hit.

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $Path)) { Write-Host "  x no such path: $Path" -ForegroundColor Red; exit 2 }

# backend/offline_chat.pem is the localhost TLS certificate the offline-mode
# chat proxy presents to the Riot client. Its key has to be on disk for the
# proxy to terminate TLS at all, it is the same well-known certificate Deceive
# publishes at CERT_URL, and it guards nothing but a loopback socket. It is the
# one file allowed to contain a private key, and it is named here rather than
# excluded by extension so that a *second* key file cannot arrive unnoticed.
$KeyAllowed = @("backend\offline_chat.pem")

$Scans = @(
    # 'BEGIN PRIVATE KEY' (PKCS#8) is deliberately included. The pattern used to
    # read `BEGIN (RSA|EC|OPENSSH) PRIVATE KEY`, which misses the format every
    # modern tool actually emits — openssl genpkey, cryptography, ssh-keygen -m
    # PKCS8 — so the most likely leak was the one shape that got through.
    # Split so this file does not match its own pattern, the same trick the
    # canary below uses.
    @{ Name = "private key"; Pattern = ('BEGIN [A-Z0-9 ]*PRIVATE' + ' KEY') },
    @{ Name = "Ably root key"; Pattern = '\b[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}:[A-Za-z0-9_\-]{16,}\b' },
    @{ Name = "Supabase service key"; Pattern = 'eyJ[A-Za-z0-9_\-]{30,}\.[A-Za-z0-9_\-]{30,}' },
    @{ Name = "Riot API key"; Pattern = 'RGAPI-[0-9a-fA-F-]{20,}' },
    @{ Name = "developer absolute path"; Pattern = '[A-Za-z]:\\Users\\(?!Public)[A-Za-z0-9._ -]+\\' },

    # Split so this file does not match itself; build-release plants the joined
    # string in a scratch file to prove the scan is actually running.
    @{ Name = "canary marker"; Pattern = ('VS-CANARY' + '-SECRET') }
)

# Extension list rather than "every text file": the scanned trees contain
# release zips and screenshots, and a PNG trips a high-entropy pattern
# eventually. Anything that can carry a credential in this repository is here.
$TextExt = @(".py", ".ps1", ".psd1", ".bat", ".cmd", ".md", ".txt", ".json",
    ".example", ".yaml", ".yml", ".cfg", ".toml", ".env", ".pem", ".svg")
$TextName = @(".gitignore", ".gitattributes", "VERSION", "LICENSE", "policy")

$bad = 0
# This file necessarily contains the patterns it looks for, the way
# .githooksanned-patterns.txt does. It is the one exemption.
$SelfExempt = 'scan-secrets\.ps1$'

foreach ($file in (Get-ChildItem -LiteralPath $Path -Recurse -File -Force)) {
    if ($file.FullName -match $SelfExempt) { continue }
    if ($file.FullName -match '\\(\.git|\.venv|node_modules|__pycache__|dist)\\') { continue }
    if (($TextExt -notcontains $file.Extension.ToLowerInvariant()) -and
        ($TextName -notcontains $file.Name)) { continue }
    $rel = $file.FullName.Substring($Path.TrimEnd('\').Length).TrimStart('\')
    $content = [System.IO.File]::ReadAllText($file.FullName)
    foreach ($scan in $Scans) {
        if ($scan.Name -eq "private key" -and ($KeyAllowed -contains $rel)) { continue }
        if ($content -match $scan.Pattern) {
            Write-Host "  x possible $($scan.Name) in $rel" -ForegroundColor Red
            $bad = 1
        }
    }
}

exit $bad
