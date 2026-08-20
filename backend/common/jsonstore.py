"""The on-disk JSON stores under backend/data.

encounter_log, history, match_meta, session_tracker and app all keep a dict on
disk and rewrite it whole. They each had their own copy of the same sixteen
lines, identical down to the nested try/finally, and the copies had begun to
disagree: match_meta and encounter_log passed ensure_ascii=False, history and
session_tracker did not, so a Riot ID with a non-ASCII character was escaped in
two files and not in the other two.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def data_path(name: str) -> str:
    """Path to a file in backend/data. The directory may not exist yet."""
    return os.path.join(DATA_DIR, name)


def read_json(path: str, default: Any) -> Any:
    """Return the parsed file, or `default` if it is missing or unreadable.

    A corrupt store is a supported state: the app starts with an empty one
    rather than refusing to launch over a file the user cannot fix by hand.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def write_atomic(path: str, data: Any, *, prefix: str, ensure_ascii: bool = False) -> bool:
    """Write `data` as JSON to `path` via a temp file in the same directory.

    Same directory so os.replace is a rename rather than a copy, which is what
    makes it atomic: a reader either sees the old file or the new one, never a
    half-written one. Returns False instead of raising, because every caller is
    a best-effort save on a background thread and none of them can do anything
    useful with the exception.
    """
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=prefix, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=ensure_ascii, separators=(",", ":"))
            os.replace(tmp, path)
            return True
        finally:
            # os.replace already moved it on the happy path; this is the
            # failure path, where the temp file is still sitting there.
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    except Exception:
        return False
