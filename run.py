"""
run.py — launcher for Valorant Scout.

Startup NEVER installs anything (no venv creation, no pip, no npm, no builds).
install.bat owns installation and repair; this file only:
  1. Loads environment from .env / backend/.env (no extra deps required).
  2. Validates the installed runtime fast and offline (VS-PY-001 / VS-DEPS-001
     point the user at install.bat).
  3. Picks free ports (never killing foreign processes), spawns the backend,
     waits for it to be healthy, and opens the terminal scoreboard.

The live scoreboard needs no PUUID — your running client identifies you. With
VALORANT closed it shows a demo lobby so the UI is always explorable.

Press Ctrl+C to stop everything.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
SCOUT_DIR = ROOT / ".scout"
IS_WIN = os.name == "nt"

HOSTED_FRONTEND = "https://valorantscout.vercel.app"

sys.path.insert(0, str(BACKEND))
try:
    import scoutlog
    LOG = scoutlog.get_logger("launcher")
except Exception:  # a broken checkout must still be able to print an error
    import logging
    LOG = logging.getLogger("launcher")
    LOG.addHandler(logging.NullHandler())

def has_local_frontend() -> bool:
    requested = "--local-frontend" in sys.argv or os.environ.get(
        "SCOUT_LOCAL_FRONTEND", "").strip().lower() in ("1", "true", "yes")
    if requested and not (FRONTEND / "package.json").exists():
        die("VS-FRONTEND-001", "Local frontend mode was requested, but frontend/ is not bundled.")
    return requested

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

if IS_WIN:
    try:
        import ctypes
        from ctypes import wintypes
        _k32 = ctypes.windll.kernel32
        # HANDLE is a 64-bit pointer; ctypes' default c_int restype truncates and
        # sign-extends values >= 0x80000000, corrupting the handle on its way back
        # into WaitForSingleObject/CloseHandle — which would make startup wrongly
        # report "already running". windll caches these function objects, so
        # declaring the prototypes once here covers every later call site.
        _k32.CreateMutexW.restype = wintypes.HANDLE
        _k32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
        _k32.WaitForSingleObject.restype = wintypes.DWORD
        _k32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        _k32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
        _k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        _k32.OpenProcess.restype = wintypes.HANDLE
        _k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        _k32.QueryFullProcessImageNameW.argtypes = (
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
        _k32.GetStdHandle.restype = wintypes.HANDLE
        _k32.SetConsoleMode.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        # Enable ANSI/VT escape processing on the Windows console without shelling
        # out (STD_OUTPUT_HANDLE = -11; mode 7 = processed|wrap|virtual-terminal).
        _k32.SetConsoleMode(_k32.GetStdHandle(-11), 7)
    except Exception:
        pass

C_RED = "\033[38;5;203m"
C_TEAL = "\033[38;5;43m"
C_DIM = "\033[2m"
C_OK = "\033[38;5;78m"
C_WARN = "\033[38;5;214m"
C_END = "\033[0m"

# Attached single-console mode (start.ps1 sets this): the scoreboard renders in
# THE SAME console we run in, so our own chatter must stay off the screen —
# it goes to launcher.log instead.
ATTACHED = os.environ.get("VS_ATTACHED_CLI", "").strip() == "1"

def say(msg, color=C_TEAL):
    if ATTACHED:
        LOG.info("%s", msg)
        return
    print(f"{color}» {msg}{C_END}", flush=True)

def warn(msg):
    if ATTACHED:
        LOG.warning("%s", msg)
        return
    print(f"{C_WARN}! {msg}{C_END}", flush=True)

def die(code: str, msg: str) -> "NoReturn":
    LOG.error("%s %s", code, msg)
    _fatal_dialog(f"{code}: {msg}")
    warn(f"{code}: {msg}")
    sys.exit(1)

def _fatal_dialog(message: str) -> None:
    # start.bat runs us hidden; a fatal error must still be visible.
    if not (IS_WIN and "--prod" in sys.argv):
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None,
            f"{message}\n\nDetails: {SCOUT_DIR / 'launcher.log'}",
            "Valorant Scout", 0x10)
    except Exception:
        pass

def load_env():
    for path in (ROOT / ".env", BACKEND / ".env"):
        if not path.exists():
            continue
        # utf-8-sig: users edit .env by hand; an editor-added BOM must not
        # corrupt the first key, and a stray non-UTF8 byte must not crash startup.
        for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)

def venv_python() -> Path:
    return ROOT / ".venv" / ("Scripts/python.exe" if IS_WIN else "bin/python")

def resolve_python() -> str:
    """Pick the interpreter to run the app with. Never installs anything."""
    py = venv_python()
    if py.exists():
        return str(py)
    # Developer fallback: whoever is running run.py directly with deps present.
    if subprocess.run([sys.executable, "-c", "import flask"],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        return sys.executable
    die("VS-PY-001",
        "No Python environment found (.venv is missing). "
        "Run install.bat to set up Valorant Scout.")

def validate_runtime(py: str) -> None:
    """Fast offline check that the installed packages can actually load."""
    # start.ps1 just ran these exact probes (Test-Venv); don't spend 2-4s
    # re-running them. Direct `python run.py` (dev) still validates.
    if os.environ.get("VS_PREVALIDATED", "").strip() == "1":
        return
    exact = ROOT / "scripts" / "verify_installed.py"
    requirements = BACKEND / "requirements.txt"
    if exact.exists() and requirements.exists():
        r = subprocess.run([py, str(exact), "--requirements", str(requirements)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            LOG.error("VS-DEPS-001 exact dependency check failed:\n%s",
                      (r.stderr or r.stdout).strip())
            die("VS-DEPS-001",
                "Installed package versions do not match this release. "
                "Run install.bat to repair (your settings and data are kept).")
    smoke = ROOT / "scripts" / "import_smoke.py"
    if not smoke.exists():
        return  # stray copy without scripts/ — let the app try
    r = subprocess.run([py, str(smoke)], capture_output=True, text=True)
    if r.returncode != 0:
        LOG.error("VS-DEPS-001 import smoke failed:\n%s", r.stderr.strip())
        die("VS-DEPS-001",
            "Installed packages are broken or missing. "
            "Run install.bat to repair (your settings and data are kept).")

# ---------------------------------------------------------------------------
# Single instance — a second launch must not spawn a second stack.
# ---------------------------------------------------------------------------
_INSTANCE_LOCK = None  # keep a reference so the handle lives for the process

def _path_fingerprint() -> str:
    """Fingerprint of the install folder. MUST stay byte-identical to
    Get-PathFingerprint in scripts/common.ps1 on every path:
      * use abspath (NOT resolve) so the invocation form matches what
        $PSScriptRoot sees — no junction/8.3 expansion;
      * ASCII-only lowercasing (map 'A'-'Z' only) so İ (U+0130) / ẞ (U+1E9E)
        don't diverge from .NET invariant lowering, which str.lower() would.
    """
    base = os.path.dirname(os.path.abspath(__file__)).rstrip("\\")
    lowered = "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in base)
    return hashlib.sha256(lowered.encode("utf-8")).hexdigest()[:16].upper()

def _mutex_name(purpose: str) -> str:
    return rf"Local\ValorantScout-{purpose}-{_path_fingerprint()}"

def _my_process_tree() -> set[int]:
    """Our own pid plus a few ancestors. A Store-Python venv runs us as a CHILD
    of the venv redirector stub — killing the stub (its cmdline also contains
    run.py) would kill us, so the whole ancestry must be exempt from takeover."""
    mine = {os.getpid()}
    pid = os.getpid()
    for _ in range(4):
        _, _, ppid = _proc_info(pid)
        if not ppid:
            break
        mine.add(ppid)
        pid = ppid
    return mine

def _kill_leftover_instances() -> bool:
    """Kill a previous run.py of THIS install (and its tree). Returns True if
    anything was killed. Never touches processes outside this folder."""
    if not IS_WIN:
        return False
    mine = _my_process_tree()
    pids = set()
    # Fast path: the old launcher recorded its pid.
    try:
        state = json.loads((SCOUT_DIR / "runtime-state.json").read_text(encoding="utf-8"))
        pid = int(state.get("pid", 0))
        if pid > 0 and pid not in mine and _is_ours(pid):
            pids.add(pid)
    except Exception:
        pass
    # Fallback: a mid-shutdown instance may already have removed the state file.
    # Match "<this folder>\run.py" in the command line — full-path prefix, so a
    # sibling install can never match. Path goes via env var (no PS quoting games).
    try:
        env = os.environ.copy()
        env["VS_MATCH"] = (str(ROOT).rstrip("\\") + os.sep + "run.py").lower()
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "$m = $env:VS_MATCH; Get-CimInstance Win32_Process -Filter "
             "\"Name like 'py%'\" | Where-Object { $_.CommandLine -and "
             "$_.CommandLine.ToLower().Contains($m) } | "
             "ForEach-Object { $_.ProcessId }"],
            capture_output=True, text=True, timeout=20, env=env).stdout
        for tok in out.split():
            if tok.isdigit() and int(tok) not in mine:
                pids.add(int(tok))
    except Exception:
        pass
    if not pids:
        return False
    for pid in pids:
        say(f"A previous Valorant Scout (PID {pid}) is still closing — taking over.", C_DIM)
        LOG.info("killing leftover instance pid=%s to take over", pid)
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True

def acquire_instance_lock() -> bool:
    global _INSTANCE_LOCK
    if not IS_WIN:
        return True
    try:
        import ctypes
        handle = ctypes.windll.kernel32.CreateMutexW(None, False, _mutex_name("App"))
        if not handle:
            raise OSError("CreateMutexW failed")
        # A handle is not ownership (PowerShell's New-ScoutMutex tests ownership
        # with WaitOne(0)). Try to take it: WAIT_OBJECT_0 = free, WAIT_ABANDONED
        # = the previous owner died (e.g. start.bat just killed it) — both mean
        # we own it now. Only a LIVE instance yields WAIT_TIMEOUT. Deciding on
        # ERROR_ALREADY_EXISTS instead would wrongly refuse an abandoned mutex.
        wait = ctypes.windll.kernel32.WaitForSingleObject(handle, 0)
        if wait not in (0, 0x80):  # not WAIT_OBJECT_0 / WAIT_ABANDONED
            # A previous instance still holds the lock (e.g. the user closed the
            # scoreboard and relaunched immediately). Kill it and take over —
            # relaunching should always win. The killed owner leaves the mutex
            # ABANDONED, which wakes this wait instantly.
            if _kill_leftover_instances():
                wait = ctypes.windll.kernel32.WaitForSingleObject(handle, 3000)
            if wait not in (0, 0x80):
                ctypes.windll.kernel32.CloseHandle(handle)
                return False
        _INSTANCE_LOCK = handle
        return True
    except Exception:
        LOG.exception("could not create the app instance mutex")
        return False

def release_instance_lock() -> None:
    global _INSTANCE_LOCK
    if not (IS_WIN and _INSTANCE_LOCK):
        return
    try:
        import ctypes
        ctypes.windll.kernel32.ReleaseMutex(_INSTANCE_LOCK)  # give up ownership
        ctypes.windll.kernel32.CloseHandle(_INSTANCE_LOCK)
    finally:
        _INSTANCE_LOCK = None

def write_runtime_state(backend_port: int, ws_port: int, frontend_port: str) -> None:
    # Best-effort: the state file helps install/update find us to take over,
    # but a locked/synced .scout folder must not kill startup.
    try:
        SCOUT_DIR.mkdir(exist_ok=True)
        path = SCOUT_DIR / "runtime-state.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps({
            "pid": os.getpid(),
            "backendPort": int(backend_port),
            "wsPort": int(ws_port),
            "frontendPort": int(frontend_port),
            "startedAt": int(time.time()),
        }), encoding="utf-8")
        os.replace(temp, path)
    except OSError:
        LOG.warning("couldn't write runtime-state.json (non-fatal)", exc_info=True)

def clear_runtime_state() -> None:
    try:
        (SCOUT_DIR / "runtime-state.json").unlink(missing_ok=True)
    except OSError:
        pass

# ---------------------------------------------------------------------------
# Ports — kill only our own stale instances; foreign occupants mean we move
# to a free alternate port and propagate it everywhere.
# ---------------------------------------------------------------------------
def _pid_exe(pid: int) -> str:
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
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
    # Store-Python venvs run the listener as a child of the venv wrapper, so the
    # ROOT path may only appear on a parent; walk up a few hops.
    root = str(ROOT).lower()
    prefix = root.rstrip("\\") + os.sep  # boundary so "<root>-old" can't match
    for _ in range(3):
        exe, cmd, ppid = _proc_info(pid)
        for hay in (exe.lower(), cmd.lower()):
            if hay == root or prefix in hay:
                return True
        if not ppid:
            return False
        pid = ppid
    return False

def _port_free(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            s.bind(("127.0.0.1", int(port)))
        return True
    except OSError:
        return False

def _port_pids(port) -> set[int]:
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
        if len(parts) >= 5 and parts[1].endswith(f":{port}") \
                and parts[2] in ("0.0.0.0:0", "[::]:0"):
            try:
                pid = int(parts[4])
            except ValueError:
                continue
            if pid not in (0, 4, me):
                pids.add(pid)
    return pids

def _kill_our_stale(port) -> None:
    """Kill processes on `port` ONLY if they run from our folder."""
    if not IS_WIN:
        return
    root = str(ROOT).lower()
    prefix = root.rstrip("\\") + os.sep  # boundary so "<root>-old" can't match
    for pid in _port_pids(port):
        exe = _pid_exe(pid).lower()
        if exe == root or exe.startswith(prefix) or _is_ours(pid):
            say(f"Port {port} is held by a previous Valorant Scout instance (PID {pid}) — closing it.", C_DIM)
            LOG.info("closing our stale instance pid=%s on port %s", pid, port)
            subprocess.run(["taskkill", "/PID", str(pid), "/T"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and not _port_free(port):
                time.sleep(0.15)
            if not _port_free(port):
                LOG.warning("stale pid %s ignored graceful shutdown; forcing it", pid)
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def choose_port(preferred, label: str, reserved=()) -> int:
    """Return a usable port, preferring `preferred`. Never kills foreign processes."""
    preferred = int(preferred)
    reserved = {int(port) for port in reserved}
    if preferred not in reserved and _port_free(preferred):
        return preferred
    if preferred not in reserved:
        _kill_our_stale(preferred)
        for _ in range(20):  # taskkill is async — give the socket a moment to free up
            if _port_free(preferred):
                return preferred
            time.sleep(0.25)
    holder = ""
    if preferred in reserved:
        holder = "another Valorant Scout service"
    else:
        for pid in _port_pids(preferred):
            holder = _pid_exe(pid) or f"PID {pid}"
            break
    for alt in range(preferred + 1, preferred + 21):
        if alt not in reserved and _port_free(alt):
            warn(f"Port {preferred} ({label}) is in use by {holder or 'another program'} — using port {alt} instead.")
            LOG.warning("VS-PORT-001 port %s (%s) busy (%s); using alternate %s",
                        preferred, label, holder, alt)
            return alt
    die("VS-PORT-001",
        f"Ports {preferred}-{preferred + 20} ({label}) are all in use "
        f"(first held by {holder or 'another program'}). Close it, or set "
        f"BACKEND_PORT / WS_PORT in backend\\.env to a free port.")

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
# Child processes
# ---------------------------------------------------------------------------
def node_cmd() -> str:
    cmd = "node.exe" if IS_WIN else "node"
    executable = shutil.which(cmd)
    if executable is None:
        warn("Node.js not found on PATH. Install Node.js 18.17+ from https://nodejs.org and retry.")
        sys.exit(1)
    try:
        version = subprocess.run([executable, "--version"], capture_output=True,
                                 text=True, timeout=10, check=True).stdout.strip().lstrip("v")
        # keep digits only so prerelease tags ("21.0.0-nightly") still parse
        parts = tuple(int(re.sub(r"\D.*$", "", part) or 0) for part in version.split(".")[:3])
        if parts < (18, 17, 0):
            raise ValueError(version)
    except Exception:
        die("VS-FRONTEND-001",
            "The local frontend requires Node.js 18.17 or newer. Upgrade Node.js and retry.")
    return executable

def _rotate(path: Path, max_bytes: int = 2 * 1024 * 1024, backups: int = 5) -> None:
    try:
        if path.exists() and path.stat().st_size > max_bytes:
            for i in range(backups - 1, 0, -1):
                src = path.with_suffix(path.suffix + f".{i}")
                if src.exists():
                    os.replace(src, path.with_suffix(path.suffix + f".{i + 1}"))
            os.replace(path, path.with_suffix(path.suffix + ".1"))
    except OSError:
        pass

def backend_output(prod: bool):
    """In hidden/prod mode capture the backend's RAW console into
    .scout/backend-console.log so fatal output is never discarded; in dev keep
    it on the console. This is a separate file from scoutlog's redacted,
    rotated .scout/backend.log — two writers must not share one file."""
    if not prod:
        return None
    try:
        SCOUT_DIR.mkdir(exist_ok=True)
        log = SCOUT_DIR / "backend-console.log"
        _rotate(log)
        return open(log, "a", encoding="utf-8", errors="replace")
    except OSError:
        return None

def tail_backend_log(lines: int = 12) -> str:
    try:
        text = (SCOUT_DIR / "backend-console.log").read_text(encoding="utf-8", errors="replace")
        return "\n".join(text.splitlines()[-lines:])
    except OSError:
        return ""

def run_cli():
    py = resolve_python()
    validate_runtime(py)
    extra = [a for a in sys.argv[1:]
             if a not in ("--cli", "--no-cli", "--prod", "--local-frontend")]
    say("Launching terminal scoreboard…", C_OK)
    subprocess.run([py, str(ROOT / "cli.py"), *extra])

def _hidden_window() -> dict:
    if not IS_WIN:
        return {}
    return {"creationflags": subprocess.CREATE_NO_WINDOW,
            "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}

def spawn_cli_window(py: str):
    extra = [a for a in sys.argv[1:] if a not in ("--cli", "--no-cli", "--prod")]
    cli = str(ROOT / "cli.py")
    try:
        if IS_WIN and ATTACHED:
            # Single-window mode: the scoreboard renders in OUR console (the
            # one start.bat opened with the progress bar) — no new window.
            proc = subprocess.Popen([py, cli, *extra])
        elif IS_WIN:
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

# Pids the console-close handler must take down with us (backend). In attached
# mode the whole stack shares ONE console; clicking X sends CTRL_CLOSE_EVENT to
# every attached process, but the backend (own process group, may ignore it)
# gets force-killed here so it can never orphan. The handler has a ~5s budget.
_CTRL_KILL_PIDS: list[int] = []
_CTRL_HANDLER_REF = None  # keep the ctypes callback alive (GC would unhook it)
_CLOSING = False  # set by the close handler; the monitor loop must not treat
                  # the resulting child deaths as crashes (no error dialog)

def _install_console_close_handler() -> None:
    if not (IS_WIN and ATTACHED):
        return
    global _CTRL_HANDLER_REF
    try:
        import ctypes
        from ctypes import wintypes
        HandlerRoutine = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

        def _handler(event):
            # CTRL_CLOSE_EVENT=2, CTRL_LOGOFF_EVENT=5, CTRL_SHUTDOWN_EVENT=6
            if event in (2, 5, 6):
                global _CLOSING
                _CLOSING = True
                for pid in list(_CTRL_KILL_PIDS):
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    release_instance_lock()
                    clear_runtime_state()
                except Exception:
                    pass
            return False  # let the default handler terminate us

        _CTRL_HANDLER_REF = HandlerRoutine(_handler)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_CTRL_HANDLER_REF, True)
    except Exception:
        LOG.debug("couldn't install console close handler", exc_info=True)

def shutdown(procs, grouped=()) -> None:
    """Ask children to exit, then force the stragglers. Budget: < 4s total —
    closing the scoreboard must feel instant, so one short graceful window
    (2s) then straight to a tree force-kill."""
    alive = [p for p in procs if p.poll() is None]
    grouped = set(grouped)
    for p in alive:
        try:
            if IS_WIN and p in grouped:
                p.send_signal(signal.CTRL_BREAK_EVENT)
            elif IS_WIN:
                subprocess.run(["taskkill", "/PID", str(p.pid), "/T"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                p.terminate()
        except Exception:
            LOG.debug("graceful stop failed for pid %s", p.pid, exc_info=True)

    deadline = time.monotonic() + 2
    for p in alive:
        try:
            p.wait(timeout=max(0.05, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass

    stubborn = [p for p in alive if p.poll() is None]
    for p in stubborn:
        LOG.warning("pid %s ignored graceful shutdown; forcing its process tree", p.pid)
        try:
            if IS_WIN:
                subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                p.kill()
        except Exception:
            pass
    deadline = time.monotonic() + 1.5
    for p in stubborn:
        try:
            p.wait(timeout=max(0.05, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass

def main():
    load_env()

    if "--cli" in sys.argv:
        run_cli()
        return

    with_cli = "--no-cli" not in sys.argv
    prod = "--prod" in sys.argv

    if not acquire_instance_lock():
        LOG.info("second instance blocked")
        say("Valorant Scout is already running or being installed/updated.", C_WARN)
        _fatal_dialog("Valorant Scout is already running or maintenance is in progress.\n\n"
                      "Close the app or wait for install/update to finish, then try again.")
        return

    if not ATTACHED:
        print(f"{C_RED}{'='*58}{C_END}")
        print(f"{C_RED}  VALORANT SCOUT{C_END}  {C_DIM}web + terminal · live scoreboard · instalock{C_END}")
        print(f"{C_RED}{'='*58}{C_END}")

    source = os.environ.get("DATA_SOURCE", "auto")
    say("Live scoreboard reads your LOCAL VALORANT client — open the game and")
    say("join Agent Select / a match to see real ranks, names & parties.")
    say(f"Otherwise a demo lobby is shown.  (DATA_SOURCE={source})")
    if os.environ.get("RIOT_API_KEY", "").strip():
        say("RIOT_API_KEY found (used by the legacy match-history endpoint).", C_OK)
    if os.environ.get("VS_UPDATE_AVAILABLE", "").strip():
        say(f"Update {os.environ['VS_UPDATE_AVAILABLE']} is available — run UPDATE.bat to apply it.", C_WARN)

    py = resolve_python()
    validate_runtime(py)

    backend_port = choose_port(os.environ.get("BACKEND_PORT", "5000"), "backend")
    ws_port = choose_port(os.environ.get("WS_PORT", "7878"), "WebSocket bridge",
                          reserved={backend_port})
    frontend_port = os.environ.get("FRONTEND_PORT", "3000")

    local_frontend = has_local_frontend()
    node = None
    if local_frontend:
        node = node_cmd()
        if not (FRONTEND / "node_modules").exists():
            die("VS-FRONTEND-001",
                "frontend/node_modules is missing. Run install.bat -Frontend "
                "to set up the local frontend first.")
        frontend_port = str(choose_port(frontend_port, "frontend",
                                        reserved={backend_port, ws_port}))
        frontend_url = (os.environ.get("LOCAL_FRONTEND_URL", "").strip()
                        or f"http://localhost:{frontend_port}").rstrip("/")
    else:
        frontend_url = (os.environ.get("FRONTEND_URL", "").strip()
                        or HOSTED_FRONTEND).rstrip("/")
        say("No local frontend bundled — using the hosted dashboard.", C_OK)
        say(f"Dashboard host: {frontend_url}")

    child_env = os.environ.copy()
    child_env["BACKEND_PORT"] = str(backend_port)
    child_env["WS_PORT"] = str(ws_port)
    child_env["FRONTEND_PORT"] = str(frontend_port)
    child_env["PORT"] = str(frontend_port)
    child_env["FRONTEND_URL"] = frontend_url

    LOG.info("starting stack: backend=%s ws=%s frontend=%s (%s)",
             backend_port, ws_port, frontend_port,
             "local frontend" if local_frontend else "hosted")

    procs = []
    roles = {}
    grouped = set()
    backend_log_fh = backend_output(prod)
    try:
        write_runtime_state(backend_port, ws_port, frontend_port)
        say(f"Starting backend → http://127.0.0.1:{backend_port}")
        out = {}
        if backend_log_fh is not None:
            out = {"stdout": backend_log_fh, "stderr": subprocess.STDOUT}
        if IS_WIN:
            out["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        backend_proc = subprocess.Popen([py, "app.py"], cwd=str(BACKEND), env=child_env, **out)
        procs.append(backend_proc)
        roles[backend_proc] = "backend"
        if IS_WIN:
            grouped.add(backend_proc)
        _CTRL_KILL_PIDS.append(backend_proc.pid)
        _install_console_close_handler()

        # Open the scoreboard window IMMEDIATELY — cli.py reads the Valorant
        # client directly (it doesn't need our backend), and its startup banner
        # covers the backend boot so the user never stares at nothing.
        if with_cli:
            cli_proc = spawn_cli_window(py)
            if cli_proc is not None:
                procs.append(cli_proc)
                roles[cli_proc] = "scoreboard"

        if not wait_http(f"http://127.0.0.1:{backend_port}/api/health", 40, "Backend"):
            tail = tail_backend_log()
            LOG.error("VS-BACKEND-001 backend did not become healthy; last output:\n%s", tail)
            die("VS-BACKEND-001",
                "The backend did not start. See .scout\\backend-console.log for details.")

        if local_frontend:
            if prod:
                if not (FRONTEND / ".next").exists():
                    die("VS-FRONTEND-001",
                        "frontend/.next is missing. Run install.bat -Frontend "
                        "to build the local frontend first.")
                say(f"Starting frontend (production) → http://localhost:{frontend_port}")
                frontend_mode = "start"
            else:
                say(f"Starting frontend → http://localhost:{frontend_port}")
                frontend_mode = "dev"
            next_cli = FRONTEND / "node_modules" / "next" / "dist" / "bin" / "next"
            if not next_cli.exists():
                die("VS-FRONTEND-001",
                    "The local Next.js runtime is incomplete. Run install.bat -Frontend to repair it.")
            frontend_args = [node, str(next_cli), frontend_mode, "-H", "127.0.0.1"]
            frontend_opts = _hidden_window()
            if IS_WIN:
                frontend_opts["creationflags"] = (
                    frontend_opts.get("creationflags", 0) | subprocess.CREATE_NEW_PROCESS_GROUP)
            frontend_proc = subprocess.Popen(frontend_args, cwd=str(FRONTEND), env=child_env,
                                             shell=False, **frontend_opts)
            procs.append(frontend_proc)
            roles[frontend_proc] = "frontend"
            if IS_WIN:
                grouped.add(frontend_proc)
            if not wait_http(f"http://127.0.0.1:{frontend_port}", 120, "Frontend"):
                die("VS-FRONTEND-001",
                    "The local frontend did not start. Run diagnostics.bat for details.")

        say(f"Dashboard will open at {frontend_url}/dashboard", C_OK)
        if not local_frontend:
            say("Your browser may ask to allow local-network access — click Allow.", C_WARN)

        if not ATTACHED:
            print(f"\n{C_OK}Web app + terminal scoreboard running. Press Ctrl+C to stop.{C_END}\n")
        stop = False
        while not stop:
            time.sleep(0.5)
            for p in procs:
                if p.poll() is None:
                    continue
                role = roles.get(p, "child")
                if role == "backend":
                    # Window-X / logoff / shutdown: CTRL_CLOSE kills every
                    # console-attached process, so the backend dying with
                    # STATUS_CONTROL_C_EXIT is the app CLOSING, not crashing —
                    # this loop can observe it before we're terminated ourselves.
                    if _CLOSING or (ATTACHED and p.returncode in (0xC000013A, -1073741510)):
                        LOG.info("backend exited with console-close status; shutting down")
                        stop = True
                        break
                    tail = tail_backend_log()
                    LOG.error("VS-BACKEND-001 backend exited (code %s); last output:\n%s",
                              p.returncode, tail)
                    die("VS-BACKEND-001",
                        f"The backend stopped unexpectedly (exit {p.returncode}). "
                        "See .scout\\backend-console.log for details.")
                if role == "frontend":
                    die("VS-FRONTEND-001",
                        f"The local frontend stopped unexpectedly (exit {p.returncode}).")
                if role == "scoreboard":
                    # Closing the scoreboard window IS how users quit the app.
                    say("Scoreboard closed — shutting down.", C_WARN)
                    LOG.info("scoreboard window closed; shutting down")
                    stop = True
                    break
    except KeyboardInterrupt:
        print()
        say("Shutting down…", C_WARN)
    finally:
        shutdown(procs, grouped)
        if backend_log_fh is not None:
            try:
                backend_log_fh.close()
            except OSError:
                pass
        # Release the mutex BEFORE clearing the state file: the reverse order
        # leaves a window where a relauncher sees nothing to kill (no state
        # file) while the mutex is still held.
        release_instance_lock()
        clear_runtime_state()
        say("Bye.", C_DIM)

def _report_crash():
    # start.bat runs us in a hidden window: without this, any startup failure is invisible.
    import traceback
    tb = traceback.format_exc()
    print(tb, file=sys.stderr)
    log = SCOUT_DIR / "crash.log"
    try:
        log.parent.mkdir(exist_ok=True)
        _rotate(log)
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(f"\n--- {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} ---\n{tb}")
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
    except SystemExit:
        raise
    except Exception:
        _report_crash()
        sys.exit(1)
