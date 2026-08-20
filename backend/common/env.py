"""Reading backend/.env without a dependency.

run.py and cli.py each had their own copy of this loop. python-dotenv is
already pinned and app.py uses it, but the launcher must not import anything
from site-packages before it has checked that site-packages is intact, which is
the whole point of validate_runtime().
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def load_env(*paths: Path) -> None:
    """Populate os.environ from KEY=VALUE files, first file wins.

    setdefault, not assignment: a variable already set by start.ps1 or by the
    user's shell outranks the file, which is what lets the launcher pass
    BACKEND_PORT down to a child that also reads the same .env.
    """
    for path in paths:
        if not path.exists():
            continue
        # utf-8-sig because Notepad writes a BOM and the first key would
        # otherwise be named "﻿RIOT_REGION".
        for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
