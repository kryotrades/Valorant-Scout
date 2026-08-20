from __future__ import annotations

import logging
import re
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

SCOUT_DIR = Path(__file__).resolve().parent.parent / ".scout"

MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 5

_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"([?&](?:s|t|token|key)=)[^&\s\"']+"), r"\1[REDACTED]"),
    (re.compile(r"\b([st]=)[A-Za-z0-9._~-]{8,}"), r"\1[REDACTED]"),
    (
        re.compile(
            r'("(?:token|password|apiKey|api_key|key|secret|authorization)"\s*:\s*")[^"]+(")',
            re.IGNORECASE,
        ),
        r"\1[REDACTED]\2",
    ),
    (re.compile(r"\b(Basic|Bearer)\s+[A-Za-z0-9+/=_\-.]{8,}"), r"\1 [REDACTED]"),
    (
        re.compile(
            r"\b(password|token|secret|api_key|apikey|authorization)\s*[=:]\s*\S+", re.IGNORECASE
        ),
        r"\1=[REDACTED]",
    ),
    (
        re.compile(r"\b[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}:[A-Za-z0-9_\-]{16,}\b"),
        "[REDACTED-ABLY-KEY]",
    ),
    (re.compile(r"\b([0-9a-fA-F]{8})[0-9a-fA-F\-]{24,}\b"), r"\1…[REDACTED]"),
    (re.compile(r"\b(\d{6})\d{11,}\b"), r"\1…[REDACTED]"),
]


def redact(text: str) -> str:
    for pat, repl in _REDACTIONS:
        text = pat.sub(repl, text)
    return text


class _UtcFormatter(logging.Formatter):
    @staticmethod
    def converter(timestamp: float | None) -> time.struct_time:
        return time.gmtime(timestamp)

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def get_logger(component: str, filename: str | None = None) -> logging.Logger:
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
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setFormatter(
            _UtcFormatter(
                fmt=f"%(asctime)s.%(msecs)03dZ [{component}] %(levelname)s %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
        logger.addHandler(handler)
    except OSError:
        logger.addHandler(logging.NullHandler())
    return logger
