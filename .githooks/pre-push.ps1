param([string]$Remote = "origin")

# The rebase and squash policy, enforced rather than documented.
#
#   1. never push a branch that is behind the base branch — rebase, always
#   2. never push a merge commit; history here is linear
#   3. keep any push to as few commits as it takes to tell a coherent story
#   4. no banned attribution in any commit being pushed, author OR committer,
#      nor in an annotated tag or a branch name
#   5. the full lint suite passes
#
# Override for a genuine emergency: SCOUT_SKIP_HOOKS=1. It prints, so it is
# never silent.

$ErrorActionPreference = "Stop"

if ($env:SCOUT_SKIP_HOOKS -eq "1") {
    Write-Host "! pre-push hooks skipped via SCOUT_SKIP_HOOKS" -ForegroundColor Yellow
    exit 0
}

$Root = (& git rev-parse --show-toplevel).Trim()
$Scan = Join-Path $Root "scripts\check-attribution.ps1"

function Get-Policy([string]$Key, [string]$Fallback) {
    $value = (& git config --get "scout.$Key")
    if ($LASTEXITCODE -ne 0 -or -not $value) {
        $value = (& git config --file (Join-Path $Root ".githooks\policy") --get "scout.$Key")
    }
    if ($LASTEXITCODE -ne 0 -or -not $value) { return $Fallback }
    return $value.Trim()
}

$MaxCommits = [int](Get-Policy "maxCommits" "5")
$BaseBranch = Get-Policy "baseBranch" "main"

function Deny([string]$Reason, [string[]]$Detail) {
    Write-Host ""
    Write-Host "push rejected: $Reason" -ForegroundColor Red
    Write-Host ""
    foreach ($d in $Detail) { Write-Host "  $d" }
    Write-Host ""
    exit 1
}

# Through a file, not an argument: see the note at the top of the scanner.
function Test-Attribution([string]$Text, [string]$Label) {
    $file = Join-Path $env:TEMP ("vs-scan-" + [Guid]::NewGuid().ToString("N") + ".txt")
    [System.IO.File]::WriteAllText($file, $Text)
    try {
        & powershell -NoProfile -ExecutionPolicy Bypass -File $Scan -Path $file -Label $Label
    }
    finally { Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue }
    return ($LASTEXITCODE -eq 0)
}

$Zero = "0000000000000000000000000000000000000000"

& git fetch --quiet $Remote $BaseBranch 2>$null
$upstream = "$Remote/$BaseBranch"
& git rev-parse --verify --quiet $upstream > $null
if ($LASTEXITCODE -ne 0) { $upstream = "" }

foreach ($line in ([Console]::In.ReadToEnd() -split "`r?`n")) {
    $parts = @($line -split '\s+' | Where-Object { $_ })
    if ($parts.Count -lt 4) { continue }
    $localRef, $localSha, $remoteRef, $remoteSha = $parts[0], $parts[1], $parts[2], $parts[3]
    if ($localSha -eq $Zero) { continue }   # branch deletion

    # An annotated tag carries a message that `git log` over the range never sees.
    if ($remoteRef -like "refs/tags/*") {
        $tagText = (& git cat-file -p $localSha) -join "`n"
        if (-not (Test-Attribution "$tagText$localRef" "the tag being pushed")) {
            Deny "the tag being pushed contains a banned attribution" @()
        }
        continue
    }

    if (-not (Test-Attribution $localRef "the branch name")) {
        Deny "the branch name contains a banned attribution" @("Rename it:  git branch -m <new-name>")
    }

    $pushingBase = ($remoteRef -eq "refs/heads/$BaseBranch")

    if ($upstream -and -not $pushingBase) {
        & git merge-base --is-ancestor $upstream $localSha
        if ($LASTEXITCODE -ne 0) {
            $behind = (& git rev-list --count "$localSha..$upstream").Trim()
            Deny "branch is $behind commit(s) behind $upstream" @(
                "Policy is to rebase, never merge:",
                "  git fetch $Remote && git rebase $upstream")
        }
    }

    if ($remoteSha -eq $Zero -and $upstream -and -not $pushingBase) { $range = "$upstream..$localSha" }
    elseif ($remoteSha -eq $Zero) { $range = $localSha }
    else { $range = "$remoteSha..$localSha" }

    $commits = @((& git rev-list $range) | Where-Object { $_ })
    if ($commits.Count -eq 0) { continue }

    $merges = @((& git rev-list --merges $range) | Where-Object { $_ })
    if ($merges.Count -gt 0) {
        Deny "this push contains merge commits" @(
            "History is linear here. Rebase instead:",
            "  git rebase $(if ($upstream) { $upstream } else { $BaseBranch })")
    }

    if ($commits.Count -gt $MaxCommits) {
        Deny "this push has $($commits.Count) commits; the limit is $MaxCommits" @(
            "Squash to the fewest commits that still read as a coherent story:",
            "  git rebase -i $(if ($upstream) { $upstream } else { $BaseBranch })",
            "Raise the limit only with a reason: git config scout.maxCommits N")
    }

    $text = (& git log --format="%B%n%an%n%ae%n%cn%n%ce" $range) -join "`n"
    if (-not (Test-Attribution $text "a commit being pushed")) {
        Deny "a commit being pushed contains a banned attribution" @(
            "Rewrite the history before pushing:",
            "  git rebase -i $(if ($upstream) { $upstream } else { $BaseBranch })")
    }
}

Write-Host "running full lint before push" -ForegroundColor White
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "scripts\lint.ps1")
exit $LASTEXITCODE
