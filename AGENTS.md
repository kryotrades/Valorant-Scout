# AGENTS.md

Valorant Scout reads the VALORANT client running on this PC and renders the
lobby it finds: ranks, peaks, parties, K/D, smurf risk, skins. A Flask backend,
a Rich terminal scoreboard, a Discord presence, and a bridge to the hosted
dashboard at valorantscout.com. Nothing about a match leaves the machine.

This file carries only what you cannot infer from the tree. Read the code for
everything else.

## Commands

```powershell
scripts\setup.ps1          # once: hooks, merge policy, tool check
scripts\lint.ps1           # every check there is. Must pass before you push.
scripts\lint.ps1 -Fix      # apply the safe automatic fixes first
scripts\lint.ps1 -Staged   # staged files only (what pre-commit runs)

install.bat                # user-facing setup: pinned CPython + .venv + deps
start.bat                  # user-facing launch
python run.py --cli --once # one frame of the terminal scoreboard, then exit

scripts\build-release.ps1 -Version 2.1.0 -Output dist
scripts\verify-release.ps1 -Zip dist\valorant-scout-v2.1.0.zip
```

`scripts\lint.ps1` is the only entry point. The git hooks call it and nothing
else, so there is one definition of ready. There is no hosted CI: every check
needs Windows, three of them need the repository's own `.venv`, and all of them
finish in seconds on the machine that made the change.

Twenty-three steps, and every one of them fatal. The tools are ruff (with
`select = ["ALL"]`, not a hand-picked list) and `ruff format`, mypy at
`strict = True`, PSScriptAnalyzer and its formatter, markdownlint, yamllint,
typos, shellcheck, vulture, gitleaks over both the working tree and the
history, and pip-audit against the pinned lockfile. The rest are checks this
repository needs and no off-the-shelf tool provides: the policy rules below,
the encodings, the four module self-checks, the release comment-strip round
trip, and the three `verify-*.ps1` scripts that were already in the tree with
nothing calling them.

Two steps need the network: `pip-audit` resolves the pins against PyPI's
advisory database, and `inventory.py`'s self-check re-derives the collection
price table from valorant-api.com. Both are worth it. pip-audit found three
live advisories the first time it ran.

**Not included, and why.** `deptry` reads every sibling import in `backend/` as
a missing dependency, because the package is imported by path rather than
installed; configuring it around that means a hand-maintained list of 25 module
names. `editorconfig-checker` disagrees with `Invoke-Formatter` about
PowerShell continuation lines, and the encoding half of its job is already done
more precisely by the `encodings` step, which knows about the BOM rule. Adding
either would mean turning off the rule that makes it worth having.

Layout is `ruff format`'s problem, not yours. `lint.ps1` runs
`ruff format --check` and fails on a difference, so there is nothing to argue
about in review and nothing to align by hand — write it however, run
`scripts\lint.ps1 -Fix`, and it comes back in the house style. `ruff.toml`
carries no `[format]` section on purpose: those are the defaults, and a setting
written down is a setting somebody will change.

Windows PowerShell 5.1, not `pwsh`. The product launches `powershell` from
`install.bat` and `start.bat`, and a developer tool that needs a newer shell
than the product does is a tool people stop running.

## Rules that are not negotiable

**Windows is the only target.** VALORANT's anti-cheat is a Windows kernel
driver; there is no macOS build, no Linux build, and Proton does not run it. A
platform branch is therefore not portability, it is a second code path nobody
can reach, nobody tests and nobody deletes. `lint.ps1` rejects `os.name`,
`sys.platform`, `$IsLinux`/`$IsMacOS` and friends outright. Do not add one back
"just in case" — there is no case.

**Attribution.** No commit message, trailer, branch name, tag or tracked file
credits a tool or adds a co-author. The patterns are in
`.githooks/banned-patterns.txt`; `scripts\check-attribution.ps1` is the single
scanner and `commit-msg`, `pre-push` and `lint.ps1` all call it, so the three
layers cannot drift apart.

**Rebase, never merge.** If your branch is behind the base branch, rebase onto
it before pushing. `pre-push` enforces this and rejects merge commits.

**Squash.** Keep a branch to the fewest commits that still read as a coherent
story. Hard limit 5 (`git config scout.maxCommits`). Conventional Commits
subject, at most 72 characters, no trailing full stop.

**No inline lint suppressions.** `# noqa`, `# type: ignore`, `# nosec`,
`SuppressMessageAttribute`, `<!-- markdownlint-disable -->` and friends are
rejected by `lint.ps1`. If a rule is genuinely wrong here, argue it in
`ruff.toml` or `PSScriptAnalyzerSettings.psd1`, where the exception is visible
in a diff and has its reason written next to it. Every existing one does.

**Select everything, then argue.** `ruff.toml` says `select = ["ALL"]` rather
than naming families, because naming them is how a rule that would have caught
something never gets the chance. This config used to list 37 families and
ignore `BLE001` and `C901` — neither of whose family was in the list, so both
ignores were dead and both rules had never run once. An ignore whose rule is
not selected is not an exception, it is a comment.

**Everything is annotated.** mypy runs at `strict = True` over every file, with
three relaxations argued in `mypy.ini`. That is the check that makes a wrong
type a lint failure instead of a runtime surprise, and it is worth more than it
looks: annotating the signatures was a review of what they actually return, and
`_current_players` turned out to return a 4-tuple, `kd_hs` five values, and
`fetch_rank` a pair it had been declared to return nothing from.

**Shared code goes in `backend/common/`.** Not on the theory that something
might be shared one day — every function in there replaced at least two copies
that already existed and had started to drift. The four `_save()` bodies were
identical except for a temp-file prefix, and two of them wrote non-ASCII
differently from the other two. Four modules had their own `_log`, and two
honoured `SCOUT_QUIET` while two did not.

**No nested lint config.** Lint configuration lives at the repository root
only. A config in a subdirectory is a suppression nobody can see. `lint.ps1`
looks on disk as well as in the index, because an untracked `pyproject.toml` is
read by ruff exactly the same way a tracked one is.

**Nothing may email the maintainer.** No workflows, no Dependabot config, no
CODEOWNERS, no scheduled job, no `mailto:`. Riot's XMPP identifiers look like
addresses and are exempt; nothing else is.

**A missing tool is a failure, not a skip.** `lint.ps1` fails if a linter is
absent rather than silently dropping that check. A check that quietly
disappears is worse than no check, because the green tick still appears.

**The source keeps its comments; the artifact does not.** `build-release.ps1`
strips every comment and docstring from a *staged copy* before zipping, and
`verify-release.ps1` proves the artifact is comment-free. The repository itself
is not subject to that policy and should not be — it was run in place at some
point, which is why so little of this code explains itself and why a hundred
vestigial `pass` statements were sitting where docstrings used to be. Write the
comment. `lint.ps1` runs both strippers over a throwaway copy on every push, so
a comment can never be the thing that breaks a release.

## Constraints that shape design decisions

- **Nothing about a match leaves the machine.** The hosted dashboard is only a
  UI; it connects back over a local token-authenticated WebSocket. There is no
  server-side store, no account and no telemetry beyond `sync.py`'s opt-in
  ping. Anything that would send match data somewhere does not ship.
- **Riot's local API is undocumented and moves with every patch.** A failing
  call is a supported state, never a crash: the board holds its last good copy
  for `_HOLD_SECS` and shows a notice. This is why `except Exception` is
  everywhere and why `BLE001` is off in `ruff.toml`. It is a deliberate design,
  not laziness — but the failure must still be *logged*, or a silently empty
  scoreboard looks exactly like an empty lobby.
- **Anything that acts on the game defaults to dry run.** Instalock, dodge and
  queue control all take `dryRun` and default it to `true`. Keep it that way;
  the endpoint that does something to somebody's ranked match should need an
  explicit opt-out.
- **The startup path installs nothing.** `start.bat` validates and launches; it
  never pips, venvs or npms. `verify-no-runtime-installs.ps1` enforces this by
  grepping `start.ps1` and `run.py` for install verbs — if you rename an
  installing function, update that list or the guard stops guarding.
- **Every dependency is pinned exactly, including transitives.**
  `backend\requirements.txt` doubles as the constraints file, and
  `verify_installed.py` refuses to launch against a version that does not
  match. A range is a different program on somebody else's PC.
- **`tzdata` is a real dependency on Windows.** Windows ships no tz database,
  so `zoneinfo.ZoneInfo("UTC")` raises `ModuleNotFoundError` — not
  `ZoneInfoNotFoundError` — when the package is absent. Without it
  `/api/insights` and `/api/performance` return 500 on every clean install.
  It is pinned, `import_smoke.py` checks it, and `_valid_timezone` falls back
  to `datetime.UTC` anyway.

## Verifying a change end to end

`lint.ps1` does not talk to VALORANT. Demo mode does, in the sense that it
exercises the whole render path with a generated lobby:

```powershell
$env:DATA_SOURCE = "demo"
python cli.py --once            # one frame, ten players, both teams
```

For the backend, start it and walk the endpoints — every one of these answers
with VALORANT closed:

```powershell
$env:DATA_SOURCE = "demo"; $env:SCOUT_NO_BROWSER = "1"
python backend\app.py
# /api/health /api/live /api/agents /api/recap /api/insights
# /api/performance /api/settings /api/queue
```

`/api/insights` and `/api/performance` are the two worth checking by hand after
any dependency change: they are the only endpoints that reach for the tz
database, and they are the ones that were returning 500.

Then the release pipeline, which is its own kind of test — it strips, scans and
byte-compiles the whole tree:

```powershell
scripts\build-release.ps1 -Version 2.1.0 -Output dist -AllowDirty
scripts\verify-release.ps1 -Zip dist\valorant-scout-v2.1.0.zip
```

## The self-checks

Four modules end in an assert-based `if __name__ == "__main__"` block:
`history.py`, `inventory.py`, `offline_launch.py` and `scout_commands.py`. They
were written, they pass, and until `lint.ps1` got a step for them nothing had
ever run one. Add to them rather than starting a test framework.

`inventory.py`'s re-derives the collection-value price table from
valorant-api.com and asserts every content tier still costs what `_TIER_VP`
says. Riot repricing a tier makes every collection total silently wrong, and no
local fixture can notice — which is why it is allowed to reach the network.

## Traps

**The XMPP proxy is parsed with string surgery, not an XML parser.** `offline_launch.process_c2s`
takes a byte stream that can split mid-stanza and has to hand back the
unconsumed tail. Its self-check covers the split case, the MUC passthrough and
the non-presence passthrough; run it after touching anything in there.

**`backend/offline_chat.pem` contains a private key on purpose.** It is the
localhost TLS certificate the chat proxy presents to the Riot client — the same
well-known one Deceive publishes — and its key has to be on disk to terminate
TLS at all. `scan-secrets.ps1` names that one file as the exception, so a
*second* key file cannot arrive unnoticed. Note the pattern it uses now matches
a bare PKCS#8 header too — the old one only knew the RSA, EC and OPENSSH
spellings, which meant the format every modern tool actually emits was the one
shape that could leak. This file does not spell the header out, for the same
reason the scanner splits it: it would match itself.

**Encodings are load-bearing.** `.ps1` must be UTF-8 *with* a BOM and CRLF, or
Windows PowerShell misreads the box-drawing characters in `start.ps1`'s
progress bar; `.bat` must be CRLF or `cmd.exe` mishandles it at a line
boundary; nothing else may carry a BOM. `.gitattributes` sets it, `.editorconfig`
tells editors, and `lint.ps1` checks it.

**The git hooks are `sh` shims, and they must stay LF.** git runs hooks through
its bundled `sh`, which refuses a shebang line ending in CR. `.gitattributes`
pins `.githooks/*` to `eol=lf` *before* the `*.ps1` rule, so the `.ps1` half of
each hook still gets CRLF — the last matching line wins.

**markdownlint's anchor slugs disagree with GitHub's.** An emoji carrying a
variation selector (U+FE0F) stays in markdownlint's slug and is dropped by
GitHub's, so `MD051` calls a working nav link broken. It is off, with the
disagreement written down in `.markdownlint-cli2.yaml`. Check the README's nav
bar by hand when you rename a heading; nothing here can do it for you.
