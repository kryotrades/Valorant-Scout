"""The `[tag] message` console line four modules were each defining for themselves.

live_match, riot_client, remote_ably and ws_server all had a private `_log`.
Two of them honoured SCOUT_QUIET and two did not, which is why the terminal
scoreboard printed backend chatter over its own frame.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable


def console_logger(
    tag: str, *, quiet_aware: bool = True, also: logging.Logger | None = None
) -> Callable[[str], None]:
    """Return a `log(msg)` that prints `[tag] msg` to the console.

    quiet_aware=False is for the two callers that must speak even under
    SCOUT_QUIET, because their message is the reason the user is looking at the
    console at all: the remote-mode pairing code and the WebSocket bridge's
    startup failure.
    """

    def log(msg: str) -> None:
        if quiet_aware and os.getenv("SCOUT_QUIET"):
            return
        print(f"[{tag}] {msg}", flush=True)
        if also is not None:
            also.info("%s", msg)

    return log
