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

HOSTED_FRONTEND = "https://valorantscout.vercel.app"

def has_local_frontend() -> bool:
    pass
    return (FRONTEND / "package.json").exists()

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

if IS_WIN:
    os.system("")

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

def load_env():
    pass
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

def venv_python() -> Path:
    return ROOT / ".venv" / ("Scripts/python.exe" if IS_WIN else "bin/python")

def ensure_backend_python() -> str:
    pass
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

    needs_install = (
        not (FRONTEND / "node_modules").exists()
        or not (FRONTEND / "node_modules" / "ably").exists()
        or not (FRONTEND / "node_modules" / "qrcode.react").exists()
    )
    if needs_install:
        say("Installing frontend dependencies (first run, this can take a minute)…")
        subprocess.run([npm, "install", "--no-audit", "--no-fund"], cwd=str(FRONTEND), check=True)

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

def _pid_exe(pid: int) -> str:
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.c_ulong(1024)
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return buf.value
            return ""
        finally:
            k32.CloseHandle(h)
    except Exception:
        return ""

def _proc_info(pid: int):
    try:
        lines = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"$p = Get-CimInstance Win32_Process -Filter 'ProcessId={pid}'; "
             "$p.ExecutablePath; $p.CommandLine; $p.ParentProcessId"],
            capture_output=True, text=True, timeout=20).stdout.splitlines()
        lines += ["", "", ""]
        exe, cmd, ppid = lines[0].strip(), lines[1].strip(), lines[2].strip()
        return exe, cmd, int(ppid) if ppid.isdigit() else 0
    except Exception:
        return "", "", 0

def _is_ours(pid: int) -> bool:


    root = str(ROOT).lower()
    for _ in range(3):
        exe, cmd, ppid = _proc_info(pid)
        if root in exe.lower() or root in cmd.lower():
            return True
        if not ppid:
            return False
        pid = ppid
    return False

def kill_port(port) -> None:
    pass
    if not IS_WIN:
        return
    out = ""
    for proto in ("TCP", "TCPv6"):
        try:
            out += subprocess.run(["netstat", "-ano", "-p", proto],
                                  capture_output=True, text=True, timeout=15).stdout
        except Exception:
            pass
    me = os.getpid()
    pids = set()
    for line in out.splitlines():
        parts = line.split()

        if len(parts) >= 5 and parts[1].endswith(f":{port}")                and parts[2] in ("0.0.0.0:0", "[::]:0"):
            try:
                pid = int(parts[4])
            except ValueError:
                continue
            if pid not in (0, 4, me):
                pids.add(pid)
    for pid in pids:





        exe = _pid_exe(pid)
        if not (exe.lower().startswith(str(ROOT).lower()) or _is_ours(pid)):
            raise RuntimeError(
                f"Port {port} is already in use by "
                f"{exe or f'another program (PID {pid})'}.\n"
                f"Close that program, or set BACKEND_PORT / WS_PORT to a free port "
                f"in backend\\.env and start again.")
        say(f"Port {port} is held by a previous Valorant Scout instance (PID {pid}) — closing it.", C_DIM)
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def _ensure_pkg(py: str, import_name: str, pip_name: str, why: str):
    ok = subprocess.run([py, "-c", f"import {import_name}"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if not ok:
        say(f"Installing '{pip_name}' {why}…")
        subprocess.run([py, "-m", "pip", "install", "--quiet", pip_name], check=False)

def ensure_rich(py: str):
    _ensure_pkg(py, "rich", "rich", "for the terminal scoreboard")

def run_cli():
    pass
    py = ensure_backend_python()
    ensure_rich(py)
    extra = [a for a in sys.argv[1:] if a not in ("--cli", "--no-cli", "--prod")]
    say("Launching terminal scoreboard…", C_OK)
    subprocess.run([py, str(ROOT / "cli.py"), *extra])

def _hidden_window() -> dict:
    pass
    if not IS_WIN:
        return {}
    return {"creationflags": subprocess.CREATE_NO_WINDOW,
            "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}

def spawn_cli_window(py: str):
    pass
    ensure_rich(py)
    extra = [a for a in sys.argv[1:] if a not in ("--cli", "--no-cli", "--prod")]
    cli = str(ROOT / "cli.py")
    try:
        if IS_WIN:
            proc = subprocess.Popen([py, cli, *extra],
                                    creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
            term = next((t for t in ("x-terminal-emulator", "gnome-terminal",
                                     "konsole", "xterm") if shutil.which(t)), None)
            if term:
                proc = subprocess.Popen([term, "-e", py, cli, *extra])
            else:
                proc = subprocess.Popen([py, cli, *extra])
        say("Terminal scoreboard opened in a separate window.", C_OK)
        return proc
    except Exception as e:
        warn(f"Couldn't open the terminal scoreboard ({e}). "
             f"Run it manually with: python run.py --cli")
        return None

def main():
    load_env()

    if "--cli" in sys.argv:
        run_cli()
        return

    with_cli = "--no-cli" not in sys.argv

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

        frontend_url = os.environ.get("FRONTEND_URL", HOSTED_FRONTEND)
        say("No local frontend bundled — using the hosted dashboard.", C_OK)
        say(f"Dashboard host: {frontend_url}")

    child_env = os.environ.copy()
    child_env["BACKEND_PORT"] = backend_port
    child_env["FRONTEND_PORT"] = frontend_port
    child_env["PORT"] = frontend_port

    child_env["FRONTEND_URL"] = frontend_url

    kill_port(backend_port)
    kill_port(os.environ.get("WS_PORT", "7878"))
    if local_frontend:
        kill_port(frontend_port)

    procs = []
    try:

        say(f"Starting backend → http://127.0.0.1:{backend_port}")
        procs.append(subprocess.Popen([py, "app.py"], cwd=str(BACKEND), env=child_env))
        if not wait_http(f"http://127.0.0.1:{backend_port}/api/health", 40, "Backend"):
            warn("Continuing anyway — backend may still be starting.")

        if local_frontend:
            if prod:
                if not (FRONTEND / ".next").exists():
                    say("Building frontend for production (first run can take a minute)…")
                    subprocess.run([npm, "run", "build"], cwd=str(FRONTEND), env=child_env,
                                   shell=IS_WIN, check=True)
                say(f"Starting frontend (production) → http://localhost:{frontend_port}")
                procs.append(subprocess.Popen([npm, "run", "start"], cwd=str(FRONTEND), env=child_env,
                                              shell=IS_WIN, **_hidden_window()))
            else:
                say(f"Starting frontend → http://localhost:{frontend_port}")
                procs.append(subprocess.Popen([npm, "run", "dev"], cwd=str(FRONTEND), env=child_env,
                                              shell=IS_WIN, **_hidden_window()))
            wait_http(f"http://127.0.0.1:{frontend_port}", 120, "Frontend")

        say(f"Dashboard will open at {frontend_url}/dashboard", C_OK)
        if not local_frontend:
            say("Your browser may ask to allow local-network access — click Allow.", C_WARN)

        if with_cli:
            cli_proc = spawn_cli_window(py)
            if cli_proc is not None:
                procs.append(cli_proc)

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
                if IS_WIN:

                    subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
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

def _report_crash():

    import traceback
    tb = traceback.format_exc()
    print(tb, file=sys.stderr)
    log = ROOT / ".scout" / "crash.log"
    try:
        log.parent.mkdir(exist_ok=True)
        log.write_text(tb, encoding="utf-8")
    except Exception:
        pass
    if IS_WIN and "--prod" in sys.argv:
        try:
            import ctypes
            last = tb.strip().splitlines()[-1]
            ctypes.windll.user32.MessageBoxW(
                None,
                f"Valorant Scout couldn't start.\n\n{last}\n\n"
                f"Details were saved to:\n{log}",
                "Valorant Scout", 0x10)
        except Exception:
            pass

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception:
        _report_crash()
        sys.exit(1)
