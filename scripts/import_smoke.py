"""import_smoke.py — prove the installed environment can actually run Scout.

Imports every package the app needs at runtime (direct deps only; pip check
covers the transitive closure). Any failure exits nonzero with the module
name so install.bat / startup validation can report VS-DEPS-001 precisely.
Works offline; touches no network, no Riot client, no user data.
"""
import importlib
import sys

REQUIRED = [
    "flask",
    "flask_cors",
    "requests",
    "dotenv",
    "urllib3",
    "rich",
    "pypresence",
    "websockets",
    "websockets.sync.client",
    "ably",
    "valclient",
]

failed = []
for mod in REQUIRED:
    try:
        importlib.import_module(mod)
    except Exception as e:  # noqa: BLE001 — any import failure is a broken install
        failed.append(f"{mod}: {type(e).__name__}: {e}")

if failed:
    print("IMPORT SMOKE FAILED:", file=sys.stderr)
    for line in failed:
        print("  " + line, file=sys.stderr)
    sys.exit(1)

print(f"import smoke ok ({len(REQUIRED)} modules)")
