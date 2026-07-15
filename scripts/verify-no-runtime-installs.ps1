. (Join-Path $PSScriptRoot "common.ps1")

# Policy: the startup path must contain NO installation actions. Only
# install.bat / UPDATE.bat may create venvs, run pip/npm, or build anything.

$bad = 0

# run.py / cli.py: no pip, no venv creation, no npm install/ci/build as
# subprocess arguments (quoted forms are what subprocess actually executes).
foreach ($rel in @("run.py", "cli.py")) {
    $c = Get-Content (Join-Path $Root $rel) -Raw -Encoding UTF8
    # subprocess-argument forms — a bare hint string like "pip install rich" in
    # an error message is fine, actually running pip is not. The os.system(/
    # shell=True forms below catch the string-command bypasses the tokenized
    # patterns miss (e.g. os.system("pip install ..."), subprocess(..., shell=True)).
    foreach ($pat in @('"-m",\s*"pip"', "'-m',\s*'pip'",
                       '"-m",\s*"venv"', "'-m',\s*'venv'",
                       '"install"', "'install'", '"ci"', "'ci'",
                       '"build"', "'build'", 'ensurepip',
                       'os\.system\(', 'shell\s*=\s*True')) {
        if ($c -match $pat) { Fail "$rel contains a runtime install action: $pat"; $bad = 1 }
    }
}

# start.ps1 / start.bat: must never invoke pip, venv, npm or a build.
foreach ($rel in @("scripts\start.ps1", "start.bat")) {
    $c = Get-Content (Join-Path $Root $rel) -Raw -Encoding UTF8
    foreach ($pat in @('pip install', '-m venv', 'npm\s+(install|ci)', 'run build', 'Install-PyDeps', 'Repair-Venv', 'Install-ExactPython', 'Install-NodeDeps', 'Build-Frontend')) {
        if ($c -match $pat) { Fail "$rel contains a runtime install action: $pat"; $bad = 1 }
    }
}

if ($bad -eq 0) { Ok "startup path is install-free (validation + launch only)." }
exit $bad
