import importlib
import sys

REQUIRED = [
    "flask",
    "flask_cors",
    "tzdata",
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
    except Exception as e:
        failed.append(f"{mod}: {type(e).__name__}: {e}")

if failed:
    print("IMPORT SMOKE FAILED:", file=sys.stderr)
    for line in failed:
        print("  " + line, file=sys.stderr)
    sys.exit(1)

print(f"import smoke ok ({len(REQUIRED)} modules)")
