#!/usr/bin/env python3
"""
run.py — one-command launcher for Valorant Scout.

What it does:
  1. Loads environment from .env / backend/.env (no extra deps required).
  2. Resolves (and if needed bootstraps) a Python venv with the backend deps,
     and ensures the frontend's node_modules exist.
  3. Spawns `python backend/app.py` and `npm run dev` in frontend/.
  4. Waits for both to come up, then opens http://localhost:<FRONTEND_PORT>/.

The live scoreboard needs no PUUID — your running client identifies you. With
VALORANT closed it shows a demo lobby so the UI is always explorable.

Press Ctrl+C to stop everything.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
IS_WIN = os.name == "nt"

# The slim "backend + launcher" download ships without the Next.js frontend and
# runs against the HOSTED site instead of building it locally. Overridable via
# the FRONTEND_URL env var (e.g. a custom domain).
HOSTED_FRONTEND = "https://valorantscout.vercel.app"


def has_local_frontend() -> bool:
    """True when the Next.js frontend is bundled (full source), False for the
    slim backend-only distribution that uses the hosted site."""
    return (FRONTEND / "package.json").exists()


# Force UTF-8 on our own stdout/stderr so the » / → glyphs in the banners never
# crash the launcher on a legacy code page (cp1252) or when output is piped.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - older Python / non-reconfigurable stream
        pass

C_RED = "\033[38;5;203m"
C_TEAL = "\033[38;5;43m"
C_DIM = "\033[2m"
C_OK = "\033[38;5;78m"
C_WARN = "\033[38;5;214m"
C_END = "\033[0m"


def say(msg, color=C_TEAL):
    print(f"{color}» {msg}{C_END}", flush=True)


def warn(msg):
    print(f"{C_WARN}! {msg}{C_END}", flush=True)


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
def load_env():
    """Minimal .env loader (root then backend/.env); never overrides real env."""
    for path in (ROOT / ".env", BACKEND / ".env"):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)


# ---------------------------------------------------------------------------
# Bootstrap: backend python + deps
# ---------------------------------------------------------------------------
def venv_python() -> Path:
    return ROOT / ".venv" / ("Scripts/python.exe" if IS_WIN else "bin/python")


def ensure_backend_python() -> str:
    """Return a python that can import flask, creating a venv if necessary."""
    py = venv_python()
    if not py.exists():
        say("Creating virtual environment (.venv)…")
        subprocess.run([sys.executable, "-m", "venv", str(ROOT / ".venv")], check=True)

    def has_flask(p) -> bool:
        return subprocess.run(
            [str(p), "-c", "import flask"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        ).returncode == 0

    if not has_flask(py):
        say("Installing backend dependencies…")
        subprocess.run([str(py), "-m", "pip", "install", "--quiet", "--upgrade", "pip"], check=False)
        subprocess.run(
            [str(py), "-m", "pip", "install", "--quiet", "-r", str(BACKEND / "requirements.txt")],
            check=True,
        )
    return str(py)


def npm_cmd() -> str:
    cmd = "npm.cmd" if IS_WIN else "npm"
    if shutil.which(cmd) is None:
        warn("npm not found on PATH. Install Node.js 18+ from https://nodejs.org and retry.")
        sys.exit(1)
    return cmd


def ensure_frontend_deps(npm: str):
    # Reinstall when node_modules is missing OR a newer dependency (added for the
    # hosted-frontend bridge: ably, qrcode.react) hasn't been installed yet.
    needs_install = (
        not (FRONTEND / "node_modules").exists()
        or not (FRONTEND / "node_modules" / "ably").exists()
        or not (FRONTEND / "node_modules" / "qrcode.react").exists()
    )
    if needs_install:
        say("Installing frontend dependencies (first run, this can take a minute)…")
        subprocess.run([npm, "install", "--no-audit", "--no-fund"], cwd=str(FRONTEND), check=True)


# ---------------------------------------------------------------------------
# Health polling
# ---------------------------------------------------------------------------
def wait_http(url: str, timeout: float, label: str) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status < 500:
                    return True
        except Exception:
            time.sleep(0.6)
    warn(f"{label} did not respond at {url} within {int(timeout)}s.")
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def _ensure_pkg(py: str, import_name: str, pip_name: str, why: str):
    ok = subprocess.run([py, "-c", f"import {import_name}"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if not ok:
        say(f"Installing '{pip_name}' {why}…")
        subprocess.run([py, "-m", "pip", "install", "--quiet", pip_name], check=False)


def ensure_rich(py: str):
    _ensure_pkg(py, "rich", "rich", "for the terminal scoreboard")


def run_cli():
    """Launch the terminal scoreboard (no web)."""
    py = ensure_backend_python()
    ensure_rich(py)
    extra = [a for a in sys.argv[1:] if a not in ("--cli", "--no-cli", "--prod")]
    say("Launching terminal scoreboard…", C_OK)
    subprocess.run([py, str(ROOT / "cli.py"), *extra])


def spawn_cli_window(py: str):
    """
    Launch the terminal scoreboard alongside the web app.

    On Windows it opens in its own console so the live, auto-refreshing table
    has a clean surface that the server logs don't trample. Elsewhere we try a
    common terminal emulator, falling back to a background process.
    """
    ensure_rich(py)
    extra = [a for a in sys.argv[1:] if a not in ("--cli", "--no-cli", "--prod")]
    cli = str(ROOT / "cli.py")
    try:
        if IS_WIN:
            subprocess.Popen([py, cli, *extra],
                             creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
            term = next((t for t in ("x-terminal-emulator", "gnome-terminal",
                                     "konsole", "xterm") if shutil.which(t)), None)
            if term:
                subprocess.Popen([term, "-e", py, cli, *extra])
            else:
                subprocess.Popen([py, cli, *extra])
        say("Terminal scoreboard opened in a separate window.", C_OK)
        return True
    except Exception as e:  # noqa: BLE001
        warn(f"Couldn't open the terminal scoreboard ({e}). "
             f"Run it manually with: python run.py --cli")
        return False


def main():
    load_env()

    # `--cli` => terminal scoreboard only (no web). By default we run BOTH the
    # web app and the terminal scoreboard; `--no-cli` opts out of the terminal.
    if "--cli" in sys.argv:
        run_cli()
        return

    with_cli = "--no-cli" not in sys.argv
    # `--prod` => production frontend (build once, then `next start`) instead of
    # the dev server. Used by start.bat for end users; plain `run.py` stays dev.
    prod = "--prod" in sys.argv

    backend_port = os.environ.get("BACKEND_PORT", "5000")
    frontend_port = os.environ.get("FRONTEND_PORT", "3000")

    print(f"{C_RED}{'='*58}{C_END}")
    print(f"{C_RED}  VALORANT SCOUT{C_END}  {C_DIM}web + terminal · live scoreboard · instalock{C_END}")
    print(f"{C_RED}{'='*58}{C_END}")

    source = os.environ.get("DATA_SOURCE", "auto")
    say("Live scoreboard reads your LOCAL VALORANT client — open the game and")
    say("join Agent Select / a match to see real ranks, names & parties.")
    say(f"Otherwise a demo lobby is shown.  (DATA_SOURCE={source})")
    if os.environ.get("RIOT_API_KEY", "").strip():
        say("RIOT_API_KEY found (used by the legacy match-history endpoint).", C_OK)

    # Bootstrap toolchains.
    py = ensure_backend_python()
    _ensure_pkg(py, "pypresence", "pypresence", "for Discord Rich Presence")
    _ensure_pkg(py, "websockets", "websockets>=12,<14", "for the local WebSocket bridge")
    _ensure_pkg(py, "ably", "ably", "for remote/phone mode (optional)")

    local_frontend = has_local_frontend()
    npm = None
    if local_frontend:
        npm = npm_cmd()
        ensure_frontend_deps(npm)
        frontend_url = os.environ.get("FRONTEND_URL", f"http://localhost:{frontend_port}")
    else:
        # Slim download: no bundled Next.js — run the backend against the hosted
        # site. No Node/npm needed; the backend opens the hosted dashboard.
        frontend_url = os.environ.get("FRONTEND_URL", HOSTED_FRONTEND)
        say("No local frontend bundled — using the hosted dashboard.", C_OK)
        say(f"Dashboard host: {frontend_url}")

    child_env = os.environ.copy()
    child_env["BACKEND_PORT"] = backend_port
    child_env["FRONTEND_PORT"] = frontend_port
    child_env["PORT"] = frontend_port  # Next.js dev port
    # The backend opens FRONTEND_URL/dashboard?mode=local&… once the frontend is
    # up, so the bridge token + ws port travel in the URL (not via HTTP).
    child_env["FRONTEND_URL"] = frontend_url

    procs = []
    try:
        # Backend.
        say(f"Starting backend → http://127.0.0.1:{backend_port}")
        procs.append(subprocess.Popen([py, "app.py"], cwd=str(BACKEND), env=child_env))
        if not wait_http(f"http://127.0.0.1:{backend_port}/api/health", 40, "Backend"):
            warn("Continuing anyway — backend may still be starting.")

        # Frontend — only when bundled locally. In the slim download there's no
        # local Next.js: the backend opens the HOSTED dashboard instead.
        if local_frontend:
            if prod:
                if not (FRONTEND / ".next").exists():
                    say("Building frontend for production (first run can take a minute)…")
                    subprocess.run([npm, "run", "build"], cwd=str(FRONTEND), env=child_env,
                                   shell=IS_WIN, check=False)
                say(f"Starting frontend (production) → http://localhost:{frontend_port}")
                procs.append(subprocess.Popen([npm, "run", "start"], cwd=str(FRONTEND), env=child_env,
                                              shell=IS_WIN))
            else:
                say(f"Starting frontend → http://localhost:{frontend_port}")
                procs.append(subprocess.Popen([npm, "run", "dev"], cwd=str(FRONTEND), env=child_env,
                                              shell=IS_WIN))
            wait_http(f"http://127.0.0.1:{frontend_port}", 120, "Frontend")

        # The backend opens the dashboard itself (it holds the bridge session
        # token + ws port) once the frontend is reachable, printing
        # "Open dashboard: <FRONTEND_URL>/dashboard?mode=local&port=…&s=…".
        say(f"Dashboard will open at {frontend_url}/dashboard", C_OK)
        if not local_frontend:
            say("Your browser may ask to allow local-network access — click Allow.", C_WARN)

        # Also launch the terminal scoreboard (unless --no-cli).
        if with_cli:
            spawn_cli_window(py)

        print(f"\n{C_OK}Web app + terminal scoreboard running. Press Ctrl+C to stop.{C_END}\n")
        while True:
            time.sleep(1)
            for p in procs:
                if p.poll() is not None:
                    warn("A child process exited; shutting down.")
                    raise KeyboardInterrupt
    except KeyboardInterrupt:
        print()
        say("Shutting down…", C_WARN)
    finally:
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        for p in procs:
            try:
                p.wait(timeout=5)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        say("Bye.", C_DIM)


if __name__ == "__main__":
    main()
