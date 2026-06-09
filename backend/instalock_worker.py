"""
instalock_worker.py
===================
Background auto-instalock — a background auto-instalock loop.

It runs a daemon thread: `while running:` it polls the game state, and as
soon as it sees PREGAME (agent select) it waits `delay` seconds, then
selects + locks the chosen agent, tracking matches it has already acted on. It
keeps running until the target is locked or the user presses Stop.

We do the same against the local client (raw glz, region-aware), so the web UI
can "arm" an instalock that waits for agent select and fires automatically.

Locking automates the game client and may violate Riot's Terms of Service — it
only runs when the request explicitly turns dry-run OFF.
"""

from __future__ import annotations

import base64
import json
import threading

from agents import resolve_agent
from riot_client import LocalAuth
from vconstants import map_name_from_path


def _self_session_state(presences: list, puuid: str) -> str | None:
    """sessionLoopState for the local player from the presence list."""
    for p in presences or []:
        if p.get("puuid") != puuid:
            continue
        priv = p.get("private")
        if not priv or "{" in str(priv):
            return None
        try:
            data = json.loads(base64.b64decode(str(priv)).decode("utf-8"))
        except Exception:  # noqa: BLE001
            return None
        if "matchPresenceData" in data:
            return data["matchPresenceData"].get("sessionLoopState")
        return data.get("sessionLoopState")
    return None


def _side_from_match(match: dict, puuid: str) -> str | None:
    ally = (match or {}).get("AllyTeam") or {}
    team = ally.get("TeamID")
    return {"Red": "Attacker", "Blue": "Defender"}.get(team)


class InstalockWorker:
    """Singleton background worker that arms/auto-fires an instalock."""

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.state = {"running": False, "status": "idle", "message": "",
                      "agent": None, "mode": "lock", "side": None, "map": None}

    def status(self) -> dict:
        return dict(self.state)

    @staticmethod
    def _normalize_per_map(per_map: dict | None) -> dict:
        """Lower-cased {mapName: agentName} so map lookups are case-insensitive."""
        out: dict[str, str] = {}
        for k, v in (per_map or {}).items():
            if k and v:
                out[str(k).strip().lower()] = str(v).strip()
        return out

    def start(self, agent_id: str, mode: str = "lock", delay: float = 0.0,
              region: str | None = None, per_map: dict | None = None) -> dict:
        agent = resolve_agent(agent_id)
        if not agent:
            return {"ok": False, "message": f"Unknown agent '{agent_id}'."}
        per_map_norm = self._normalize_per_map(per_map)
        # Validate every per-map agent up front so the user gets fast feedback.
        for mapn, name in per_map_norm.items():
            if not resolve_agent(name):
                return {"ok": False,
                        "message": f"Unknown agent '{name}' for map '{mapn}'."}
        self.stop()                                   # cancel any prior run
        with self._lock:
            self._stop.clear()
            self.state.update(running=True, status="waiting", agent=agent["name"],
                              mode=mode, side=None, map=None,
                              message="Armed — waiting for agent select…")
            self._thread = threading.Thread(
                target=self._loop,
                args=(agent, mode, float(delay or 0), region, per_map_norm),
                daemon=True)
            self._thread.start()
        return {"ok": True, "running": True, "agent": agent["name"],
                "status": "waiting", "perMap": per_map_norm}

    def stop(self) -> dict:
        self._stop.set()
        t = self._thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=2.5)
        if self.state.get("status") in ("waiting", "running"):
            self.state.update(status="stopped", message="Stopped.")
        self.state.update(running=False)
        return {"ok": True, "running": False}

    # -- the loop -----------------------------------------------------------
    def _loop(self, agent: dict, mode: str, delay: float, region, per_map: dict):
        done: set[str] = set()
        try:
            auth = LocalAuth(region)
            auth.headers()
        except Exception as e:  # noqa: BLE001
            self.state.update(running=False, status="error",
                              message=f"Couldn't reach the local client: {e}")
            return

        while not self._stop.is_set():
            try:
                presences = (auth.local_get("/chat/v4/presences") or {}).get("presences", [])
                st = _self_session_state(presences, auth.puuid)
                if st == "PREGAME":
                    pg = auth.glz_get(f"/pregame/v1/players/{auth.puuid}")
                    mid = pg.get("MatchID") if isinstance(pg, dict) else None
                    if mid and mid not in done:
                        if delay > 0 and self._stop.wait(delay):
                            break
                        match = auth.glz_get(f"/pregame/v1/matches/{mid}")
                        side = _side_from_match(match, auth.puuid)
                        map_name = map_name_from_path((match or {}).get("MapID", ""))
                        # Per-map override picks the agent for this map; else default.
                        chosen = agent
                        override = per_map.get((map_name or "").lower())
                        if override:
                            resolved = resolve_agent(override)
                            if resolved:
                                chosen = resolved
                        agent_id = chosen["uuid"]
                        auth.glz_post(f"/pregame/v1/matches/{mid}/select/{agent_id}")
                        if mode == "lock":
                            auth.glz_post(f"/pregame/v1/matches/{mid}/lock/{agent_id}")
                        done.add(mid)
                        self.state.update(
                            running=False, status="locked", side=side,
                            agent=chosen["name"], map=map_name,
                            message=f"{'Locked' if mode == 'lock' else 'Hovered'} "
                                    f"{chosen['name']}"
                                    + (f" on {map_name}" if map_name and map_name != "Unknown" else "")
                                    + "!"
                                    + (f"  You're {side}." if side else ""))
                        return                          # target done — stop
                    # already acted on this match; keep waiting for the next
                elif st is None:
                    self.state.update(running=False, status="error",
                                      message="Local client not reachable — is VALORANT open?")
                    return
                if self._stop.wait(1.0):                 # poll ~1s while waiting
                    break
            except Exception:  # noqa: BLE001 - transient; refresh token and retry
                try:
                    auth.headers(refresh=True)
                except Exception:  # noqa: BLE001
                    pass
                if self._stop.wait(1.5):
                    break

        self.state.update(running=False)
        if self.state.get("status") not in ("locked", "error"):
            self.state.update(status="stopped", message="Stopped.")
