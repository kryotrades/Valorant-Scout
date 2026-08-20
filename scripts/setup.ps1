# One-time developer setup: hooks, merge policy, tools. Idempotent — safe to
# re-run. This is for people working ON Valorant Scout; a user installing it
# runs install.bat instead.

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "common.ps1")

Step "Installing git hooks ..."
& git -C $Root config core.hooksPath .githooks
Ok "hooks: .githooks (pre-commit, commit-msg, pre-push)"

Step "Setting the merge policy ..."
# Rebase always, never merge. These are repo-local, so other checkouts on this
# machine keep whatever they had.
& git -C $Root config pull.rebase true
& git -C $Root config rebase.autoStash true
& git -C $Root config rebase.autosquash true
& git -C $Root config merge.ff only
& git -C $Root config branch.autoSetupRebase always
& git -C $Root config commit.cleanup strip
Ok "rebase, never merge; fast-forward only."

# Deliberately NOT set here: user.name and user.email. This repository takes
# pull requests, and a setup script that silently rewrites a contributor's
# identity is how commits end up authored by the wrong person.
$who = (& git -C $Root config --get user.name)
if (-not $who) { Warn2 "git user.name is not set — set it before committing." }

Step "Checking tools ..."
# Every one of these is a fatal step in lint.ps1, so a missing one is a
# missing check rather than a degraded run. That is the whole point.
$missing = @()
foreach ($tool in "ruff", "mypy", "yamllint", "pip-audit", "typos", "gitleaks",
    "vulture", "shellcheck", "markdownlint-cli2") {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { $missing += $tool }
}
if (-not (Get-Module -ListAvailable PSScriptAnalyzer)) { $missing += "PSScriptAnalyzer" }
if (-not (Test-Path $VenvPy)) { $missing += ".venv (run install.bat)" }

if ($missing.Count -gt 0) {
    Warn2 "missing: $($missing -join ', ')"
    Write-Host ""
    Write-Host "  pip install ruff==0.15.14 mypy==2.1.0 yamllint==1.38.0 pip-audit vulture" -ForegroundColor Gray
    Write-Host "  npm install -g markdownlint-cli2@0.23.2" -ForegroundColor Gray
    Write-Host "  Install-Module PSScriptAnalyzer -Scope CurrentUser" -ForegroundColor Gray
    Write-Host "  winget install gitleaks.gitleaks   # or the release zip from github.com/gitleaks" -ForegroundColor Gray
    Write-Host "  winget install crate-ci.typos      # or cargo install typos-cli" -ForegroundColor Gray
    Write-Host "  winget install koalaman.shellcheck" -ForegroundColor Gray
    Write-Host "  install.bat" -ForegroundColor Gray
    Write-Host ""
    exit 1
}

Ok "ready — run scripts\lint.ps1"
exit 0
