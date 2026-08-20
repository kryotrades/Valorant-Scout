@{
    # PSScriptAnalyzer — the PowerShell half of scripts/lint.ps1. Root only,
    # for the same reason ruff.toml is: a settings file in a subdirectory
    # weakens the rules for that directory and nobody reviewing a diff sees it.
    #
    # Everything is on except the rules below, and every exclusion has its
    # reason written next to it. Adding one without a reason is how a lint
    # config turns into a list of things that were once annoying.
    IncludeDefaultRules = $true
    Severity            = @('Error', 'Warning', 'Information')

    ExcludeRules = @(
        # These scripts ARE the user interface. install.bat and start.bat open
        # a console and the coloured Step/Ok/Note/Fail lines are what the user
        # reads; Write-Output would put them in the pipeline, where a caller
        # capturing a function's return value would swallow them. Since
        # PowerShell 5 Write-Host writes to the information stream, so it is
        # redirectable anyway.
        'PSAvoidUsingWriteHost'

        # Fourteen `catch { }` blocks, and each is a best-effort path whose
        # failure is genuinely uninteresting: releasing a mutex that is already
        # released, reading a free-space figure for a diagnostic line, probing
        # a Python that may be a Store alias. The alternative is fourteen log
        # lines nobody will ever read.
        'PSAvoidUsingEmptyCatchBlock'

        # Get-PythonCandidates returns candidates; Test-Markers checks the
        # markers; Install-PyDeps installs the dependency set. The singular
        # form would misdescribe every one of them.
        'PSUseSingularNouns'

        # -WhatIf on an internal helper that is only ever called by install.ps1
        # or update.ps1, both of which are already the confirmation step.
        'PSUseShouldProcessForStateChangingFunctions'
    )
}
