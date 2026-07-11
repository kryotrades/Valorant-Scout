from __future__ import annotations

import base64
import threading
import time

from riot_client import LocalAuth

_CID_PARTS = ("MTAxMjQwMjIx", "MTEzNDkxMDU0Ng==")
_CLIENT_ID = (base64.b64decode(_CID_PARTS[0]).decode()
              + base64.b64decode(_CID_PARTS[1]).decode())

_UPDATE_SECS = 15
_worker: "_Worker | None" = None

_DISCORD_USER = {"name": None, "username": None, "id": None}

def discord_user() -> dict:
    return dict(_DISCORD_USER)

def probe_discord_identity() -> None:
    pass
    if _DISCORD_USER["name"]:
        return
    try:
        import json as _json
        import struct as _struct
        from pypresence import Presence
        from pypresence.utils import get_ipc_path
        from pypresence.exceptions import (DiscordNotFound, InvalidPipe,
                                           InvalidID, DiscordError)

        class _IdPresence(Presence):
            captured = None

            async def handshake(self):
                ipc_path = get_ipc_path(self.pipe)
                if not ipc_path:
                    raise DiscordNotFound
                await self.create_reader_writer(ipc_path)
                self.send_data(0, {"v": 1, "client_id": self.client_id})
                preamble = await self.sock_reader.read(8)
                if len(preamble) < 8:
                    raise InvalidPipe
                _code, length = _struct.unpack("<ii", preamble)
                data = _json.loads(await self.sock_reader.read(length))
                if "code" in data:
                    if data.get("message") == "Invalid Client ID":
                        raise InvalidID
                    raise DiscordError(data["code"], data["message"])
                _IdPresence.captured = data

        rpc = _IdPresence(_CLIENT_ID)
        rpc.connect()
        u = ((_IdPresence.captured or {}).get("data") or {}).get("user") or {}
        try:
            rpc.close()
        except Exception:
            pass
        handle = u.get("username")
        disc = u.get("discriminator")
        if handle and disc and disc not in ("0", "0000"):
            handle = f"{handle}#{disc}"
        name = u.get("global_name") or u.get("username")
        if name or handle:
            _DISCORD_USER["name"] = str(name).strip() if name else None
            _DISCORD_USER["username"] = handle
            _DISCORD_USER["id"] = u.get("id")
    except Exception:
        pass

def maybe_start(region: str | None = None) -> None:
    pass
    global _worker
    import os
    if os.getenv("DISCORD_RPC", "true").strip().lower() == "false":
        return
    if _worker is not None:
        return
    _worker = _Worker(region)
    _worker.start()

_BUTTONS = [{"label": "Get Valorant Scout", "url": "https://github.com/kryotrades/valorant-scout"}]

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

    def _run(self):
        try:
            from pypresence import Presence
        except Exception:
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
            except Exception as e:
                print(f"[discord] presence lost ({e}); will retry.", flush=True)
                rpc = None
                last_payload = None
                time.sleep(_UPDATE_SECS)

    def _build(self) -> dict | None:
        import live_match
        try:
            board = live_match.LiveMatch(LocalAuth(self.region)).build_scoreboard(
                include_stats=False)
        except Exception:
            return None

        state = board.get("state")
        if state not in ("INGAME", "PREGAME", "MENUS"):
            return None

        if state != self._last_state:
            self._last_state = state
            self._state_since = time.time()

        self_p = next((p for p in board.get("players", []) if p.get("isSelf")), None)
        rank_name = (self_p or {}).get("rank", "Unrated")
        rank_icon = (self_p or {}).get("rankIcon")
        if not (self_p or {}).get("rankTier"):

            try:
                from riot_client import _self_presence_private
                from vconstants import rank_from_tier
                import valapi
                priv = _self_presence_private(LocalAuth(self.region)) or {}
                tier = (priv.get("playerPresenceData") or {}).get("competitiveTier") or 0
                if tier:
                    rank_name = rank_from_tier(tier)["name"]
                    rank_icon = valapi.rank_icon(tier)
            except Exception:
                pass
        mode = board.get("mode") or "Custom"
        mapn = board.get("map")
        splash = board.get("mapSplash")

        if state == "INGAME":
            agent = (self_p or {}).get("agent")

            score = board.get("score") or {}
            details = f"{mode} // {score.get('ally', 0)} - {score.get('enemy', 0)}"
            dm = mode in ("Deathmatch", "Team Deathmatch")
            status = rank_name if dm else                rank_name + (f" · {board['side']}" if board.get("side") else "")

            comp = mode == "Competitive"
            small = rank_icon if comp else ((self_p or {}).get("agentPortrait") or rank_icon)
            small_text = rank_name if comp else (agent or rank_name)
            return _clean({
                "details": details,
                "state": status,
                "large_image": splash or "game_icon",
                "large_text": mapn or "VALORANT",
                "small_image": small,
                "small_text": small_text,
                "start": int(self._state_since),
                "buttons": _BUTTONS,
            })
        if state == "PREGAME":
            return _clean({
                "details": f"Agent Select — {mode}",
                "state": rank_name,
                "large_image": splash or "game_icon",
                "large_text": mapn or "Agent Select",
                "small_image": rank_icon,
                "small_text": rank_name,
                "start": int(self._state_since),
                "buttons": _BUTTONS,
            })

        queue = board.get("queue") or {}
        qname = queue.get("queueName") or mode
        size = queue.get("partySize") or 1
        if queue.get("inQueue"):
            details = f"In Queue — {qname}"
            start = int(queue.get("queuedAt") or self._state_since)
        else:
            details = f"Lobby — {qname}"
            start = None
        return _clean({
            "details": details,
            "state": f"In a Party ({size} of 5)" if size > 1 else rank_name,
            "large_image": "game_icon",
            "large_text": "VALORANT",
            "small_image": rank_icon,
            "small_text": rank_name,
            "start": start,
            "buttons": _BUTTONS,
        })

def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None and v != ""}
