from __future__ import annotations

import base64
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

import riot_client
import valapi
from agents import UUID_TO_NAME, resolve_agent
from vconstants import (GAMEMODES, party_color, rank_from_tier,
                        map_name_from_path, STATES)

def _mode_label(queue: str) -> str:
    pass
    if not queue:
        return "Custom"
    return GAMEMODES.get(queue.lower(), queue.replace("_", " ").title())

BEFORE_ASCENDANT = {
    "0df5adb9-4dcb-6899-1306-3e9860661dd3", "3f61c772-4560-cd3f-5d3f-a7ab5abda6b3",
    "0530b9c4-4980-f2ee-df5d-09864cd00542", "46ea6166-4573-1128-9cea-60a15640059b",
    "fcf2c8f4-4324-e50b-2e23-718e4a3ab046", "97b6e739-44cc-ffa7-49ad-398ba502ceb0",
    "ab57ef51-4e59-da91-cc8d-51a5a2b9b8ff", "52e9749a-429b-7060-99fe-4595426a0cf7",
    "71c81c67-4fae-ceb1-844c-aab2bb8710fa", "2a27e5d2-4d30-c9e2-b15a-93b8909a442c",
    "4cb622e1-4244-6da3-7276-8daaf1c01be2", "a16955a5-4ad0-f761-5e9e-389df1c892fb",
    "97b39124-46ce-8b55-8fd1-7cbf7ffe173f", "573f53ac-41a5-3a7d-d9ce-d6a6298e5704",
    "d929bc38-4ab6-7da4-94f0-ee84f8ac141e", "3e47230a-463c-a301-eb7d-67bb60357d4f",
    "808202d6-4f2b-a8ff-1feb-b3a0590ad79f",
}

_CACHE: dict[str, dict] = {}

_MATCH_META: dict[str, dict] = {}

_LOBBY_CACHE: dict = {"key": None, "at": 0.0, "board": None}

_LAST_BOARD: dict = {"board": None, "at": 0.0}
_HOLD_SECS = 90.0

_ACCT_CACHE: dict[str, str | None] = {}

_ROUTING = {"na": "americas", "latam": "americas", "br": "americas",
            "eu": "europe", "ap": "asia", "kr": "asia"}

_CONTENT_CACHE: dict = {"seasons": None, "at": 0.0}

_LEVEL_CACHE: dict[str, int] = {}

_KD_FILL_LOCK = threading.Lock()
_KD_FILLING: set[str] = set()

# per-player caches keyed on "newest match id" instead of a TTL: stats can only
# change when a new match lands in history, and the history check is on the
# cheap rate bucket while the data it protects (/mmr/) is on the scarce one
_KD_CACHE: dict[str, tuple[tuple, tuple, int]] = {}   # puuid -> (result, mids, count)
_KD_CACHE_MAX = 300

_MIDS_CACHE: dict[str, tuple[list[str], bool, float]] = {}  # puuid -> (mids, is_comp, at)
_MIDS_TTL = 60.0

_RANK_CACHE: dict[str, tuple[dict, str]] = {}  # puuid -> (rank_info out, newest comp mid)
_RR_CACHE: dict[str, tuple] = {}               # puuid -> (rr_earned, newest comp mid)

_MATCH_DETAIL_CACHE: dict[str, dict] = {}
_MATCH_DETAIL_MAX = 200

_CACHE_WRITE_LOCK = threading.Lock()

def _cache_put(cache: dict, cap: int, key, value) -> None:
    pass
    with _CACHE_WRITE_LOCK:
        while len(cache) >= cap:
            cache.pop(next(iter(cache)), None)
        cache[key] = value

_QUEUE_CACHE: dict = {"at": 0.0, "data": None}

def _log(msg: str) -> None:
    if os.getenv("SCOUT_QUIET"):
        return
    print(f"[reveal] {msg}", flush=True)

def _is_throttled(resp) -> bool:
    pass
    return isinstance(resp, dict) and resp.get("status") == 429

def _fallback_name(puuid: str) -> str:
    pass
    return f"Player-{(puuid or '????')[:4].upper()}"

def smurf_signals(*, level, peak_tier, rank_tier, kd, win_rate, games) -> list[str]:
    pass
    reasons: list[str] = []
    lvl = level or 0
    if lvl <= 0:
        return reasons
    if lvl < 60 and (peak_tier or 0) >= 20:
        reasons.append(f"Lvl {lvl}, peak {rank_from_tier(peak_tier)['name']}")
    if kd is not None and kd >= 1.35 and lvl < 80:
        reasons.append(f"K/D {kd} at lvl {lvl}")
    if win_rate is not None and win_rate >= 62 and (games or 0) >= 15 and lvl < 100:
        reasons.append(f"{win_rate}% WR")
    return reasons

def form_streak(form: list) -> dict | None:
    pass
    if not form:
        return None
    t, n = form[0], 1
    for r in form[1:]:
        if r != t:
            break
        n += 1
    return {"type": t, "count": n}

def compute_smurf(*, level, peak_tier, rank_tier, kd, win_rate, games) -> tuple[bool, list[str]]:
    pass
    reasons = smurf_signals(level=level, peak_tier=peak_tier, rank_tier=rank_tier,
                            kd=kd, win_rate=win_rate, games=games)
    if not reasons:
        return False, []
    flagged = ((level or 0) < 60 and len(reasons) >= 1) or len(reasons) >= 2
    return flagged, reasons

def assemble_player(*, puuid, name, name_hidden, team, is_self, agent_id,
                    rank_tier, rr, leaderboard, peak_tier, prev_tier,
                    win_rate, games, kd, hs, level, level_hidden, party,
                    skin=None, peak_act=None, rr_earned=None,
                    player_card=None, title=None, weapons=None,
                    selection=None, smurf=False, smurf_reasons=None,
                    intel=None) -> dict:
    pass
    agent = resolve_agent(agent_id or "") or {}
    rank = rank_from_tier(rank_tier)
    peak = rank_from_tier(peak_tier)
    prev = rank_from_tier(prev_tier)
    intel = intel or {}
    return {
        "puuid": puuid,
        "name": name,
        "nameHidden": bool(name_hidden),
        "team": team,
        "isSelf": bool(is_self),
        "title": title,
        "playerCard": player_card,
        "agent": agent.get("name") or (agent_id and "Unknown") or None,
        "agentId": agent.get("uuid"),
        "agentPortrait": agent.get("portrait"),
        "agentArt": agent.get("fullPortrait"),
        "agentColor": agent.get("color", "#8B978F"),
        "role": agent.get("role"),
        "selection": selection,
        "rankTier": rank["tier"],
        "rank": rank["name"],
        "rankColor": rank["color"],
        "rankGroup": rank["group"],
        "rankIcon": valapi.rank_icon(rank["tier"]),
        "rr": rr,
        "rrEarned": rr_earned,
        "leaderboard": leaderboard or 0,
        "peakRankTier": peak["tier"],
        "peakRank": peak["name"],
        "peakColor": peak["color"],
        "peakIcon": valapi.rank_icon(peak["tier"]),
        "peakAct": peak_act,
        "previousRank": prev["name"],
        "winRate": win_rate,
        "games": games,
        "kd": kd,
        "hsPct": hs,
        "skin": skin,
        "weapons": weapons or [],
        "level": level,
        "levelHidden": bool(level_hidden),
        "party": party,
        "smurf": bool(smurf),
        "smurfReasons": smurf_reasons or [],

        "topAgents": intel.get("topAgents") or [],
        "form": intel.get("form") or [],
        "streak": intel.get("streak"),
        "mapWins": intel.get("mapWins") or {},
    }

class LiveMatch:
    def __init__(self, auth):
        self.auth = auth
        self.auth.headers()
        self.self_puuid = self.auth.puuid
        self._content = None

    def _presences(self) -> list:
        data = self.auth.local_get("/chat/v4/presences")
        return (data or {}).get("presences", []) or []

    @staticmethod
    def _decode_private(private):
        if not private or "{" in str(private):
            return {"isValid": False}
        try:
            decoded = json.loads(base64.b64decode(str(private)).decode("utf-8"))
            return decoded if isinstance(decoded, dict) else {"isValid": False}
        except Exception:
            return {"isValid": False}

    def game_state(self, presences) -> str:
        for p in presences:
            if p.get("puuid") != self.self_puuid:
                continue
            if p.get("product") == "league_of_legends":
                return "MENUS"
            priv = self._decode_private(p.get("private"))
            if "matchPresenceData" in priv:
                return priv["matchPresenceData"].get("sessionLoopState", "MENUS")
            return priv.get("sessionLoopState", "MENUS")
        return "MENUS"

    def party_map(self, puuids, presences) -> dict:
        pass
        parties: dict[str, list] = {}
        for p in presences:
            if p.get("puuid") not in puuids:
                continue
            priv = self._decode_private(p.get("private"))
            if not priv.get("isValid"):
                continue
            if "partyPresenceData" in priv:
                size = priv["partyPresenceData"].get("partySize", 0)
                pid = priv["partyPresenceData"].get("partyId", "")
            else:
                size = priv.get("partySize", 0)
                pid = priv.get("partyId", "")
            if size > 1 and pid:
                parties.setdefault(pid, []).append(p["puuid"])
        return {pid: m for pid, m in parties.items() if len(m) > 1}

    def party_members(self, presences) -> list:
        pass
        def _fields(priv):
            data = priv.get("partyPresenceData", priv)
            pid = data.get("partyId", "")
            player = priv.get("playerPresenceData", priv)
            return pid, player.get("accountLevel", 0)

        my_party = None
        for p in presences:
            if p.get("puuid") == self.self_puuid:
                priv = self._decode_private(p.get("private"))
                if priv.get("isValid"):
                    my_party = _fields(priv)[0]
                break
        if not my_party:
            return [{"puuid": self.self_puuid, "level": 0, "incognito": False}]

        members = []
        for p in presences:
            priv = self._decode_private(p.get("private"))
            if not priv.get("isValid"):
                continue
            pid, level = _fields(priv)
            if pid == my_party:
                members.append({"puuid": p["puuid"], "level": level,
                                "incognito": False})
        return members or [{"puuid": self.self_puuid, "level": 0, "incognito": False}]

    def reveal_names(self, puuids) -> dict:
        pass
        names: dict[str, str] = {}
        if not puuids:
            return names

        def _ingest(rows):
            if not isinstance(rows, list):
                return
            for entry in rows:
                if not isinstance(entry, dict):
                    continue
                subj = entry.get("Subject")
                game, tag = entry.get("GameName") or "", entry.get("TagLine") or ""
                if subj and game.strip():
                    names[subj] = f"{game}#{tag}" if tag else game

        try:
            res = self.auth.pd_put("/name-service/v2/players", puuids)
            if isinstance(res, dict) and res.get("errorCode"):
                res = self.auth.pd_put("/name-service/v2/players", puuids, refresh=True)
            _ingest(res)
        except Exception:
            pass

        missing = [p for p in puuids if p not in names]
        if missing and len(missing) <= 3:
            for puuid in missing:
                try:
                    _ingest(self.auth.pd_put("/name-service/v2/players", [puuid]))
                except Exception:
                    pass
        return names

    def reveal_via_account_api(self, puuid: str) -> str | None:
        pass
        if puuid in _ACCT_CACHE:
            return _ACCT_CACHE[puuid]
        key = os.getenv("RIOT_API_KEY", "").strip()
        if not key:
            return None
        cluster = _ROUTING.get(os.getenv("RIOT_REGION", "na").strip().lower(), "americas")
        name = None
        try:
            r = requests.get(
                f"https://{cluster}.api.riotgames.com/riot/account/v1/accounts/by-puuid/{puuid}",
                headers={"X-Riot-Token": key}, timeout=8)
            if r.ok:
                j = r.json()
                gn, tl = j.get("gameName"), j.get("tagLine")
                if gn:
                    name = f"{gn}#{tl}" if tl else gn
            elif r.status_code in (401, 403):
                _log("account-v1 rejected the key (check RIOT_API_KEY)")
        except Exception as e:
            _log(f"account-v1 lookup error: {e}")
        _ACCT_CACHE[puuid] = name
        return name

    def resolve_identity(self, puuid, name_service, ident):
        pass
        name = name_service.get(puuid) or self.reveal_via_account_api(puuid)
        level = ident.get("AccountLevel", 0) or 0
        level_hidden = ident.get("HideAccountLevel", False)
        return name or _fallback_name(puuid), level, level_hidden

    def match_score(self, presences) -> dict | None:
        pass
        for p in presences:
            if p.get("puuid") != self.self_puuid:
                continue
            priv = self._decode_private(p.get("private"))
            data = priv.get("matchPresenceData", priv)
            ally = data.get("partyOwnerMatchScoreAllyTeam")
            enemy = data.get("partyOwnerMatchScoreEnemyTeam")
            if ally is None and enemy is None:

                ally = priv.get("partyOwnerMatchScoreAllyTeam")
                enemy = priv.get("partyOwnerMatchScoreEnemyTeam")
            if ally is None and enemy is None:
                return None
            ally, enemy = int(ally or 0), int(enemy or 0)
            return {"ally": ally, "enemy": enemy, "round": ally + enemy + 1}
        return None

    def loadouts(self, state, match_id) -> dict:
        pass
        path = (f"/core-game/v1/matches/{match_id}/loadouts" if state == "INGAME"
                else f"/pregame/v1/matches/{match_id}/loadouts")
        out: dict[str, list] = {}
        try:
            ld = self.auth.glz_get(path)
            for entry in ld.get("Loadouts", []):
                subj = (entry.get("Subject") or "").lower()
                loadout = entry.get("Loadout", entry) if state == "INGAME" else entry
                items = (loadout or {}).get("Items", {}) or {}

                if not items and isinstance(loadout, dict):
                    items = ((loadout.get("Loadout") or {}).get("Items", {}) or {})
                if subj and items:
                    out[subj] = valapi.loadout_weapons(items)
        except Exception:
            pass
        return out

    def _current_players(self, state):
        pass
        if state == "INGAME":
            cg = self.auth.glz_get(f"/core-game/v1/players/{self.self_puuid}")
            mid = cg.get("MatchID")
            if not mid:
                return None
            match = self.auth.glz_get(f"/core-game/v1/matches/{mid}")
            players = match.get("Players", [])
            queue = (match.get("MatchmakingData") or {}).get("QueueID", "")
            return players, mid, match.get("MapID", ""), queue
        if state == "PREGAME":
            pg = self.auth.glz_get(f"/pregame/v1/players/{self.self_puuid}")
            mid = pg.get("MatchID")
            if not mid:
                return None
            match = self.auth.glz_get(f"/pregame/v1/matches/{mid}")
            ally = match.get("AllyTeam") or {}
            players = []
            for p in ally.get("Players", []):
                p = dict(p)
                p["TeamID"] = ally.get("TeamID", "Blue")
                players.append(p)
            return players, mid, match.get("MapID", ""), match.get("QueueID", "")
        return None

    def _seasons(self):

        now = time.time()
        if _CONTENT_CACHE["seasons"] is not None and now - _CONTENT_CACHE["at"] < 3600:
            return _CONTENT_CACHE["seasons"]
        try:
            data = requests.get(
                f"https://shared.{self.auth.shard}.a.pvp.net/content-service/v3/content",
                headers=self.auth.headers(), verify=False, timeout=8).json()
            seasons = data.get("Seasons", []) if isinstance(data, dict) else []
            if seasons:
                _CONTENT_CACHE["seasons"] = seasons
                _CONTENT_CACHE["at"] = now
            return seasons or (_CONTENT_CACHE["seasons"] or [])
        except Exception:
            return _CONTENT_CACHE["seasons"] or []

    def season_id(self) -> str | None:
        for s in self._seasons():
            if s.get("IsActive") and s.get("Type") == "act":
                return s["ID"]
        return None

    def prev_season_id(self) -> str | None:
        seasons = self._seasons()
        current = next((s for s in seasons if s.get("IsActive") and s.get("Type") == "act"), None)
        if not current:
            return None
        for s in seasons:
            if s.get("Type") == "act" and s.get("EndTime") == current.get("StartTime"):
                return s["ID"]
        return None

    def _fresh_mids(self, puuid):
        """Newest match ids for a player — first non-empty of competitive,
        unrated, swiftplay, then anything. Cheap history calls only, cached
        for _MIDS_TTL; returns (mids, is_comp, throttled)."""
        hit = _MIDS_CACHE.get(puuid)
        if hit and time.time() - hit[2] < _MIDS_TTL:
            return hit[0], hit[1], False
        mids: list[str] = []
        is_comp = False
        throttled = False
        for queue in ("competitive", "unrated", "swiftplay", ""):
            q = f"&queue={queue}" if queue else ""
            hist = self.auth.pd_get(
                f"/match-history/v1/history/{puuid}?startIndex=0&endIndex=10{q}",
                retries=3)
            throttled = throttled or _is_throttled(hist)
            entries = (hist or {}).get("History", []) if isinstance(hist, dict) else []
            mids = [e["MatchID"] for e in entries if e.get("MatchID")]
            if mids or throttled:
                is_comp = bool(mids) and queue == "competitive"
                break
        if not throttled:
            _cache_put(_MIDS_CACHE, _KD_CACHE_MAX, puuid, (mids, is_comp, time.time()))
        return mids, is_comp, throttled

    def rank_info(self, puuid, season, prev_season=None):
        out = {"tier": 0, "rr": 0, "lb": 0, "peak": 0, "wr": 0, "games": 0,
               "prev": 0, "peak_season": season, "ok": False}
        try:
            # rank/RR/peak/wr only move via comp matches — reuse the cached
            # result until a new comp match shows up in history
            mids, is_comp, _ = self._fresh_mids(puuid)
            rank_key = mids[0] if (mids and is_comp) else "nocomp"
            hit = _RANK_CACHE.get(puuid)
            if hit and hit[1] == rank_key:
                return hit[0]
            if riot_client.held_secs("/mmr/") > 0:
                return out
            r = self.auth.pd_get(f"/mmr/v1/players/{puuid}")
            if not isinstance(r, dict) or "QueueSkills" not in r:
                return out
            out["ok"] = True
            si = (((r.get("QueueSkills") or {}).get("competitive") or {})
                  .get("SeasonalInfoBySeasonID")) or {}
            cur = si.get(season, {}) if season else {}
            out["tier"] = cur.get("CompetitiveTier", 0) or 0
            out["rr"] = cur.get("RankedRating", 0) or 0
            out["lb"] = cur.get("LeaderboardRank", 0) or 0

            if prev_season:
                out["prev"] = (si.get(prev_season, {}) or {}).get("CompetitiveTier", 0) or 0
            peak = out["tier"]
            for s, info in si.items():
                for t in (info.get("WinsByTier") or {}):
                    ti = int(t)
                    if s in BEFORE_ASCENDANT and ti > 20:
                        ti += 3
                    if ti > peak:
                        peak = ti
                        out["peak_season"] = s
            out["peak"] = peak
            wins = cur.get("NumberOfWinsWithPlacements", 0) or 0
            games = cur.get("NumberOfGames", 0) or 0
            out["games"] = games
            out["wr"] = round(wins / games * 100) if games else 0
            _cache_put(_RANK_CACHE, _KD_CACHE_MAX, puuid, (out, rank_key))
        except Exception:
            pass
        return out

    def act_episode(self, season_id):
        pass
        if not season_id:
            return None
        label = valapi.season_label(season_id)
        if label:
            return label

        seasons = self._seasons()
        act = ep = None
        for s in seasons:
            if (s.get("Type") or "").lower() == "episode":
                ep = s
            if s.get("ID", "").lower() == season_id.lower():
                act = s
                break
        if not act:
            return None
        num = valapi._act_number(act.get("Name"))
        ep_label = valapi._episode_label((ep or {}).get("Name"))
        if ep_label and num is not None:
            return f"{ep_label} Act {num}"
        if num is not None:
            return f"Act {num}"
        return (act.get("Name") or "").title() or None

    def level_from_history(self, puuid: str) -> int:
        pass
        if puuid in _LEVEL_CACHE:
            return _LEVEL_CACHE[puuid]
        level = 0
        try:
            hist = self.auth.pd_get(
                f"/match-history/v1/history/{puuid}?startIndex=0&endIndex=1")
            entries = (hist or {}).get("History", []) if isinstance(hist, dict) else []
            mid = entries[0].get("MatchID") if entries else None
            if mid:
                md = self.auth.pd_get(f"/match-details/v1/matches/{mid}")
                pl = next((x for x in (md.get("players") or [])
                           if x.get("subject") == puuid), None)
                level = int((pl or {}).get("accountLevel", 0) or 0)
        except Exception:
            level = 0
        if level > 0:
            _LEVEL_CACHE[puuid] = level
        return level

    def kd_hs(self, puuid, count=3):
        pass
        try:
            rr_earned = None

            # comp match ids come from match-history (generous rate bucket),
            # NOT competitiveupdates (/mmr/ bucket is ~30 req/60s account-wide)
            mids_all, _, throttled = self._fresh_mids(puuid)
            mids = mids_all[:count]
            if not mids:
                return None, None, rr_earned, ("throttled" if throttled else "empty"), None
            cached = _KD_CACHE.get(puuid)
            if (cached and cached[2] >= count
                    and list(cached[1])[:count] == mids):
                return cached[0]

            def fetch_detail(mid):
                hit = _MATCH_DETAIL_CACHE.get(mid)
                if hit is not None:
                    return hit
                md = self.auth.pd_get(f"/match-details/v1/matches/{mid}", retries=3)
                if _is_throttled(md):
                    return "throttled"
                if isinstance(md, dict) and "players" in md:
                    _cache_put(_MATCH_DETAIL_CACHE, _MATCH_DETAIL_MAX, mid, md)
                    return md
                return None

            kills = deaths = hits = heads = used = 0
            agent_counts: dict[str, int] = {}
            form: list[str] = []
            map_wins: dict[str, list] = {}

            with ThreadPoolExecutor(max_workers=min(3, len(mids))) as ex:
                details = list(ex.map(fetch_detail, mids))
            for md in details:
                if md == "throttled":
                    throttled = True
                    continue
                if not md:
                    continue
                for rr in md.get("roundResults", []):
                    for ps in rr.get("playerStats", []):
                        if ps.get("subject") == puuid:
                            for dmg in ps.get("damage", []):
                                hits += dmg.get("legshots", 0) + dmg.get("bodyshots", 0) + dmg.get("headshots", 0)
                                heads += dmg.get("headshots", 0)
                for pl in md.get("players", []):
                    if pl.get("subject") == puuid:
                        st = pl.get("stats", {})
                        kills += st.get("kills", 0)
                        deaths += st.get("deaths", 0)
                        used += 1

                        aname = UUID_TO_NAME.get((pl.get("characterId") or "").lower())
                        if aname:
                            agent_counts[aname] = agent_counts.get(aname, 0) + 1
                        teams = {t.get("teamId"): t for t in md.get("teams", [])}
                        won = (teams.get(pl.get("teamId")) or {}).get("won")
                        if won is not None:
                            form.append("W" if won else "L")
                            mapn = map_name_from_path(
                                (md.get("matchInfo", {}) or {}).get("mapId", ""))
                            mw = map_wins.setdefault(mapn, [0, 0])
                            mw[1] += 1
                            if won:
                                mw[0] += 1
                        break
            if used == 0:

                return None, None, rr_earned, ("throttled" if throttled else "empty"), None
            kd = round(kills / deaths, 2) if deaths else float(kills)
            hs = round(heads / hits * 100) if hits else None
            intel = {
                "topAgents": [{"agent": a, "games": n} for a, n in
                              sorted(agent_counts.items(), key=lambda x: -x[1])[:3]],
                "form": form,
                "streak": form_streak(form),
                "mapWins": map_wins,
            }
            result = (kd, hs, rr_earned, "ok", intel)
            if not throttled and used == len(mids):
                _cache_put(_KD_CACHE, _KD_CACHE_MAX, puuid, (result, tuple(mids), count))
            return result
        except Exception:
            return None, None, None, "error", None

    def _spawn_kd_fill(self, match_id, puuids, season, prev_season) -> None:
        pass
        with _KD_FILL_LOCK:
            if match_id in _KD_FILLING:
                return
            _KD_FILLING.add(match_id)

        def _run():
            try:
                def _fill_one(puuid):
                    cache_key = f"{match_id}:{puuid}"
                    entry = _CACHE.get(cache_key)
                    if entry is None or entry.get("kd_done"):
                        return
                    entry["kd_tries"] = entry.get("kd_tries", 0) + 1
                    kd, hs, _, status, intel = self.kd_hs(puuid, count=3)
                    if kd is None:
                        _log(f"kd-fill {puuid[:8]} status={status} "
                             f"tries={entry['kd_tries']}")
                    if kd is not None:
                        entry["kd"], entry["hs"] = kd, hs
                        entry["intel"] = intel
                        entry["kd_done"] = True
                    elif status == "empty":

                        entry["kd_done"] = True
                    elif status == "throttled":

                        pass
                    elif entry["kd_tries"] >= 6:

                        entry["kd_done"] = True

                def _top_up(puuid):
                    # phase 2: last-match RR (one /mmr/ call, auto-paced by the
                    # mmr bucket) + matches 4-5; row upgrades on the next tick
                    entry = _CACHE.get(f"{match_id}:{puuid}")
                    if entry is None or entry.get("kd_full") or entry.get("kd") is None:
                        return
                    entry["kd_full"] = True
                    mids, is_comp, _ = self._fresh_mids(puuid)
                    rr_key = mids[0] if (mids and is_comp) else "nocomp"
                    hit = _RR_CACHE.get(puuid)
                    if hit and hit[1] == rr_key:
                        entry["rr_earned"] = hit[0]
                    else:
                        cu = self.auth.pd_get(
                            f"/mmr/v1/players/{puuid}/competitiveupdates"
                            f"?startIndex=0&endIndex=1&queue=competitive", retries=1)
                        m = cu.get("Matches", []) if isinstance(cu, dict) else []
                        if m:
                            entry["rr_earned"] = m[0].get("RankedRatingEarned")
                        if isinstance(cu, dict) and not _is_throttled(cu):
                            _cache_put(_RR_CACHE, _KD_CACHE_MAX, puuid,
                                       (entry.get("rr_earned"), rr_key))
                    kd, hs, _, status, intel = self.kd_hs(puuid, count=5)
                    if kd is not None:
                        entry["kd"], entry["hs"] = kd, hs
                        entry["intel"] = intel

                # phase 1: 3 comp matches per player -> every row fills fast
                with ThreadPoolExecutor(max_workers=8) as ex:
                    list(ex.map(_fill_one, puuids))
                # phase 2: top rows up to 5-match stats in the background
                with ThreadPoolExecutor(max_workers=4) as ex:
                    list(ex.map(_top_up, puuids))
            finally:
                with _KD_FILL_LOCK:
                    _KD_FILLING.discard(match_id)

        threading.Thread(target=_run, daemon=True,
                         name=f"kd-fill-{match_id[:8]}").start()

    def build_scoreboard(self, include_stats=True) -> dict:
        presences = self._presences()
        state = self.game_state(presences)

        if state == "MENUS":

            _LAST_BOARD["board"] = None

            board = dict(self.build_lobby(presences, include_stats=include_stats))
            board["queue"] = self.queue_status()
            return board
        if state not in ("INGAME", "PREGAME"):
            held = self._held_board()
            return held or {"state": state, "stateLabel": STATES.get(state, state),
                            "source": "local", "players": [], "teams": {}, "parties": []}

        current = self._current_players(state)
        if not current:

            held = self._held_board()
            return held or {"state": "MENUS", "stateLabel": STATES["MENUS"],
                            "source": "local", "players": [], "teams": {}, "parties": []}

        raw_players, match_id, map_id, queue = current
        puuids = [p["Subject"] for p in raw_players]

        if match_id not in _MATCH_META:
            _MATCH_META.clear()
            _MATCH_META[match_id] = {}
        meta = _MATCH_META[match_id]

        names = meta.get("names") or {}
        missing_names = [p for p in puuids if p not in names]
        if missing_names and meta.get("name_tries", 0) < 8:
            meta["name_tries"] = meta.get("name_tries", 0) + 1
            names = {**names, **self.reveal_names(missing_names)}
            meta["names"] = names
        if not meta.get("loadouts"):
            ld = self.loadouts(state, match_id)
            if ld:
                meta["loadouts"] = ld
            weapons_by_puuid = ld
        else:
            weapons_by_puuid = meta["loadouts"]

        pmap = self.party_map(puuids, presences)
        party_lookup = {}
        parties_out = []
        for idx, (pid, members) in enumerate(pmap.items()):
            color = party_color(idx)
            parties_out.append({"id": pid, "color": color, "number": idx + 1,
                                "size": len(members), "members": members})
            for m in members:
                party_lookup[m] = {"id": pid, "color": color, "number": idx + 1}

        season = self.season_id()
        prev_season = self.prev_season_id()
        self_team = next((p["TeamID"] for p in raw_players
                          if p["Subject"] == self.self_puuid), "Blue")

        uncached_kd: list[str] = []

        def fetch_player(p):
            puuid = p["Subject"]
            ident = p.get("PlayerIdentity", {}) or {}
            cache_key = f"{match_id}:{puuid}"
            cached = _CACHE.get(cache_key)
            if cached is None:
                rk = self.rank_info(puuid, season, prev_season)

                # kd_done=False even for include_stats=False callers (discord
                # presence): a stats-less build racing the main loop must not
                # permanently mark players done — the next stats tick queues them
                cached = {"rk": rk, "prev": rk.get("prev", 0),
                          "kd": None, "hs": None, "rr_earned": None,
                          "kd_done": False}
                if not rk.get("ok"):

                    cached["rank_at"] = time.time()
                _CACHE[cache_key] = cached
                if include_stats:
                    uncached_kd.append(puuid)
            else:
                if (not cached["rk"].get("ok")
                        and time.time() - cached.get("rank_at", 0.0) > 20.0):

                    rk = self.rank_info(puuid, season, prev_season)
                    if rk.get("ok"):
                        cached["rk"], cached["prev"] = rk, rk.get("prev", 0)
                    else:
                        cached["rank_at"] = time.time()
                if include_stats and not cached.get("kd_done"):
                    uncached_kd.append(puuid)
            name, level, level_hidden = self.resolve_identity(puuid, names, ident)

            if (level or 0) <= 0:
                recovered = self.level_from_history(puuid)
                if recovered > 0:
                    level = recovered
            return puuid, cached, name, level, level_hidden

        with ThreadPoolExecutor(max_workers=min(6, len(raw_players) or 1)) as ex:
            resolved = {r[0]: r[1:] for r in ex.map(fetch_player, raw_players)}

        if uncached_kd:
            self._spawn_kd_fill(match_id, uncached_kd, season, prev_season)

        players = []
        for p in raw_players:
            puuid = p["Subject"]
            ident = p.get("PlayerIdentity", {}) or {}
            cached, name, level, level_hidden = resolved[puuid]
            if name == _fallback_name(puuid):

                agent_meta = resolve_agent(p.get("CharacterID", "") or "") or {}
                if state != "PREGAME" and agent_meta.get("name"):
                    name = agent_meta["name"]
                else:
                    name = f"Player {len(players) + 1}"
            rk = cached["rk"]
            weapons = weapons_by_puuid.get(puuid.lower(), [])
            vandal = next((w["skin"] for w in weapons
                           if w["weapon"] == "Vandal" and w.get("skin")), None)
            smurf, smurf_reasons = compute_smurf(
                level=level, peak_tier=rk["peak"], rank_tier=rk["tier"],
                kd=cached["kd"], win_rate=rk["wr"], games=rk["games"])
            players.append(assemble_player(
                puuid=puuid,
                name=name,
                name_hidden=ident.get("Incognito", False),
                team=p.get("TeamID", "Blue"),
                is_self=(puuid == self.self_puuid),
                agent_id=p.get("CharacterID", ""),
                selection=p.get("CharacterSelectionState") if state == "PREGAME" else None,
                rank_tier=rk["tier"], rr=rk["rr"], leaderboard=rk["lb"],
                peak_tier=rk["peak"], prev_tier=cached["prev"],
                win_rate=rk["wr"], games=rk["games"],
                kd=cached["kd"], hs=cached["hs"],
                level=level,
                level_hidden=level_hidden,
                party=party_lookup.get(puuid),
                skin=vandal,
                weapons=weapons,
                peak_act=self.act_episode(rk.get("peak_season")),
                rr_earned=cached.get("rr_earned"),
                intel=cached.get("intel"),
                player_card=valapi.player_card(ident.get("PlayerCardID")),
                title=valapi.title_text(ident.get("PlayerTitleID")),
                smurf=smurf, smurf_reasons=smurf_reasons,
            ))

        map_name = map_name_from_path(map_id)
        score = self.match_score(presences) if state == "INGAME" else None
        if score and (queue or "").lower() in ("deathmatch", "hurm"):
            score["round"] = None
        board = finalize(players, state=state, source="local", self_team=self_team,
                         map_name=map_name, queue=queue, match_id=match_id,
                         parties=parties_out, map_splash=valapi.map_splash(map_name),
                         score=score)
        board["riotRequests"] = self.auth.req_count

        _LAST_BOARD["board"] = board
        _LAST_BOARD["at"] = time.time()
        return board

    def _held_board(self):
        pass
        b = _LAST_BOARD.get("board")
        if b and (time.time() - _LAST_BOARD.get("at", 0.0)) < _HOLD_SECS:
            return b
        return None

    def queue_status(self) -> dict:
        pass
        now = time.time()
        if _QUEUE_CACHE["data"] is not None and now - _QUEUE_CACHE["at"] < 3.0:
            return _QUEUE_CACHE["data"]
        from riot_client import party_snapshot
        try:
            snap = party_snapshot(self.auth)
        except Exception:
            snap = {"available": False}
        if snap.get("throttled") and _QUEUE_CACHE["data"]:
            return _QUEUE_CACHE["data"]
        snap.pop("throttled", None)
        _QUEUE_CACHE.update(at=now, data=snap)
        return snap

    def diagnose_reveal(self, max_players=2, max_matches=8) -> dict:
        pass
        presences = self._presences()
        state = self.game_state(presences)
        current = self._current_players(state)
        if not current:
            return {"state": state, "error": "Not in a pre-game/in-game match."}
        raw_players, _, _, _ = current
        puuids = [p["Subject"] for p in raw_players]
        names = self.reveal_names(puuids)

        targets = [p["Subject"] for p in raw_players
                   if (p.get("PlayerIdentity", {}) or {}).get("Incognito")
                   and p["Subject"] != self.self_puuid]

        report = []
        for puuid in targets[:max_players]:
            entry = {"puuid": puuid[:8], "nameService": names.get(puuid), "matches": []}
            try:
                hist = self.auth.pd_get(
                    f"/match-history/v1/history/{puuid}?startIndex=0&endIndex={max_matches}")
                for m in (hist.get("History") or [])[:max_matches]:
                    mid = m.get("MatchID")
                    if not mid:
                        continue
                    md = self.auth.pd_get(f"/match-details/v1/matches/{mid}")
                    pl = next((x for x in (md.get("players") or [])
                               if x.get("subject") == puuid), None)
                    gn = (pl or {}).get("gameName") or ""
                    entry["matches"].append({
                        "queue": m.get("QueueID") or "?",
                        "namePresent": bool(gn.strip()),
                        "name": (f"{gn}#{(pl or {}).get('tagLine', '')}" if gn.strip() else None),
                        "level": (pl or {}).get("accountLevel"),
                    })
            except Exception as e:
                entry["error"] = str(e)
            entry["nameEverPresent"] = any(x["namePresent"] for x in entry["matches"])
            report.append(entry)

        if not targets:
            verdict = "no Incognito players in this match to test"
        elif any(e["nameEverPresent"] for e in report):
            verdict = "baked per-match — deeper history search CAN reveal names"
        else:
            verdict = ("dynamic on current status — match history canNOT reveal names; "
                       "account-v1 (RIOT_API_KEY) is the only path")
        return {"state": state, "incognitoCount": len(targets),
                "verdict": verdict, "report": report}

    def build_lobby(self, presences, include_stats=False) -> dict:
        pass
        members = self.party_members(presences)
        puuids = [m["puuid"] for m in members]

        key = tuple(sorted(puuids))
        now = time.time()
        if (_LOBBY_CACHE["board"] is not None and _LOBBY_CACHE["key"] == key
                and now - _LOBBY_CACHE["at"] < 20):
            return _LOBBY_CACHE["board"]

        names = self.reveal_names(puuids)

        season = self.season_id()
        prev_season = self.prev_season_id()
        multi = len(members) > 1
        party = {"id": "lobby", "color": party_color(0), "number": 1,
                 "size": len(members)} if multi else None

        def fetch_member(m):
            puuid = m["puuid"]
            rk = self.rank_info(puuid, season, prev_season)
            kd = hs = intel = None
            if include_stats:

                kd, hs, _, _, intel = self.kd_hs(puuid)

            level = m.get("level", 0) or 0
            if level <= 0:
                level = self.level_from_history(puuid)
            return m, rk, kd, hs, level, intel

        with ThreadPoolExecutor(max_workers=min(6, len(members) or 1)) as ex:
            fetched = list(ex.map(fetch_member, members))

        players = []
        for m, rk, kd, hs, lvl, intel in fetched:
            puuid = m["puuid"]
            ident = {"AccountLevel": lvl, "HideAccountLevel": False,
                     "Incognito": m.get("incognito", False)}
            name, level, level_hidden = self.resolve_identity(puuid, names, ident)
            smurf, smurf_reasons = compute_smurf(
                level=level, peak_tier=rk["peak"], rank_tier=rk["tier"],
                kd=kd, win_rate=rk["wr"], games=rk["games"])
            players.append(assemble_player(
                puuid=puuid, name=name, name_hidden=False, team="Blue",
                is_self=(puuid == self.self_puuid), agent_id="",
                rank_tier=rk["tier"], rr=rk["rr"], leaderboard=rk["lb"],
                peak_tier=rk["peak"], prev_tier=rk.get("prev", 0),
                win_rate=rk["wr"], games=rk["games"], kd=kd, hs=hs,
                intel=intel,
                level=level, level_hidden=level_hidden,
                party=party, peak_act=self.act_episode(rk.get("peak_season")),
                smurf=smurf, smurf_reasons=smurf_reasons,
            ))

        parties_out = [{**party, "members": puuids}] if party else []
        board = finalize(players, state="MENUS", source="local", self_team="Blue",
                         map_name=None, queue="Lobby", match_id="lobby",
                         parties=parties_out)
        board["riotRequests"] = self.auth.req_count
        _LOBBY_CACHE.update(key=key, at=now, board=board)
        return board

    def player_career(self, puuid: str, count: int = 8) -> dict:
        pass
        try:
            hist = self.auth.pd_get(
                f"/match-history/v1/history/{puuid}?startIndex=0&endIndex={count}")
            entries = hist.get("History", []) or [] if isinstance(hist, dict) else []
        except Exception:
            entries = []
        mids = [h["MatchID"] for h in entries if h.get("MatchID")]

        def fetch_detail(mid):
            try:
                return self._career_match(
                    self.auth.pd_get(f"/match-details/v1/matches/{mid}"), puuid, mid)
            except Exception:
                return None

        matches, mate_puuids = [], set()
        if mids:
            with ThreadPoolExecutor(max_workers=min(8, len(mids))) as ex:
                for row in ex.map(fetch_detail, mids):
                    if row:
                        matches.append(row)
                        mate_puuids.update(m["puuid"] for m in row["teammates"])

        names = self.reveal_names(list(mate_puuids)) if mate_puuids else {}
        for row in matches:
            for mate in row["teammates"]:
                mate["name"] = names.get(mate["puuid"]) or _fallback_name(mate["puuid"])

        return {"source": "local", "puuid": puuid, "matches": matches,
                **_career_summary(matches)}

    def _career_match(self, md: dict, puuid: str, mid: str = "") -> dict | None:
        info = md.get("matchInfo", {}) or {}
        players = md.get("players", []) or []
        subj = next((p for p in players if p.get("subject") == puuid), None)
        if not subj:
            return None

        st = subj.get("stats", {}) or {}
        team_id = subj.get("teamId")
        teams = {t.get("teamId"): t for t in md.get("teams", []) if t.get("teamId")}
        mine = teams.get(team_id, {})
        won = mine.get("won")
        rounds = max((t.get("roundsWon", 0) for t in teams.values()), default=0) +            min((t.get("roundsWon", 0) for t in teams.values()), default=0)

        hits = heads = 0
        for rr in md.get("roundResults", []):
            for ps in rr.get("playerStats", []):
                if ps.get("subject") == puuid:
                    for dmg in ps.get("damage", []):
                        hits += dmg.get("legshots", 0) + dmg.get("bodyshots", 0) + dmg.get("headshots", 0)
                        heads += dmg.get("headshots", 0)

        kills, deaths = st.get("kills", 0), st.get("deaths", 0)
        agent = resolve_agent((subj.get("characterId") or "")) or {}
        teammates = [
            {"puuid": p.get("subject"),
             "agent": (resolve_agent(p.get("characterId") or "") or {}).get("name", "Unknown")}
            for p in players
            if p.get("teamId") == team_id and p.get("subject") != puuid
        ]
        queue = info.get("queueID") or info.get("queueId") or ""
        return {
            "matchId": mid or info.get("matchId", ""),
            "map": map_name_from_path(info.get("mapId", "")),
            "mode": _mode_label(queue),
            "startMillis": info.get("gameStartMillis", 0),
            "result": "Victory" if won is True else "Defeat" if won is False else "Draw",
            "agent": agent.get("name", "Unknown"),
            "agentPortrait": agent.get("portrait"),
            "agentColor": agent.get("color", "#8B978F"),
            "kills": kills,
            "deaths": deaths,
            "assists": st.get("assists", 0),
            "kd": round(kills / deaths, 2) if deaths else float(kills),
            "acs": round(st.get("score", 0) / rounds) if rounds else 0,
            "hsPct": round(heads / hits * 100) if hits else None,
            "teammates": teammates,
        }

    def match_detail(self, match_id: str, subject: str | None = None) -> dict:
        pass
        md = self.auth.pd_get(f"/match-details/v1/matches/{match_id}")
        if not isinstance(md, dict) or "players" not in md:
            return {"error": "Match details unavailable."}
        info = md.get("matchInfo", {}) or {}
        teams = {t.get("teamId"): t for t in md.get("teams", []) if t.get("teamId")}
        rounds = sum(t.get("roundsWon", 0) for t in teams.values())            or len(md.get("roundResults", [])) or 1

        hits: dict = {}
        heads: dict = {}
        for rr in md.get("roundResults", []):
            for ps in rr.get("playerStats", []):
                s = ps.get("subject")
                for dmg in ps.get("damage", []):
                    hits[s] = hits.get(s, 0) + dmg.get("legshots", 0) +                        dmg.get("bodyshots", 0) + dmg.get("headshots", 0)
                    heads[s] = heads.get(s, 0) + dmg.get("headshots", 0)

        raw = md.get("players", []) or []
        names = self.reveal_names([p.get("subject") for p in raw])
        players = []
        for p in raw:
            sub = p.get("subject")
            st = p.get("stats", {}) or {}
            agent = resolve_agent(p.get("characterId") or "") or {}
            k, d, a = st.get("kills", 0), st.get("deaths", 0), st.get("assists", 0)
            th = hits.get(sub, 0)
            stored = (f"{p.get('gameName')}#{p.get('tagLine')}"
                      if p.get("gameName") else None)
            players.append({
                "puuid": sub,
                "name": names.get(sub) or stored or _fallback_name(sub),
                "team": p.get("teamId"),
                "agent": agent.get("name", "Unknown"),
                "agentPortrait": agent.get("portrait"),
                "agentColor": agent.get("color", "#8B978F"),
                "kills": k, "deaths": d, "assists": a,
                "kd": round(k / d, 2) if d else float(k),
                "acs": round(st.get("score", 0) / rounds) if rounds else 0,
                "hsPct": round(heads.get(sub, 0) / th * 100) if th else None,
                "isSubject": sub == subject,
            })
        players.sort(key=lambda x: -x["acs"])

        won = None
        if subject:
            sp = next((p for p in raw if p.get("subject") == subject), None)
            if sp:
                won = teams.get(sp.get("teamId"), {}).get("won")
        return {
            "matchId": match_id,
            "map": map_name_from_path(info.get("mapId", "")),
            "mode": _mode_label(info.get("queueID") or info.get("queueId") or ""),
            "scores": {tid: t.get("roundsWon", 0) for tid, t in teams.items()},
            "result": ("Victory" if won is True else "Defeat" if won is False
                       else ("Draw" if won is not None else None)),
            "players": players,
        }

def _career_summary(matches: list) -> dict:
    pass
    n = len(matches)
    if not n:
        return {"averages": {"games": 0, "wins": 0, "winRate": 0, "kd": 0,
                             "kills": 0, "deaths": 0, "assists": 0, "hsPct": 0},
                "coPlayers": [], "agentPool": [], "mapStats": []}
    wins = sum(1 for m in matches if m["result"] == "Victory")
    k = sum(m["kills"] for m in matches)
    d = sum(m["deaths"] for m in matches)
    a = sum(m["assists"] for m in matches)
    hs = [m["hsPct"] for m in matches if m.get("hsPct") is not None]

    seen: dict[str, dict] = {}
    for m in matches:
        for mate in m["teammates"]:
            pid = mate.get("puuid")
            if not pid:
                continue
            e = seen.setdefault(pid, {"puuid": pid, "name": mate.get("name"),
                                      "sharedMatches": 0, "agents": set()})
            e["sharedMatches"] += 1
            e["name"] = mate.get("name") or e["name"]
            if mate.get("agent"):
                e["agents"].add(mate["agent"])
    co_players = sorted(
        ({"puuid": e["puuid"], "name": e["name"], "sharedMatches": e["sharedMatches"],
          "agents": sorted(e["agents"]), "isParty": e["sharedMatches"] >= 2}
         for e in seen.values()),
        key=lambda x: x["sharedMatches"], reverse=True)[:6]

    def _tally(key):
        out: dict[str, list] = {}
        for m in matches:
            name = m.get(key)
            if not name or name == "Unknown":
                continue
            t = out.setdefault(name, [0, 0])
            t[1] += 1
            if m["result"] == "Victory":
                t[0] += 1
        return out

    agent_pool = [
        {"agent": ag, "games": g, "winRate": round(100 * w / g),
         "portrait": (resolve_agent(ag) or {}).get("portrait"),
         "color": (resolve_agent(ag) or {}).get("color", "#8B978F")}
        for ag, (w, g) in sorted(_tally("agent").items(), key=lambda x: -x[1][1])
    ][:5]
    map_stats = [
        {"map": mp, "games": g, "wins": w, "winRate": round(100 * w / g)}
        for mp, (w, g) in sorted(_tally("map").items(), key=lambda x: -x[1][1])
    ]

    return {
        "averages": {
            "games": n, "wins": wins, "winRate": round(100 * wins / n),
            "kills": round(k / n, 1), "deaths": round(d / n, 1), "assists": round(a / n, 1),
            "kd": round(k / d, 2) if d else float(k),
            "hsPct": round(sum(hs) / len(hs)) if hs else None,
        },
        "coPlayers": co_players,
        "agentPool": agent_pool,
        "mapStats": map_stats,
    }

def _team_stats(team_players: list) -> dict:
    pass
    ranked = [p["rankTier"] for p in team_players if (p.get("rankTier") or 0) > 0]
    kds = [p["kd"] for p in team_players if p.get("kd") is not None]
    wrs = [p["winRate"] for p in team_players if p.get("winRate") is not None]
    avg_tier = sum(ranked) / len(ranked) if ranked else 0
    rank_meta = rank_from_tier(round(avg_tier)) if ranked else rank_from_tier(0)
    return {
        "avgRankTier": round(avg_tier, 2),
        "avgRank": rank_meta["name"],
        "rankColor": rank_meta["color"],
        "rankIcon": valapi.rank_icon(round(avg_tier)) if ranked else None,
        "avgKd": round(sum(kds) / len(kds), 2) if kds else None,
        "avgWinRate": round(sum(wrs) / len(wrs)) if wrs else None,
        "smurfCount": sum(1 for p in team_players if p.get("smurf")),
        "size": len(team_players),
    }

def _win_prob(self_stats: dict, enemy_stats: dict) -> int:
    pass
    prob = 50.0
    prob += (self_stats["avgRankTier"] - enemy_stats["avgRankTier"]) * 5
    self_kd = self_stats["avgKd"]
    enemy_kd = enemy_stats["avgKd"]
    if self_kd is not None and enemy_kd is not None:
        prob += (self_kd - enemy_kd) * 20
    return max(5, min(95, round(prob)))

def finalize(players, *, state, source, self_team, map_name, queue, match_id,
             parties, map_splash=None, score=None):
    pass

    for p in players:
        mw = p.pop("mapWins", None) or {}
        w, g = (mw.get(map_name) or [0, 0]) if map_name else (0, 0)
        p["mapWinRate"] = {"winRate": round(100 * w / g), "games": g} if g else None
    players.sort(key=lambda x: (x["team"] != self_team, -x["rankTier"], -(x["level"] or 0)))
    teams = {}
    for p in players:
        teams.setdefault(p["team"], []).append(p)

    team_stats = {tid: _team_stats(tp) for tid, tp in teams.items()}

    win_prob = None
    if state == "INGAME" and len(team_stats) == 2 and self_team in team_stats:
        enemy_team = next(t for t in team_stats if t != self_team)
        win_prob = _win_prob(team_stats[self_team], team_stats[enemy_team])

    locked = sum(1 for p in players if p.get("selection") == "locked")

    side = ({"Red": "Attacker", "Blue": "Defender"}.get(self_team)
            if state in ("INGAME", "PREGAME") else None)
    return {
        "state": state,
        "stateLabel": STATES.get(state, state),
        "source": source,
        "map": map_name,
        "mapSplash": map_splash,
        "mode": _mode_label(queue),
        "matchId": match_id,
        "selfTeam": self_team,
        "side": side,
        "players": players,
        "teams": teams,
        "teamStats": team_stats,
        "winProb": win_prob,
        "parties": parties,
        "score": score,
        "lockProgress": {"locked": locked, "total": len(players)} if state == "PREGAME" else None,
    }
