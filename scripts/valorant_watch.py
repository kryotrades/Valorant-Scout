import ctypes
import hashlib
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))).rstrip("\\")
GAME = "valorant-win64-shipping.exe"
POLL_S = 5

sys.path.insert(0, os.path.join(ROOT, "backend"))
from offline_launch import _windows_process_names
import autolaunch

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateMutexW.restype = ctypes.c_void_p
_k32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
_k32.OpenMutexW.restype = ctypes.c_void_p
_k32.OpenMutexW.argtypes = (ctypes.c_uint32, ctypes.c_bool, ctypes.c_wchar_p)
_k32.CloseHandle.argtypes = (ctypes.c_void_p,)


def fingerprint(base: str = ROOT) -> str:
    lowered = "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in base.rstrip("\\"))
    return hashlib.sha256(lowered.encode("utf-8")).hexdigest()[:16].upper()


def mutex_name(purpose: str) -> str:
    return rf"Local\ValorantScout-{purpose}-{fingerprint()}"


def scout_running() -> bool:
    handle = _k32.OpenMutexW(0x00100000, False, mutex_name("App"))
    if handle:
        _k32.CloseHandle(handle)
    return bool(handle)


def enabled() -> bool:
    return (autolaunch._read() or "").lower() == autolaunch._command().lower()


def game_running() -> bool:
    return GAME in _windows_process_names()


def open_scout() -> None:
    exe = os.path.join(ROOT, "Valorant Scout.exe")
    if os.path.exists(exe):
        subprocess.Popen([exe], cwd=ROOT, creationflags=subprocess.CREATE_NEW_CONSOLE)
        return
    si = subprocess.STARTUPINFO()
    if os.path.exists(os.path.join(ROOT, ".scout", "hide-window")):
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    subprocess.Popen(["cmd.exe", "/c", "start.bat"], cwd=ROOT, startupinfo=si,
                     creationflags=subprocess.CREATE_NEW_CONSOLE)


def main() -> None:
    _single = _k32.CreateMutexW(None, False, mutex_name("Watch"))
    if not _single or ctypes.get_last_error() == 183:
        return
    was = True
    while enabled():
        time.sleep(POLL_S)
        try:
            now = game_running()
        except OSError:
            continue
        if now and not was and not scout_running():
            open_scout()
        was = now


if __name__ == "__main__":
    main()
