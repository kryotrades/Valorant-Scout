param([Parameter(Mandatory = $true)][string]$MessageFile)

# Rejects a commit message that breaks this repository's conventions. It runs
# before the commit exists, so a bad message is never written and never has to
# be rewritten.
#
#   1. no assistant or tool attribution anywhere in the message, comment lines
#      included (`--cleanup=verbatim` keeps them, so stripping them before the
#      scan would leak)
#   2. a Conventional Commits subject
#   3. subject <= 72 characters, no trailing full stop

$ErrorActionPreference = "Stop"

if ($env:SCOUT_SKIP_HOOKS -eq "1") { exit 0 }

$Root = (& git rev-parse --show-toplevel).Trim()

function Deny([string]$Reason, [string[]]$Detail) {
    Write-Host ""
    Write-Host "commit rejected: $Reason" -ForegroundColor Red
    Write-Host ""
    foreach ($d in $Detail) { Write-Host "  $d" }
    Write-Host ""
    exit 1
}

$raw = [System.IO.File]::ReadAllText($MessageFile)

# `git commit --verbose` appends a scissors line and a diff. Everything after it
# is discarded by git, so it must not be scanned — and the marker is the
# scissors line, not the first `diff --git`, which a crafted message can forge.
$comment = (& git config --get core.commentChar)
if (-not $comment -or $comment -eq "auto") { $comment = "#" }
$scissors = [regex]::Escape($comment) + ' -{6,} >8 -{6,}'
$lines = @($raw -split "`r?`n")
for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match $scissors) { $lines = @($lines[0..([Math]::Max($i - 1, 0))]); break }
}
$full = ($lines -join "`n")

$scanFile = Join-Path $env:TEMP ("vs-msg-" + [Guid]::NewGuid().ToString("N") + ".txt")
[System.IO.File]::WriteAllText($scanFile, $full)
try {
    & powershell -NoProfile -ExecutionPolicy Bypass -File `
    (Join-Path $Root "scripts\check-attribution.ps1") -Path $scanFile -Label "the commit message"
}
finally { Remove-Item -LiteralPath $scanFile -Force -ErrorAction SilentlyContinue }
if ($LASTEXITCODE -ne 0) {
    Deny "the commit message credits a tool or adds a co-author" @(
        "This repository's history is authored by people.",
        "Remove the reference and any co-author trailer, then commit again.")
}

$subject = ""
foreach ($line in $lines) {
    if ($line.StartsWith($comment)) { continue }
    if (-not $line.Trim()) { continue }
    $subject = $line
    break
}
if (-not $subject) { Deny "empty commit message" @() }

$types = "build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test"
if ($subject -notmatch "^($types)(\([a-z0-9._/-]+\))?!?: .+") {
    Deny "subject is not a Conventional Commit" @(
        "Format: <type>(<optional scope>): <description>",
        "Types:  $($types -replace '\|', ', ')",
        "Got:    $subject")
}
if ($subject.Length -gt 72) {
    Deny "subject is $($subject.Length) characters; the limit is 72" @("Got: $subject")
}
if ($subject.EndsWith(".")) {
    Deny "subject must not end with a full stop" @("Got: $subject")
}

exit 0
