"""scoutlog.py — rotating, redacted UTF-8 logs under <repo>/.scout/.

Every component logs through here so the format, rotation and secret
redaction are identical everywhere:

    2026-07-15T02:41:07Z [backend] ERROR VS-BACKEND-001 message...

Rotation is bounded (2 MiB x 5 files). Redaction runs on the final formatted
line, so secrets are scrubbed no matter which component logged them.
Stdlib only — this must import even on a broken install.
"""
from __future__ import annotations

import logging
import re
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

SCOUT_DIR = Path(__file__).resolve().parent.parent / ".scout"

MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 5

# Patterns that must never reach a log file. Kept deliberately broad: a
# false-positive redaction costs a little readability, a false negative
# leaks a credential.
_REDACTIONS: list[tuple[re.Pattern, str]] = [
    # URL query params: session token, ably token
    (re.compile(r"([?&](?:s|t|token|key)=)[^&\s\"']+"), r"\1[REDACTED]"),
    # Bare/wrapped session/token params the URL rule misses (no leading ?/&),
    # e.g. "s=<token>". 8+ char values only, to avoid redacting things like
    # "s=up". Mirrored in scripts/common.ps1.
    (re.compile(r"\b([st]=)[A-Za-z0-9._~-]{8,}"), r"\1[REDACTED]"),
    # JSON-ish fields: "token": "...", "password": "...", "apiKey": "..." etc.
    (re.compile(r'("(?:token|password|apiKey|api_key|key|secret|authorization)"\s*:\s*")[^"]+(")',
                re.IGNORECASE), r"\1[REDACTED]\2"),
    # HTTP auth headers — must run BEFORE the key=value rule, which would
    # otherwise consume only the word "Bearer" and leave the token behind
    (re.compile(r"\b(Basic|Bearer)\s+[A-Za-z0-9+/=_\-.]{8,}"), r"\1 [REDACTED]"),
    # key=value style (lockfile password, env dumps)
    (re.compile(r"\b(password|token|secret|api_key|apikey|authorization)\s*[=:]\s*\S+",
                re.IGNORECASE), r"\1=[REDACTED]"),
    # Ably API keys look like  xxxxxx.yyyyyy:zzzzzzzz
    (re.compile(r"\b[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}:[A-Za-z0-9_\-]{16,}\b"),
     "[REDACTED-ABLY-KEY]"),
    # Full PUUIDs / Discord snowflakes: keep a short prefix for correlation
    (re.compile(r"\b([0-9a-fA-F]{8})[0-9a-fA-F\-]{24,}\b"), r"\1…[REDACTED]"),
    (re.compile(r"\b(\d{6})\d{11,}\b"), r"\1…[REDACTED]"),
]

def redact(text: str) -> str:
    for pat, repl in _REDACTIONS:
        text = pat.sub(repl, text)
    return text

class _UtcFormatter(logging.Formatter):
    converter = time.gmtime

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))

def get_logger(component: str, filename: str | None = None) -> logging.Logger:
    """Logger writing to .scout/<filename or component>.log. Idempotent."""
    name = f"scout.{component}"
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        SCOUT_DIR.mkdir(exist_ok=True)
        handler = RotatingFileHandler(
            SCOUT_DIR / f"{filename or component}.log",
            maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8")
        handler.setFormatter(_UtcFormatter(
            fmt=f"%(asctime)s.%(msecs)03dZ [{component}] %(levelname)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S"))
        logger.addHandler(handler)
    except OSError:
        # Read-only folder / locked file: logging must never take the app down.
        logger.addHandler(logging.NullHandler())
    return logger

def log_code(logger: logging.Logger, level: int, code: str, message: str) -> None:
    logger.log(level, "%s %s", code, message)
