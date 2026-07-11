from __future__ import annotations

import json
import os
import tempfile
import threading
import time

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_PATH = os.path.join(_DATA_DIR, "encounters.json")

_LOCK = threading.Lock()
_STORE: dict[str, dict] = {}
_LOADED = False

def _load() -> None:
    pass
    global _LOADED
    if _LOADED:
        return
    try:
        with open(_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            _STORE.update({k: v for k, v in data.items() if isinstance(v, dict)})
    except FileNotFoundError:
        pass
    except Exception:
        pass
    _LOADED = True

def _save() -> None:
    pass
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)

        fd, tmp = tempfile.mkstemp(dir=_DATA_DIR, prefix=".encounters-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(_STORE, fh, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, _PATH)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    except Exception:
        pass

_load()

def record_board(board: dict | None) -> None:
    pass
    if not isinstance(board, dict) or board.get("source") != "local":
        return

    match_id = board.get("matchId")
    if not match_id:
        return
    self_team = board.get("selfTeam")
    players = board.get("players") or []
    if not isinstance(players, list):
        return

    now = int(time.time())
    changed = False

    with _LOCK:
        for p in players:
            if not isinstance(p, dict) or p.get("isSelf"):
                continue
            puuid = p.get("puuid")
            if not puuid:
                continue

            entry = _STORE.get(puuid)
            if entry is None:
                entry = {
                    "puuid": puuid,
                    "name": None,
                    "withCount": 0,
                    "againstCount": 0,
                    "lastSeen": 0,
                    "lastMatchId": None,
                    "agents": [],
                    "rank": None,
                    "peakRank": None,
                    "kd": None,
                    "winRate": None,
                    "level": None,
                }
                _STORE[puuid] = entry

            if entry.get("lastMatchId") != match_id:
                if self_team is not None and p.get("team") == self_team:
                    entry["withCount"] = int(entry.get("withCount") or 0) + 1
                else:
                    entry["againstCount"] = int(entry.get("againstCount") or 0) + 1
                entry["lastMatchId"] = match_id

            if p.get("name"):
                entry["name"] = p.get("name")
            entry["rank"] = p.get("rank")
            entry["peakRank"] = p.get("peakRank")
            entry["kd"] = p.get("kd")
            entry["winRate"] = p.get("winRate")
            entry["level"] = p.get("level")
            entry["lastSeen"] = now

            agent = p.get("agent")
            if agent and agent != "Unknown":
                agents = entry.setdefault("agents", [])
                if agent not in agents:
                    agents.append(agent)

            changed = True

        if changed:
            _save()

def record_result(board: dict | None, won: bool | None) -> None:
    pass
    if won is None or not isinstance(board, dict) or board.get("source") != "local":
        return
    match_id = board.get("matchId")
    if not match_id:
        return
    self_team = board.get("selfTeam")
    changed = False
    with _LOCK:
        for p in board.get("players") or []:
            if not isinstance(p, dict) or p.get("isSelf") or not p.get("puuid"):
                continue
            entry = _STORE.get(p["puuid"])
            if entry is None or entry.get("lastResultMatchId") == match_id:
                continue
            entry["lastResultMatchId"] = match_id
            same_team = self_team is not None and p.get("team") == self_team
            key = (("winsWith" if won else "lossesWith") if same_team
                   else ("winsAgainst" if won else "lossesAgainst"))
            entry[key] = int(entry.get(key) or 0) + 1
            changed = True
        if changed:
            _save()

def get_all(limit: int = 200) -> list[dict]:
    pass
    with _LOCK:
        entries = [dict(e) for e in _STORE.values()]
    entries.sort(
        key=lambda e: (int(e.get("withCount") or 0) + int(e.get("againstCount") or 0)),
        reverse=True,
    )
    if limit is not None and limit >= 0:
        return entries[:limit]
    return entries

def get_one(puuid: str) -> dict | None:
    pass
    if not puuid:
        return None
    with _LOCK:
        entry = _STORE.get(puuid)
        return dict(entry) if entry else None

def encounter_for(puuid: str) -> dict | None:
    pass
    if not puuid:
        return None
    with _LOCK:
        entry = _STORE.get(puuid)
        if not entry:
            return None
        return {
            "withCount": int(entry.get("withCount") or 0),
            "againstCount": int(entry.get("againstCount") or 0),
        }
