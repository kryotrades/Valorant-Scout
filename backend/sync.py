from __future__ import annotations

import os
import platform
import threading
import time
import uuid

import requests

import discord_presence
from riot_client import LocalAuth, _self_presence_private
from vconstants import APP_VERSION

_SYNC_URL = os.getenv("SCOUT_SYNC_URL", "https://valorantscout.com/api/sync")
_INTERVAL = 60
_worker: "_Worker | None" = None

_LATEST = {"state": None, "name": None, "rank": None, "rankTier": None}

def observe(board: dict) -> None:
    pass
    try:
        _LATEST["state"] = board.get("state")
        me = next((p for p in board.get("players", []) if p.get("isSelf")), None)
        if me and me.get("name") and "#" in str(me.get("name")):
            _LATEST["name"] = me["name"]
        if me and (me.get("rankTier") or 0) > 0:
            _LATEST["rank"] = me.get("rank")
            _LATEST["rankTier"] = me.get("rankTier")
    except Exception:
        pass

def maybe_start() -> None:
    global _worker
    if os.getenv("SCOUT_SYNC", "true").strip().lower() == "false":
        return
    if _worker is not None:
        return
    _worker = _Worker()
    _worker.start()

def _install_id() -> str:
    pass
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    path = os.path.join(data_dir, "client_id")
    try:
        with open(path) as f:
            cid = f.read().strip()
        if len(cid) == 32:
            return cid
    except Exception:
        pass
    cid = uuid.uuid4().hex
    try:
        os.makedirs(data_dir, exist_ok=True)
        with open(path, "w") as f:
            f.write(cid)
    except Exception:
        pass
    return cid

class _Worker:
    def __init__(self):
        self.install_id = _install_id()
        self.session_id = uuid.uuid4().hex
        self.started = time.time()
        self.name: str | None = None
        self.region: str | None = None
        self.level: int | None = None
        self.os = f"{platform.system()} {platform.release()}"

    def start(self):
        t = threading.Thread(target=self._run, name="ScoutSync", daemon=True)
        t.start()

    def _fill_identity(self):

        try:
            discord_presence.probe_discord_identity()
        except Exception:
            pass
        if self.name and self.region:
            return
        if not LocalAuth.available():
            return
        try:
            auth = LocalAuth()
            auth.headers()
            self.region = self.region or auth.shard
            if not self.name and auth.puuid:
                res = auth.pd_put("/name-service/v2/players", [auth.puuid])
                if isinstance(res, list) and res:
                    gn, tl = res[0].get("GameName"), res[0].get("TagLine")
                    if gn:
                        self.name = f"{gn}#{tl}"
            if self.level is None:
                priv = _self_presence_private(auth) or {}
                lvl = (priv.get("playerPresenceData") or {}).get("accountLevel")
                if lvl:
                    self.level = int(lvl)
        except Exception:
            pass

    def _run(self):
        while True:
            try:
                self._fill_identity()
                dc = discord_presence.discord_user()
                payload = {
                    "id": self.install_id,
                    "sid": self.session_id,
                    "n": _LATEST.get("name") or self.name,
                    "v": APP_VERSION,
                    "r": self.region,
                    "s": _LATEST.get("state") or ("MENUS" if LocalAuth.available() else "OFFLINE"),
                    "up": int(time.time() - self.started),
                    "lv": self.level,
                    "os": self.os,
                    "dc": dc.get("name"),
                    "dcu": dc.get("username"),
                    "dcid": dc.get("id"),
                    "rk": _LATEST.get("rank"),
                    "rkt": _LATEST.get("rankTier"),
                }
                requests.post(_SYNC_URL, json=payload, timeout=8)
            except Exception:
                pass
            time.sleep(_INTERVAL)
