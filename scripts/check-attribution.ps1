param(
    [Parameter(ParameterSetName = "Path", Mandatory = $true)][string]$Path,
    [Parameter(ParameterSetName = "Files", Mandatory = $true)][switch]$Files,
    [string]$Label = "commit message"
)

# The single attribution scanner. commit-msg, pre-push and lint.ps1 all call
# this one file, so the three layers cannot drift apart — which is exactly how
# a policy spread across three copies of a regex leaks.
#
#   scripts\check-attribution.ps1 -Path msg.txt   scan one file as a blob
#   scripts\check-attribution.ps1 -Files          scan every tracked file
#
# Text arrives as a file, never as an argument: a commit message is multi-line
# and full of spaces, and `powershell -File script.ps1 -Text $msg` re-parses it
# as a command line, so the fourth word becomes an unknown positional parameter
# and the scan never runs. It failed open, which is the worst way to fail.
#
# Exits 1 and names the offending pattern on a hit.
#
# Matching is done twice. Once against the text as written, as the regex the
# pattern file says each line is; and once against a flattened form — lowercased
# with every non-alphanumeric character removed — which defeats spacing,
# punctuation and case tricks. The flattened pass cannot do the job alone
# because it flattens the *pattern* too, which silently disarms every line
# containing regex syntax. Homoglyphs are handled separately by rejecting
# Cyrillic and Greek codepoints, since neither belongs in this repository.
#
# Known ceiling: an encoded payload (base64, rot13) cannot be caught by pattern
# matching. That is accepted, not overlooked.

$ErrorActionPreference = "Stop"

$Root = (& git rev-parse --show-toplevel)
if ($LASTEXITCODE -ne 0) { Write-Host "not a git repository" -ForegroundColor Red; exit 2 }
$Root = $Root.Trim()

$patternFile = Join-Path $Root ".githooks\banned-patterns.txt"
if (-not (Test-Path $patternFile)) {
    Write-Host "missing .githooks\banned-patterns.txt" -ForegroundColor Red
    exit 2
}

# Files exempt from the -Files scan. Each names a banned string for a reason
# that is the opposite of crediting anything, and there is no third pass that
# could tell the difference automatically.
#
#   banned-patterns.txt  is the list itself; it would flag itself.
#   .gitignore           and the two release scripts name a tool's config
#                        directory in order to keep it out of the repository
#                        and out of the shipped artifact.
#   docs/screenshots/    are rendered assets. cli.svg carries "Generated with
#                        Rich", which is the renderer crediting itself on a
#                        screenshot, not a claim about who wrote the code.
$SelfExempt = '^(\.githooks/banned-patterns\.txt|\.gitignore' +
'|scripts/(build|verify)-release\.ps1|scripts/check-attribution\.ps1' +
'|docs/screenshots/)'

$Patterns = @()
foreach ($line in (Get-Content -LiteralPath $patternFile -Encoding UTF8)) {
    $p = $line.Trim()
    if (-not $p -or $p.StartsWith("#")) { continue }
    $Patterns += $p
}

function Convert-Flat([string]$s) {
    return ( -join ($s.ToLowerInvariant().ToCharArray() |
                Where-Object { [char]::IsLetterOrDigit($_) }))
}

# Cyrillic (U+0400-04FF) and Greek (U+0370-03FF) appear in this attack only to
# impersonate Latin letters. Applied to commit messages, refs and tags — where
# those scripts have no business — but not to file contents, in case a comment
# or a map name legitimately carries one.
function Test-Homoglyph([string]$s) {
    return [regex]::IsMatch($s, '[Ѐ-ӿͰ-Ͽ]')
}

function Find-Attribution([string]$label, [string]$text, [switch]$Strict) {
    if ($Strict -and (Test-Homoglyph $text)) {
        Write-Host "$label contains Cyrillic or Greek characters, which are used" -ForegroundColor Red
        Write-Host "here only to impersonate Latin letters. Rewrite it in ASCII." -ForegroundColor Red
        return $true
    }
    $flat = Convert-Flat $text
    foreach ($pattern in $Patterns) {
        $flatPattern = Convert-Flat $pattern
        $hit = [regex]::IsMatch($text, $pattern, 'IgnoreCase')
        if (-not $hit -and $flatPattern) { $hit = $flat.Contains($flatPattern) }
        if ($hit) {
            Write-Host "$label contains a banned attribution pattern: $pattern" -ForegroundColor Red
            return $true
        }
    }
    return $false
}

if ($Files) {
    $bad = $false
    $tracked = & git -C $Root ls-files
    foreach ($rel in $tracked) {
        if ($rel -match $SelfExempt) { continue }
        $full = Join-Path $Root ($rel -replace '/', '\')
        if (-not (Test-Path -LiteralPath $full)) { continue }
        # A PNG trips any byte pattern eventually. Only text is scanned, and
        # "text" means "no NUL in the first 8 KB", the same test git uses.
        $bytes = [System.IO.File]::ReadAllBytes($full)
        $probe = [Math]::Min($bytes.Length, 8192)
        $binary = $false
        for ($i = 0; $i -lt $probe; $i++) { if ($bytes[$i] -eq 0) { $binary = $true; break } }
        if ($binary) { continue }
        $content = [System.Text.Encoding]::UTF8.GetString($bytes)
        if (Find-Attribution $rel $content) { $bad = $true }
    }
    if ($bad) { exit 1 }
    exit 0
}

if (-not (Test-Path -LiteralPath $Path)) {
    Write-Host "no such file: $Path" -ForegroundColor Red
    exit 2
}
if (Find-Attribution $Label ([System.IO.File]::ReadAllText($Path)) -Strict) { exit 1 }
exit 0
