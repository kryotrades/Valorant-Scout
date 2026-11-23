import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "ValorantScoutAutoLaunch"
PYTHONW = os.path.join(ROOT, "runtime", "pythonw.exe")
if not os.path.exists(PYTHONW):
    PYTHONW = os.path.join(ROOT, ".venv", "Scripts", "pythonw.exe")
WATCHER = os.path.join(ROOT, "scripts", "valorant_watch.py")
_RUN_VARS = {"BACKEND_PORT", "WS_PORT", "FRONTEND_PORT", "PORT", "FRONTEND_URL", "SCOUT_QUIET"}
HIDE_FLAG = os.path.join(ROOT, ".scout", "hide-window")
QUIT_CODE = 64
REMOTE = None


def set_hidden(on: bool) -> dict:
    try:
        if on:
            os.makedirs(os.path.dirname(HIDE_FLAG), exist_ok=True)
            open(HIDE_FLAG, "w").close()
        elif os.path.exists(HIDE_FLAG):
            os.remove(HIDE_FLAG)
    except OSError as e:
        return {"ok": False, "message": f"Couldn't save that setting: {e}"}
    return status()


def quit_app() -> dict:
    import threading

    def _exit():
        try:
            if REMOTE is not None:
                t = threading.Thread(target=REMOTE.shutdown, daemon=True)
                t.start()
                t.join(3)
        finally:
            os._exit(QUIT_CODE)
    threading.Timer(0.5, _exit).start()
    return {"ok": True}


def _command() -> str:
    return f'"{PYTHONW}" "{WATCHER}"'


def _read():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, RUN_VALUE)[0]
    except OSError:
        return None


def status() -> dict:
    if sys.platform != "win32":
        return {"ok": True, "enabled": False}
    return {"ok": True, "enabled": _read() is not None, "hideWindow": os.path.exists(HIDE_FLAG)}


def set_enabled(on: bool) -> dict:
    if sys.platform != "win32":
        return {"ok": False, "enabled": False, "message": "Only available on Windows."}
    import winreg
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            if on:
                winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, _command())
            else:
                try:
                    winreg.DeleteValue(key, RUN_VALUE)
                except FileNotFoundError:
                    pass
    except OSError as e:
        return {"ok": False, "enabled": not on, "message": f"Couldn't change the startup entry: {e}"}
    if on:
        ensure_running()
    return {"ok": True, "enabled": on}


def apply(payload: dict) -> dict:
    if payload.get("quit"):
        return quit_app()
    if "hideWindow" in payload:
        return set_hidden(bool(payload.get("hideWindow")))
    if "enabled" in payload:
        return set_enabled(bool(payload.get("enabled")))
    return status()


def ensure_running() -> None:
    if sys.platform != "win32" or _read() is None or not os.path.exists(PYTHONW):
        return
    if _read() != _command():
        set_enabled(True)
        return
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("VS_", "SCOUT_")) and k.upper() not in _RUN_VARS | {"DATA_SOURCE"}}
    subprocess.Popen(["cmd.exe", "/c", "start", "", PYTHONW, WATCHER], cwd=ROOT, close_fds=True,
                     env=env, creationflags=subprocess.CREATE_NO_WINDOW)
