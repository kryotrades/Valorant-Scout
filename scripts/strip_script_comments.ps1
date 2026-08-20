param(
    [Parameter(Mandatory = $true)][string]$Root,
    [switch]$Check
)

$ErrorActionPreference = "Stop"
$utf8Bom = New-Object System.Text.UTF8Encoding($true)
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$changed = 0
$checked = 0

foreach ($file in (Get-ChildItem -LiteralPath $Root -Recurse -File |
            Where-Object { $_.Extension -eq ".ps1" })) {
    $text = [System.IO.File]::ReadAllText($file.FullName)
    $tokens = $null
    $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseInput(
        $text, [ref]$tokens, [ref]$errors
    )
    if ($errors.Count -gt 0) {
        throw "PowerShell parse failed before comment stripping: $($file.FullName)"
    }
    $comments = @($tokens | Where-Object {
            $_.Kind -eq [System.Management.Automation.Language.TokenKind]::Comment
        } | Sort-Object { $_.Extent.StartOffset } -Descending)
    if ($Check -and $comments.Count -gt 0) {
        throw "PowerShell comments remain: $($file.FullName)"
    }
    if (-not $Check -and $comments.Count -gt 0) {
        foreach ($token in $comments) {
            $start = $token.Extent.StartOffset
            $length = $token.Extent.EndOffset - $start
            $text = $text.Remove($start, $length)
        }
        $lines = @($text -split "\r?\n" | ForEach-Object { $_.TrimEnd() })
        $text = (($lines -join "`r`n").TrimEnd() + "`r`n")
        [System.IO.File]::WriteAllText($file.FullName, $text, $utf8Bom)
        $changed++
    }
    $checked++
}

foreach ($file in (Get-ChildItem -LiteralPath $Root -Recurse -File |
            Where-Object { $_.Extension -eq ".bat" })) {
    $text = [System.IO.File]::ReadAllText($file.FullName)
    $lines = @($text -split "\r?\n")
    $commentLines = @($lines | Where-Object { $_ -match '^\s*(?i:rem(?:\s|$)|::)' })
    if ($Check -and $commentLines.Count -gt 0) {
        throw "Batch comments remain: $($file.FullName)"
    }
    if (-not $Check -and $commentLines.Count -gt 0) {
        $kept = @($lines | Where-Object { $_ -notmatch '^\s*(?i:rem(?:\s|$)|::)' })
        $text = (($kept -join "`r`n").TrimEnd() + "`r`n")
        [System.IO.File]::WriteAllText($file.FullName, $text, $utf8NoBom)
        $changed++
    }
    $checked++
}

if ($Check) {
    Write-Host "verified $checked PowerShell/batch files contain no comments"
}
else {
    Write-Host "stripped comments from $changed of $checked PowerShell/batch files"
}
