"""Verify that the active environment exactly matches requirements.txt.

`pip check` verifies dependency relationships, not that installed versions equal
an external lock file.  Scout uses this stdlib-only check during install,
startup, update, diagnostics, and CI so compatible-but-different versions do
not silently pass as the tested environment.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import re
import sys
from pathlib import Path


PIN_RE = re.compile(r"^([A-Za-z0-9._\[\]-]+)==([A-Za-z0-9._+!-]+)$")


def canonicalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_lock(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = PIN_RE.fullmatch(line)
        if not match:
            raise ValueError(f"{path}:{number}: not an exact name==version pin: {line!r}")
        name, version = match.groups()
        key = canonicalize(name.split("[", 1)[0])
        if key in pins and pins[key] != version:
            raise ValueError(f"{path}:{number}: conflicting pins for {name}")
        pins[key] = version
    if not pins:
        raise ValueError(f"{path}: contains no package pins")
    return pins


def installed_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for dist in importlib.metadata.distributions():
        name = dist.metadata.get("Name")
        if name:
            versions[canonicalize(name)] = dist.version
    return versions


def compare(pins: dict[str, str], installed: dict[str, str]) -> list[dict[str, str]]:
    problems: list[dict[str, str]] = []
    for name in sorted(pins):
        expected = pins[name]
        actual = installed.get(name)
        if actual is None:
            problems.append({"package": name, "expected": expected, "actual": "missing"})
        elif actual != expected:
            problems.append({"package": name, "expected": expected, "actual": actual})
    return problems


def main(argv: list[str] | None = None) -> int:
    default_lock = Path(__file__).resolve().parent.parent / "backend" / "requirements.txt"
    parser = argparse.ArgumentParser()
    parser.add_argument("--requirements", type=Path, default=default_lock)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        pins = read_lock(args.requirements)
        installed = installed_versions()
        problems = compare(pins, installed)
    except Exception as exc:  # malformed/missing lock is a validation failure
        print(f"EXACT DEPENDENCY CHECK FAILED: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"ok": not problems, "locked": len(pins), "problems": problems}))
    elif problems:
        print("EXACT DEPENDENCY CHECK FAILED:", file=sys.stderr)
        for problem in problems:
            print(
                f"  {problem['package']}: expected {problem['expected']}, "
                f"installed {problem['actual']}",
                file=sys.stderr,
            )
    else:
        print(f"exact dependency check ok ({len(pins)} pins)")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
