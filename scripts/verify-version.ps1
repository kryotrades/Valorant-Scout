. (Join-Path $PSScriptRoot "common.ps1")

# One version, everywhere. VERSION is the source of truth; runtime.json must
# agree; the protocol constant must agree across backend and frontend.

$bad = 0
$v = Get-LocalVersion
$mf = Get-RuntimeManifest

if ($mf.app.version -ne $v) { Fail "runtime.json app.version '$($mf.app.version)' != VERSION '$v'"; $bad = 1 }
else { Ok "VERSION == runtime.json ($v)" }

$ws = Get-Content (Join-Path $Root "backend\ws_server.py") -Raw -Encoding UTF8
if ($ws -match 'PROTOCOL_VERSION\s*=\s*(\d+)') {
    if ([int]$Matches[1] -ne [int]$mf.protocol.version) { Fail "ws_server.py PROTOCOL_VERSION $($Matches[1]) != runtime.json protocol $($mf.protocol.version)"; $bad = 1 }
    else { Ok "backend protocol == runtime.json ($($Matches[1]))" }
} else { Fail "ws_server.py has no PROTOCOL_VERSION"; $bad = 1 }

$transport = Join-Path $Root "frontend\lib\transport.js"
if (Test-Path $transport) {
    $t = Get-Content $transport -Raw -Encoding UTF8
    if ($t -match 'PROTOCOL_VERSION\s*=\s*(\d+)') {
        if ([int]$Matches[1] -ne [int]$mf.protocol.version) { Fail "transport.js PROTOCOL_VERSION $($Matches[1]) != runtime.json protocol $($mf.protocol.version)"; $bad = 1 }
        else { Ok "frontend protocol == runtime.json ($($Matches[1]))" }
    } else { Fail "transport.js has no PROTOCOL_VERSION"; $bad = 1 }

    # APP_LATEST is "newest published release" — it may lag VERSION but must never lead it.
    $dash = Get-Content (Join-Path $Root "frontend\components\Dashboard.js") -Raw -Encoding UTF8
    if ($dash -match 'APP_LATEST\s*=\s*"([^"]+)"') {
        if ((Compare-ScoutVersion $Matches[1] $v) -gt 0) { Fail "frontend APP_LATEST '$($Matches[1])' is AHEAD of VERSION '$v'"; $bad = 1 }
        else { Ok "frontend APP_LATEST ($($Matches[1])) <= VERSION" }
    }
} else {
    Ok "slim tree (no frontend) - frontend checks skipped"
}

exit $bad
