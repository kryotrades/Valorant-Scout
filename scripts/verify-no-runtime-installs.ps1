. (Join-Path $PSScriptRoot "common.ps1")




$bad = 0



foreach ($rel in @("run.py", "cli.py")) {
    $c = Get-Content (Join-Path $Root $rel) -Raw -Encoding UTF8




    foreach ($pat in @('"-m",\s*"pip"', "'-m',\s*'pip'",
            '"-m",\s*"venv"', "'-m',\s*'venv'",
            '"install"', "'install'", '"ci"', "'ci'",
            '"build"', "'build'", 'ensurepip',
            'os\.system\(', 'shell\s*=\s*True')) {
        if ($c -match $pat) { Fail "$rel contains a runtime install action: $pat"; $bad = 1 }
    }
}


foreach ($rel in @("scripts\start.ps1", "start.bat")) {
    $c = Get-Content (Join-Path $Root $rel) -Raw -Encoding UTF8
    foreach ($pat in @('pip install', '-m venv', 'npm\s+(install|ci)', 'run build', 'Install-PyDeps', 'Repair-Venv', 'Install-ExactPython', 'Install-NodeDeps', 'Invoke-FrontendBuild')) {
        if ($c -match $pat) { Fail "$rel contains a runtime install action: $pat"; $bad = 1 }
    }
}

if ($bad -eq 0) { Ok "startup path is install-free (validation + launch only)." }
exit $bad
