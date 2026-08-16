from __future__ import annotations


import json
import os
import tempfile
import threading
import time

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_PATH = os.path.join(_DATA_DIR, "encounters.json")
_LOCK = threading.RLock()


def _empty_store() -> dict:
    return {"version": 2, "accounts": {}, "discardedLegacyPlayers": 0}


def _load() -> dict:
    try:
        with open(_PATH, encoding="utf-8") as fh:
            raw = json.load(fh)
        if isinstance(raw, dict) and raw.get("version") == 2 and isinstance(raw.get("accounts"), dict):
            return raw
        out = _empty_store()
        if isinstance(raw, dict):
            out["discardedLegacyPlayers"] = sum(isinstance(v, dict) for v in raw.values())
        return out
    except Exception:
        return _empty_store()


def _save() -> None:
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


_STORE = _load()
_save()


def _public_entry(source: dict) -> dict:
    row = dict(source)
    stats = source.get("withStats") or {}
    games = int(stats.get("games") or 0)
    deaths = int(stats.get("deaths") or 0)
    hits = int(stats.get("shotsHit") or 0)
    if games:
        row["withKd"] = round(int(stats.get("kills") or 0) / deaths, 2) if deaths else float(int(stats.get("kills") or 0))
        row["withAcs"] = round(float(stats.get("acsTotal") or 0) / games)
        row["withHsPct"] = round(100 * int(stats.get("headshots") or 0) / hits) if hits else None
        row["withStatGames"] = games
    agent_counts = source.get("agentCounts") or {}
    if agent_counts:
        top_agent = max(agent_counts, key=lambda name: (int(agent_counts.get(name) or 0), name))
        row["topAgent"] = top_agent
        row["topAgentGames"] = int(agent_counts.get(top_agent) or 0)
        row["topAgentPortrait"] = (source.get("agentPortraits") or {}).get(top_agent)
        row["topAgentColor"] = (source.get("agentColors") or {}).get(top_agent)
    return row


def _players(owner: str) -> dict:
    account = _STORE.setdefault("accounts", {}).setdefault(str(owner), {"players": {}})
    return account.setdefault("players", {})


def record_board(board: dict | None) -> None:
    if not isinstance(board, dict) or board.get("source") != "local":
        return
    owner = board.get("selfPuuid")
    match_id = board.get("matchId")
    if not owner or not match_id or not isinstance(board.get("players"), list):
        return
    self_team = board.get("selfTeam")
    now = int(time.time())
    changed = False
    with _LOCK:
        store = _players(owner)
        for player in board["players"]:
            if not isinstance(player, dict) or player.get("isSelf") or not player.get("puuid"):
                continue
            puuid = player["puuid"]
            entry = store.setdefault(puuid, {
                "puuid": puuid, "name": None, "withCount": 0, "againstCount": 0,
                "winsWith": 0, "lossesWith": 0, "winsAgainst": 0, "lossesAgainst": 0,
                "lastSeen": 0, "agents": [],
            })
            match_ids = entry.setdefault("matchIds", [])
            legacy_match_id = entry.get("lastMatchId")
            if legacy_match_id and legacy_match_id not in match_ids:
                match_ids.append(legacy_match_id)
            if match_id not in match_ids:
                same_team = self_team is not None and player.get("team") == self_team
                key = "withCount" if same_team else "againstCount"
                entry[key] = int(entry.get(key) or 0) + 1
                match_ids.append(match_id)
                entry["matchIds"] = match_ids[-80:]
                entry["lastMatchId"] = match_id
            for key in ("name", "rank", "peakRank", "rankTier", "peakTier",
                        "rankIcon", "rankColor", "kd", "winRate", "level"):
                if player.get(key) is not None:
                    entry[key] = player.get(key)
            entry["lastSeen"] = now
            agent = player.get("agent")
            if agent and agent != "Unknown" and agent not in entry.setdefault("agents", []):
                entry["agents"].append(agent)
                entry["agents"] = entry["agents"][-8:]
            changed = True
        if changed:
            _save()


def record_result(board: dict | None, won: bool | None) -> None:
    if won is None or not isinstance(board, dict) or board.get("source") != "local":
        return
    owner = board.get("selfPuuid")
    match_id = board.get("matchId")
    if not owner or not match_id:
        return
    self_team = board.get("selfTeam")
    changed = False
    with _LOCK:
        store = _players(owner)
        for player in board.get("players") or []:
            if not isinstance(player, dict) or player.get("isSelf") or not player.get("puuid"):
                continue
            entry = store.get(player["puuid"])
            if not entry:
                continue
            result_ids = entry.setdefault("resultMatchIds", [])
            legacy_result_id = entry.get("lastResultMatchId")
            if legacy_result_id and legacy_result_id not in result_ids:
                result_ids.append(legacy_result_id)
            if match_id in result_ids:
                continue
            same_team = self_team is not None and player.get("team") == self_team
            key = (("winsWith" if won else "lossesWith") if same_team
                   else ("winsAgainst" if won else "lossesAgainst"))
            entry[key] = int(entry.get(key) or 0) + 1
            result_ids.append(match_id)
            entry["resultMatchIds"] = result_ids[-80:]
            entry["lastResultMatchId"] = match_id
            timeline = entry.setdefault("timeline", [])
            timeline.append({"matchId": match_id, "at": int(time.time()),
                             "side": "with" if same_team else "against",
                             "result": "win" if won else "loss",
                             "agent": player.get("agent")})
            entry["timeline"] = timeline[-20:]
            changed = True
        if changed:
            _save()


def backfill_career(owner: str | None, matches: list[dict] | None) -> int:
    if not owner or not isinstance(matches, list):
        return 0
    changed = 0
    dirty = False
    with _LOCK:
        store = _players(owner)
        for match in matches:
            if not isinstance(match, dict) or not match.get("matchId"):
                continue
            match_id = str(match["matchId"])
            result = match.get("result")
            seen_at = int((match.get("startMillis") or 0) / 1000) or int(time.time())
            for teammate in match.get("teammates") or []:
                if not isinstance(teammate, dict) or not teammate.get("puuid"):
                    continue
                puuid = str(teammate["puuid"])
                entry = store.setdefault(puuid, {
                    "puuid": puuid, "name": None, "withCount": 0, "againstCount": 0,
                    "winsWith": 0, "lossesWith": 0, "winsAgainst": 0, "lossesAgainst": 0,
                    "lastSeen": 0, "agents": [],
                })
                match_ids = entry.setdefault("matchIds", [])
                for legacy in (entry.get("lastMatchId"),):
                    if legacy and legacy not in match_ids:
                        match_ids.append(legacy)
                if match_id not in match_ids:
                    entry["withCount"] = int(entry.get("withCount") or 0) + 1
                    match_ids.append(match_id)
                    entry["matchIds"] = match_ids[-80:]
                    changed += 1
                    dirty = True

                result_ids = entry.setdefault("resultMatchIds", [])
                legacy_result = entry.get("lastResultMatchId")
                if legacy_result and legacy_result not in result_ids:
                    result_ids.append(legacy_result)
                if result in ("Victory", "Defeat") and match_id not in result_ids:
                    key = "winsWith" if result == "Victory" else "lossesWith"
                    entry[key] = int(entry.get(key) or 0) + 1
                    result_ids.append(match_id)
                    entry["resultMatchIds"] = result_ids[-80:]
                    dirty = True

                if teammate.get("name"):
                    if entry.get("name") != teammate["name"]:
                        entry["name"] = teammate["name"]
                        dirty = True
                for key in ("rank", "peakRank", "kd", "winRate", "level"):
                    if teammate.get(key) is not None and entry.get(key) != teammate.get(key):
                        entry[key] = teammate.get(key)
                        dirty = True
                previous_seen = int(entry.get("lastSeen") or 0)
                entry["lastSeen"] = max(previous_seen, seen_at)
                dirty = dirty or entry["lastSeen"] != previous_seen
                entry["lastMatchId"] = match_id
                if result in ("Victory", "Defeat"):
                    entry["lastResultMatchId"] = match_id
                agent = teammate.get("agent")
                if agent and agent != "Unknown" and agent not in entry.setdefault("agents", []):
                    entry["agents"].append(agent)
                    entry["agents"] = entry["agents"][-8:]
                    dirty = True
                stat_match_ids = entry.setdefault("withStatMatchIds", [])
                if match_id not in stat_match_ids:
                    stats = entry.setdefault("withStats", {
                        "games": 0, "kills": 0, "deaths": 0, "assists": 0,
                        "acsTotal": 0, "shotsHit": 0, "headshots": 0,
                    })
                    stats["games"] = int(stats.get("games") or 0) + 1
                    for field in ("kills", "deaths", "assists", "shotsHit", "headshots"):
                        stats[field] = int(stats.get(field) or 0) + int(teammate.get(field) or 0)
                    stats["acsTotal"] = float(stats.get("acsTotal") or 0) + float(teammate.get("acs") or 0)
                    stat_match_ids.append(match_id)
                    entry["withStatMatchIds"] = stat_match_ids[-80:]
                    if agent and agent != "Unknown":
                        counts = entry.setdefault("agentCounts", {})
                        counts[agent] = int(counts.get(agent) or 0) + 1
                        if teammate.get("agentPortrait"):
                            entry.setdefault("agentPortraits", {})[agent] = teammate["agentPortrait"]
                        if teammate.get("agentColor"):
                            entry.setdefault("agentColors", {})[agent] = teammate["agentColor"]
                    dirty = True
                timeline = entry.setdefault("timeline", [])
                if not any(item.get("matchId") == match_id for item in timeline):
                    timeline.append({"matchId": match_id, "at": seen_at, "side": "with",
                                     "result": "win" if result == "Victory" else
                                               "loss" if result == "Defeat" else None,
                                     "agent": agent, "map": match.get("map")})
                    entry["timeline"] = sorted(timeline, key=lambda item: item.get("at") or 0)[-40:]
                    dirty = True
        if dirty:
            _save()
    return changed


def enrich_player(owner: str | None, puuid: str | None, fields: dict | None) -> None:
    if not owner or not puuid or not isinstance(fields, dict):
        return
    with _LOCK:
        entry = _players(owner).get(str(puuid))
        if not entry:
            return
        dirty = False
        for key in ("name", "rank", "peakRank", "rankTier", "peakTier",
                    "rankIcon", "rankColor", "kd", "winRate", "level"):
            if fields.get(key) is not None and entry.get(key) != fields.get(key):
                entry[key] = fields.get(key)
                dirty = True
        if dirty:
            _save()


def get_all(owner: str | None, limit: int = 200) -> list[dict]:
    if not owner:
        return []
    with _LOCK:
        entries = [_public_entry(entry) for entry in _players(owner).values()]
    entries.sort(key=lambda entry: (int(entry.get("withCount") or 0) + int(entry.get("againstCount") or 0)), reverse=True)
    return entries[:limit] if limit is not None and limit >= 0 else entries


def account_count() -> int:
    with _LOCK:
        return sum(1 for account in _STORE.get("accounts", {}).values()
                   if isinstance(account, dict) and account.get("players"))


def get_all_accounts(current_owner: str | None = None, limit: int = 200) -> list[dict]:
    merged: dict[str, dict] = {}
    counter_keys = ("withCount", "againstCount", "winsWith", "lossesWith",
                    "winsAgainst", "lossesAgainst")
    with _LOCK:
        accounts = list((_STORE.get("accounts") or {}).items())
        for owner, account in accounts:
            for puuid, source in ((account or {}).get("players") or {}).items():
                row = merged.setdefault(puuid, {"puuid": puuid, "accountsSeen": []})
                if owner not in row["accountsSeen"]:
                    row["accountsSeen"].append(owner)
                for key in counter_keys:
                    row[key] = int(row.get(key) or 0) + int(source.get(key) or 0)
                if int(source.get("lastSeen") or 0) >= int(row.get("lastSeen") or 0):
                    for key in ("name", "rank", "peakRank", "rankTier", "peakTier",
                                "rankIcon", "rankColor", "kd", "winRate", "level",
                                "lastSeen", "lastMatchId"):
                        if source.get(key) is not None:
                            row[key] = source.get(key)
                row["agents"] = list(dict.fromkeys((row.get("agents") or []) +
                                                    (source.get("agents") or [])))[-8:]
                row["timeline"] = sorted((row.get("timeline") or []) +
                                         (source.get("timeline") or []),
                                         key=lambda item: item.get("at") or 0)[-40:]
                source_stats = source.get("withStats") or {}
                stats = row.setdefault("withStats", {})
                for key in ("games", "kills", "deaths", "assists", "acsTotal", "shotsHit", "headshots"):
                    stats[key] = float(stats.get(key) or 0) + float(source_stats.get(key) or 0)
                counts = row.setdefault("agentCounts", {})
                for agent, count in (source.get("agentCounts") or {}).items():
                    counts[agent] = int(counts.get(agent) or 0) + int(count or 0)
                row["agentPortraits"] = {**(row.get("agentPortraits") or {}),
                                         **(source.get("agentPortraits") or {})}
                row["agentColors"] = {**(row.get("agentColors") or {}),
                                      **(source.get("agentColors") or {})}
    entries = [_public_entry(entry) for entry in merged.values()]
    entries.sort(key=lambda entry: (int(entry.get("withCount") or 0) +
                                    int(entry.get("againstCount") or 0)), reverse=True)
    return entries[:limit] if limit is not None and limit >= 0 else entries


def get_one(owner: str | None, puuid: str) -> dict | None:
    if not owner or not puuid:
        return None
    with _LOCK:
        entry = _players(owner).get(puuid)
        return dict(entry) if entry else None


def encounter_for(owner: str | None, puuid: str) -> dict | None:
    entry = get_one(owner, puuid)
    if not entry:
        return None
    return {
        "withCount": int(entry.get("withCount") or 0),
        "againstCount": int(entry.get("againstCount") or 0),
        "winsWith": int(entry.get("winsWith") or 0),
        "lossesWith": int(entry.get("lossesWith") or 0),
        "winsAgainst": int(entry.get("winsAgainst") or 0),
        "lossesAgainst": int(entry.get("lossesAgainst") or 0),
    }
