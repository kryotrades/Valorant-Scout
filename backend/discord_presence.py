"""
discord_presence.py
===================
Discord Rich Presence — shows your VALORANT status on your Discord profile
(rank, lobby / agent-select / in-game, map, mode and live score), mirroring
the live VALORANT client.

A background thread connects to the local Discord client over IPC (via
`pypresence`) and pushes an "activity" every ~15s built from the live
scoreboard. The rich images (map splashes, rank-tier icons, agent icons, the
game icon) come from assets on a Discord application.

Best-effort: if pypresence isn't installed or Discord isn't running, it quietly
no-ops and keeps retrying.
"""

from __future__ import annotations

import base64
import threading
import time

from riot_client import LocalAuth

# Discord application id (its assets provide the map / rank / agent images).
# Stored encoded and assembled at runtime so it isn't a plain editable string in
# the source. Not an env var on purpose — the presence images depend on this
# exact app, so it shouldn't be swapped out casually.
_CID_PARTS = ("MTAxMjQwMjIx", "MTEzNDkxMDU0Ng==")
_CLIENT_ID = (base64.b64decode(_CID_PARTS[0]).decode()
              + base64.b64decode(_CID_PARTS[1]).decode())

_UPDATE_SECS = 15
_worker: "_Worker | None" = None


def maybe_start(region: str | None = None) -> None:
    """Start the presence thread once, unless disabled."""
    global _worker
    import os
    if os.getenv("DISCORD_RPC", "true").strip().lower() == "false":
        return
    if _worker is not None:
        return
    _worker = _Worker(region)
    _worker.start()


def _map_asset(map_name: str | None) -> str | None:
    if not map_name or map_name == "Unknown":
        return None
    return f"splash_{map_name.lower()}_square"


def _agent_asset(agent_name: str | None) -> str | None:
    """agent asset key: lowercase name, no slashes/spaces."""
    if not agent_name or agent_name == "Unknown":
        return None
    return agent_name.lower().replace("/", "").replace(" ", "")


class _Worker:
    def __init__(self, region: str | None):
        self.region = region
        self.client_id = _CLIENT_ID
        self._thread: threading.Thread | None = None
        self._last_state: str | None = None
        self._state_since: float = time.time()

    def start(self):
        self._thread = threading.Thread(target=self._run, name="DiscordRPC", daemon=True)
        self._thread.start()

    # -- main loop ----------------------------------------------------------
    def _run(self):
        try:
            from pypresence import Presence
        except Exception:  # noqa: BLE001
            print("[discord] pypresence not installed - Rich Presence off "
                  "(pip install pypresence)", flush=True)
            return

        rpc = None
        last_payload = None
        while True:
            try:
                if not LocalAuth.available():
                    time.sleep(_UPDATE_SECS)
                    continue
                if rpc is None:
                    rpc = Presence(self.client_id)
                    rpc.connect()
                    print("[discord] Rich Presence connected.", flush=True)
                    last_payload = None

                payload = self._build()
                if payload is None:
                    if last_payload is not None:
                        rpc.clear()
                        last_payload = None
                elif payload != last_payload:
                    rpc.update(**payload)
                    last_payload = payload
                time.sleep(_UPDATE_SECS)
            except Exception as e:  # noqa: BLE001 - Discord closed / pipe lost
                print(f"[discord] presence lost ({e}); will retry.", flush=True)
                rpc = None
                last_payload = None
                time.sleep(_UPDATE_SECS)

    # -- payload ------------------------------------------------------------
    def _build(self) -> dict | None:
        import live_match
        try:
            board = live_match.LiveMatch(LocalAuth(self.region)).build_scoreboard(
                include_stats=False)
        except Exception:  # noqa: BLE001
            return None

        state = board.get("state")
        if state not in ("INGAME", "PREGAME", "MENUS"):
            return None

        # Reset the elapsed timer when the game state changes.
        if state != self._last_state:
            self._last_state = state
            self._state_since = time.time()

        self_p = next((p for p in board.get("players", []) if p.get("isSelf")), None)
        rank_tier = (self_p or {}).get("rankTier", 0)
        rank_name = (self_p or {}).get("rank", "Unrated")
        mode = board.get("mode") or "Custom"
        mapn = board.get("map")

        if state == "INGAME":
            # Always show the score (defaults to 0 - 0 during the buy phase before
            # Riot populates it), in the "mode // a - b" form.
            score = board.get("score") or {}
            ally, enemy = score.get("ally", 0), score.get("enemy", 0)
            agent = (self_p or {}).get("agent")
            return _clean({
                "details": f"{mode} // {ally} - {enemy}",
                "state": rank_name + (f" · {board['side']}" if board.get("side") else ""),
                "large_image": _map_asset(mapn) or "game_icon",
                "large_text": mapn or "VALORANT",
                "small_image": _agent_asset(agent) or str(rank_tier),
                "small_text": agent or rank_name,
                "start": int(self._state_since),
            })
        if state == "PREGAME":
            return _clean({
                "details": f"Agent Select // {mode}",
                "state": rank_name,
                "large_image": _map_asset(mapn) or "game_icon",
                "large_text": mapn or "Agent Select",
                "small_image": str(rank_tier),
                "small_text": rank_name,
                "start": int(self._state_since),
            })
        # MENUS / lobby
        return _clean({
            "details": f"In Lobby // {mode}",
            "state": rank_name,
            "large_image": "game_icon",
            "large_text": "VALORANT",
            "small_image": str(rank_tier),
            "small_text": rank_name,
        })


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None and v != ""}
