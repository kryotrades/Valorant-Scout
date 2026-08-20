# Fast checks on staged content only. Anything slow belongs in pre-push.

$ErrorActionPreference = "Stop"

if ($env:SCOUT_SKIP_HOOKS -eq "1") {
    Write-Host "! pre-commit hooks skipped via SCOUT_SKIP_HOOKS" -ForegroundColor Yellow
    exit 0
}

$root = (& git rev-parse --show-toplevel).Trim()
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root "scripts\lint.ps1") -Staged
exit $LASTEXITCODE
