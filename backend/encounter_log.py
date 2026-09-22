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
    for side in ("with", "against"):
        stats = source.get(f"{side}Stats") or {}
        games = int(stats.get("games") or 0)
        deaths = int(stats.get("deaths") or 0)
        hits = int(stats.get("shotsHit") or 0)
        if games:
            row[f"{side}Kd"] = round(int(stats.get("kills") or 0) / deaths, 2) if deaths else float(int(stats.get("kills") or 0))
            row[f"{side}Acs"] = round(float(stats.get("acsTotal") or 0) / games)
            row[f"{side}HsPct"] = round(100 * int(stats.get("headshots") or 0) / hits) if hits else None
            row[f"{side}StatGames"] = games
    agent_counts = source.get("agentCounts") or {}
    if agent_counts:
        top_agent = max(agent_counts, key=lambda name: (int(agent_counts.get(name) or 0), name))
        row["topAgent"] = top_agent
        row["topAgentGames"] = int(agent_counts.get(top_agent) or 0)
        row["topAgentPortrait"] = (source.get("agentPortraits") or {}).get(top_agent)
        row["topAgentColor"] = (source.get("agentColors") or {}).get(top_agent)
    elif source.get("agents"):
        top_agent = source["agents"][-1]
        row["topAgent"] = top_agent
        row["topAgentGames"] = 1
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
    if match_id == "lobby" or board.get("state") not in (None, "INGAME"):
        return
    if not owner or not match_id or not isinstance(board.get("players"), list):
        return
    self_team = board.get("selfTeam")
    if not self_team or self_team == "Neutral":
        return
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
            new_match = match_id not in match_ids
            if new_match:
                same_team = self_team is not None and player.get("team") == self_team
                key = "withCount" if same_team else "againstCount"
                entry[key] = int(entry.get(key) or 0) + 1
                match_ids.append(match_id)
                entry["matchIds"] = match_ids
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
            if agent and agent != "Unknown":
                if new_match:
                    counts = entry.setdefault("agentCounts", {})
                    counts[agent] = int(counts.get(agent) or 0) + 1
                    entry.setdefault("agentMatchIds", []).append(match_id)
                if player.get("agentPortrait"):
                    entry.setdefault("agentPortraits", {})[agent] = player["agentPortrait"]
                if player.get("agentColor"):
                    entry.setdefault("agentColors", {})[agent] = player["agentColor"]
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
            entry["resultMatchIds"] = result_ids
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
    with _LOCK:
        store = _players(owner)
        for match in sorted(matches, key=lambda row: row.get("startMillis") or 0):
            match_id = match.get("matchId")
            if not match_id or match_id == "lobby":
                continue
            result = match.get("result")
            seen_at = int((match.get("startMillis") or 0) / 1000) or int(time.time())
            participants = [("with", p) for p in match.get("teammates") or []]
            participants += [("against", p) for p in match.get("opponents") or []]
            for side, player in participants:
                puuid = player.get("puuid")
                if not puuid or puuid == owner:
                    continue
                entry = store.setdefault(puuid, {
                    "puuid": puuid, "name": None, "withCount": 0, "againstCount": 0,
                    "winsWith": 0, "lossesWith": 0, "winsAgainst": 0, "lossesAgainst": 0,
                    "lastSeen": 0, "agents": [],
                })
                match_ids = entry.setdefault("matchIds", [])
                legacy = entry.get("lastMatchId")
                if legacy and legacy not in match_ids:
                    match_ids.append(legacy)
                if match_id not in match_ids:
                    entry[f"{side}Count"] = int(entry.get(f"{side}Count") or 0) + 1
                    match_ids.append(match_id)
                    changed += 1
                result_ids = entry.setdefault("resultMatchIds", [])
                legacy = entry.get("lastResultMatchId")
                if legacy and legacy not in result_ids:
                    result_ids.append(legacy)
                if result in ("Victory", "Defeat") and match_id not in result_ids:
                    key = ("wins" if result == "Victory" else "losses") + side.title()
                    entry[key] = int(entry.get(key) or 0) + 1
                    result_ids.append(match_id)
                    entry["lastResultMatchId"] = match_id

                if seen_at >= int(entry.get("lastSeen") or 0):
                    for key in ("name", "rank", "peakRank", "rankTier", "peakTier",
                                "rankIcon", "rankColor", "kd", "winRate", "level"):
                        if player.get(key) is not None:
                            entry[key] = player[key]
                    entry["lastSeen"] = seen_at
                    entry["lastMatchId"] = match_id
                else:
                    for key in ("name", "rank", "peakRank", "rankTier", "peakTier",
                                "rankIcon", "rankColor", "level"):
                        if entry.get(key) is None and player.get(key) is not None:
                            entry[key] = player[key]
                agent = player.get("agent")
                if agent and agent != "Unknown":
                    if agent not in entry.setdefault("agents", []):
                        entry["agents"] = (entry["agents"] + [agent])[-8:]
                    agent_ids = entry.setdefault("agentMatchIds", [])
                    if not agent_ids:
                        agent_ids.extend(entry.get("withStatMatchIds") or [])
                    if match_id not in agent_ids:
                        counts = entry.setdefault("agentCounts", {})
                        counts[agent] = int(counts.get(agent) or 0) + 1
                        agent_ids.append(match_id)
                    if player.get("agentPortrait"):
                        entry.setdefault("agentPortraits", {})[agent] = player["agentPortrait"]
                    if player.get("agentColor"):
                        entry.setdefault("agentColors", {})[agent] = player["agentColor"]

                stat_ids = entry.setdefault(f"{side}StatMatchIds", [])
                if match_id not in stat_ids:
                    stats = entry.setdefault(f"{side}Stats", {})
                    stats["games"] = int(stats.get("games") or 0) + 1
                    for key in ("kills", "deaths", "assists", "shotsHit", "headshots"):
                        stats[key] = int(stats.get(key) or 0) + int(player.get(key) or 0)
                    stats["acsTotal"] = float(stats.get("acsTotal") or 0) + float(player.get("acs") or 0)
                    stat_ids.append(match_id)
                timeline = entry.setdefault("timeline", [])
                item = next((item for item in timeline if item.get("matchId") == match_id), None)
                if item is None:
                    item = {"matchId": match_id}
                    timeline.append(item)
                item.update({"at": seen_at, "side": side,
                             "result": "win" if result == "Victory" else "loss" if result == "Defeat" else None,
                             "agent": agent, "map": match.get("map")})
                entry["timeline"] = sorted(timeline, key=lambda item: item.get("at") or 0)[-40:]
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
                for side in ("with", "against"):
                    source_stats = source.get(f"{side}Stats") or {}
                    stats = row.setdefault(f"{side}Stats", {})
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
