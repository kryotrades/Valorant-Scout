. (Join-Path $PSScriptRoot "common.ps1")

# backend/requirements.txt is the sole Python lock: every active line must be
# an exact == pin (transitive closure included, verified by pip check in CI).

$bad = 0
$lines = Get-Content (Join-Path $Root "backend\requirements.txt") -Encoding UTF8
$count = 0
foreach ($line in $lines) {
    $code = ($line -split '#', 2)[0].Trim()
    if (-not $code) { continue }
    $count++
    if ($code -notmatch '^[A-Za-z0-9._\[\]-]+==[A-Za-z0-9._+!-]+$') {
        Fail "not an exact pin: '$code'"
        $bad = 1
    }
}
if ($count -lt 10) { Fail "requirements.txt has only $count entries - transitive closure missing?"; $bad = 1 }
if ($bad -eq 0) { Ok "requirements.txt: $count exact pins, no ranges." }
exit $bad
