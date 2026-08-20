"""Helpers that more than one backend module needs.

Nothing goes in here on the theory that it might be shared one day. Every
function below replaced at least two copies that already existed and had
already started to drift: the four `_save()` bodies were identical except for a
temp-file prefix, and one of them quietly wrote non-ASCII differently from the
other three.
"""

from __future__ import annotations

from common.env import load_env
from common.jsonstore import DATA_DIR, data_path, read_json, write_atomic
from common.log import console_logger

__all__ = [
    "DATA_DIR",
    "console_logger",
    "data_path",
    "load_env",
    "read_json",
    "write_atomic",
]
