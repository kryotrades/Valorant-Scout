"""
riot_client.py
==============
Unified VALORANT data client with three layered data sources and graceful
degradation between them:

  1. LOCAL   - the unofficial local client API. Instalock calls the pregame
               select/lock endpoints; the raw lockfile + entitlement auth lives
               in LocalAuth below. Requires VALORANT to be running on this
               machine.
  2. OFFICIAL- the official Riot API (https://*.api.riotgames.com) using
               RIOT_API_KEY. account-v1 is generally available; val-match-v1
               requires production approval, so it is attempted and degrades
               cleanly if Riot returns 403.
  3. DEMO    - deterministic generated career (sample_data) so the dashboard is
               always fully populated.

`get_player_overview(puuid)` returns a normalized raw career; app.py then runs
party_detector + pick_advisor on top of it regardless of source.
"""

from __future__ import annotations

import base64
import os
import threading
import time
from datetime import datetime, timezone

import requests
import urllib3

import sample_data
from agents import UUID_TO_NAME, resolve_agent
from vconstants import GAMEMODES, map_name_from_path, rank_from_tier

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Current client version, cached process-wide (see LocalAuth._client_version).
_CLIENT_VERSION: str | None = None

# Account-v1 routing cluster per shard.
_ROUTING = {
    "na": "americas", "latam": "americas", "br": "americas",
    "eu": "europe", "ap": "asia", "kr": "asia",
}

# Region override -> (pd shard, glz_a, glz_b). The local client API URLs differ
# per region; we normally auto-detect from ShooterGame.log, but the UI can pin a
# region (LATAM/BR ride the NA glz cluster).
REGION_MAP = {
    "na":    ("na",    "na-1", "na"),
    "eu":    ("eu",    "eu-1", "eu"),
    "ap":    ("ap",    "ap-1", "ap"),
    "kr":    ("kr",    "kr-1", "kr"),
    "latam": ("latam", "na-1", "latam"),
    "br":    ("br",    "na-1", "br"),
}
REGIONS = ["na", "eu", "ap", "kr", "latam", "br"]

# Riot queue ids -> friendly mode.
_QUEUE_NAMES = {
    "competitive": "Competitive", "unrated": "Unrated", "swiftplay": "Swiftplay",
    "spikerush": "Spike Rush", "deathmatch": "Deathmatch", "ggteam": "Escalation",
    "hurm": "Team Deathmatch", "": "Custom",
}


def _log(msg: str) -> None:
    print(f"[riot_client] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Riot-edge request throttle
# ---------------------------------------------------------------------------
# Building the live board can otherwise fire ~60 pd/glz requests in a burst
# (ranks for 10 players + the background K/D fill, which pulls several match
# details each), and Riot answers the burst with 429s. A token bucket smooths
# this ACROSS ALL THREADS: a short burst is allowed (so the first board's rank
# fetches stay snappy) and sustained load is then paced, so we never fire all
# ~60 at once. Tunable via RIOT_MAX_RPS (requests/sec; 0 disables throttling).
_RIOT_RATE_LOCK = threading.Lock()
try:
    _RIOT_MAX_RPS = max(0.0, float(os.getenv("RIOT_MAX_RPS", "20")))
except ValueError:
    _RIOT_MAX_RPS = 20.0
_RIOT_BURST = _RIOT_MAX_RPS if _RIOT_MAX_RPS > 0 else 1.0   # ~1s worth of burst
_RIOT_BUCKET = {"tokens": _RIOT_BURST, "at": 0.0}


def _riot_throttle() -> None:
    """Token-bucket gate: returns immediately while burst tokens remain, then
    paces requests to ~RIOT_MAX_RPS, globally across threads."""
    if _RIOT_MAX_RPS <= 0:
        return
    with _RIOT_RATE_LOCK:
        now = time.time()
        if _RIOT_BUCKET["at"] == 0.0:
            _RIOT_BUCKET["at"] = now
        _RIOT_BUCKET["tokens"] = min(
            _RIOT_BURST,
            _RIOT_BUCKET["tokens"] + (now - _RIOT_BUCKET["at"]) * _RIOT_MAX_RPS)
        _RIOT_BUCKET["at"] = now
        if _RIOT_BUCKET["tokens"] < 1.0:
            time.sleep((1.0 - _RIOT_BUCKET["tokens"]) / _RIOT_MAX_RPS)
            _RIOT_BUCKET["tokens"] = 0.0
            _RIOT_BUCKET["at"] = time.time()
        else:
            _RIOT_BUCKET["tokens"] -= 1.0


# ---------------------------------------------------------------------------
# Local auth
# ---------------------------------------------------------------------------
class LocalAuth:
    """
    Reads the Riot lockfile + ShooterGame.log to authenticate against the local
    client and the pd/glz edge servers.

    Only usable on a machine where VALORANT is currently running.
    """

    def __init__(self, region: str | None = None):
        self.lockfile = self._get_lockfile()
        # Pin region from the UI/env if given & known, else auto-detect from logs.
        region = (region or "").strip().lower()
        if region in REGION_MAP:
            shard, ga, gb = REGION_MAP[region]
            self.region = [shard, [ga, gb]]
        else:
            self.region = self._get_region()          # (pd_shard, [glz_a, glz_b])
        self.pd_url = f"https://pd.{self.region[0]}.a.pvp.net"
        self.glz_url = f"https://glz-{self.region[1][0]}.{self.region[1][1]}.a.pvp.net"
        self.shard = self.region[0]
        self._headers: dict | None = None
        self.puuid = ""
        self.req_count = 0          # pd/glz (Riot edge) requests this instance

    @staticmethod
    def available() -> bool:
        path = os.path.join(os.getenv("LOCALAPPDATA", ""),
                            r"Riot Games\Riot Client\Config\lockfile")
        return os.path.isfile(path)

    def _get_lockfile(self) -> dict:
        path = os.path.join(os.getenv("LOCALAPPDATA", ""),
                            r"Riot Games\Riot Client\Config\lockfile")
        with open(path) as f:
            keys = ["name", "PID", "port", "password", "protocol"]
            return dict(zip(keys, f.read().split(":")))

    def _get_region(self):
        path = os.path.join(os.getenv("LOCALAPPDATA", ""),
                            r"VALORANT\Saved\Logs\ShooterGame.log")
        pd_url = glz_url = None
        with open(path, "r", encoding="utf8") as f:
            for line in f:
                if ".a.pvp.net/account-xp/v1/" in line:
                    pd_url = line.split(".a.pvp.net/account-xp/v1/")[0].split(".")[-1]
                elif "https://glz" in line:
                    glz_url = [line.split("https://glz-")[1].split(".")[0],
                               line.split("https://glz-")[1].split(".")[1]]
                if pd_url and glz_url:
                    if pd_url == "pbe":
                        return ["na", ["na-1", "na"]]
                    return [pd_url, glz_url]
        raise RuntimeError("could not parse region from ShooterGame.log")

    def _client_version(self) -> str:
        """
        X-Riot-ClientVersion. Prefer the CURRENT version from valorant-api.com.
        A stale version makes some `pd` endpoints degrade — notably name-service,
        which then redacts Incognito names — so using the current version is what
        keeps hidden names resolvable. Falls back to the log, then a hardcoded
        default.
        """
        global _CLIENT_VERSION
        if _CLIENT_VERSION:
            return _CLIENT_VERSION
        try:
            data = requests.get("https://valorant-api.com/v1/version", timeout=6).json()
            rcv = (data.get("data") or {}).get("riotClientVersion")
            if rcv:
                _CLIENT_VERSION = rcv
                _log(f"client version (valorant-api): {rcv}")
                return rcv
        except Exception as e:  # noqa: BLE001
            _log(f"valorant-api version lookup failed ({e}); using log")
        try:
            path = os.path.join(os.getenv("LOCALAPPDATA", ""),
                                r"VALORANT\Saved\Logs\ShooterGame.log")
            with open(path, "r", encoding="utf8") as f:
                for line in f:
                    if "CI server version:" in line:
                        _CLIENT_VERSION = line.split("CI server version: ")[1].strip()
                        return _CLIENT_VERSION
        except Exception:  # noqa: BLE001
            pass
        return "release-09.00"

    def headers(self, refresh: bool = False) -> dict:
        if self._headers and not refresh:
            return self._headers
        local = {"Authorization": "Basic " + base64.b64encode(
            ("riot:" + self.lockfile["password"]).encode()).decode()}
        ent = requests.get(
            f"https://127.0.0.1:{self.lockfile['port']}/entitlements/v1/token",
            headers=local, verify=False, timeout=5).json()
        self.puuid = ent["subject"]
        self._headers = {
            "Authorization": f"Bearer {ent['accessToken']}",
            "X-Riot-Entitlements-JWT": ent["token"],
            "X-Riot-ClientPlatform": (
                "ew0KCSJwbGF0Zm9ybVR5cGUiOiAiUEMiLA0KCSJwbGF0Zm9ybU9TIjog"
                "IldpbmRvd3MiLA0KCSJwbGF0Zm9ybU9TVmVyc2lvbiI6ICIxMC4wLjE5"
                "MDQyLjEuMjU2LjY0Yml0IiwNCgkicGxhdGZvcm1DaGlwc2V0IjogIlVua25vd24iDQp9"),
            "X-Riot-ClientVersion": self._client_version(),
            "User-Agent": "ShooterGame/13 Windows/10.0.19043.1.256.64bit",
        }
        return self._headers

    def glz_post(self, endpoint: str) -> requests.Response:
        _riot_throttle()
        self.req_count += 1
        return requests.post(self.glz_url + endpoint, headers=self.headers(),
                             verify=False, timeout=8)

    @staticmethod
    def _json(resp):
        """Parse JSON, tolerating throttled/empty responses (429 -> '')."""
        try:
            return resp.json()
        except ValueError:
            if resp.status_code == 429:
                return {"errorCode": "RATE_LIMITED", "status": 429}
            return {}

    def glz_get(self, endpoint: str) -> dict:
        _riot_throttle()
        self.req_count += 1
        return self._json(requests.get(self.glz_url + endpoint, headers=self.headers(),
                                       verify=False, timeout=8))

    def pd_get(self, endpoint: str, refresh: bool = False, retries: int = 0) -> dict:
        """
        GET a pd endpoint. With ``retries`` > 0, a 429 (rate-limited) response is
        retried in-place with escalating backoff instead of being returned as an
        empty sentinel — the way VALORANT-rank-yoinker handles throttling. Use it
        for BACKGROUND work (the K/D fill) so a player's stats resolve even when
        the burst budget is spent; leave it 0 (the default) for the synchronous
        board build so that path always returns promptly.
        """
        backoff = 3.0
        for attempt in range(retries + 1):
            _riot_throttle()
            self.req_count += 1
            data = self._json(requests.get(self.pd_url + endpoint, headers=self.headers(refresh),
                                            verify=False, timeout=8))
            if attempt < retries and isinstance(data, dict) and data.get("status") == 429:
                time.sleep(backoff)
                backoff += 3.0
                continue
            return data
        return data

    def pd_put(self, endpoint: str, payload, refresh: bool = False) -> dict:
        _riot_throttle()
        self.req_count += 1
        return self._json(requests.put(self.pd_url + endpoint, headers=self.headers(refresh),
                                       json=payload, verify=False, timeout=8))

    def local_get(self, endpoint: str) -> dict:
        local = {"Authorization": "Basic " + base64.b64encode(
            ("riot:" + self.lockfile["password"]).encode()).decode()}
        return requests.get(
            f"https://127.0.0.1:{self.lockfile['port']}{endpoint}",
            headers=local, verify=False, timeout=5).json()


# ---------------------------------------------------------------------------
# Main client
# ---------------------------------------------------------------------------
class RiotClient:
    def __init__(self):
        self.api_key = os.getenv("RIOT_API_KEY", "").strip()
        self.region = os.getenv("RIOT_REGION", "na").strip().lower()
        self.source_pref = os.getenv("DATA_SOURCE", "auto").strip().lower()
        self.allow_live_instalock = os.getenv("ALLOW_LIVE_INSTALOCK", "true").lower() == "true"
        self._valclient = None  # lazy

    # -- source selection ---------------------------------------------------
    def get_player_overview(self, puuid: str) -> dict:
        """Return a normalized raw career: identity + rank + matches + source."""
        order = {
            "auto": ["local", "official", "demo"],
            "local": ["local"],
            "official": ["official"],
            "demo": ["demo"],
        }.get(self.source_pref, ["local", "official", "demo"])

        last_err = None
        for src in order:
            try:
                if src == "local" and self._local_ready():
                    data = self._local_overview(puuid)
                    if data and data.get("matches"):
                        return data
                elif src == "official" and self.api_key:
                    data = self._official_overview(puuid)
                    if data and data.get("matches"):
                        return data
                elif src == "demo":
                    return self._demo_overview(puuid)
            except Exception as e:  # noqa: BLE001 - degrade to next source
                last_err = e
                _log(f"source '{src}' failed: {e}")
        # If everything above produced nothing, guarantee a response.
        _log(f"falling back to demo (last error: {last_err})")
        return self._demo_overview(puuid)

    # -- demo ---------------------------------------------------------------
    def _demo_overview(self, puuid: str) -> dict:
        data = sample_data.generate_player(puuid)
        # Enrich with the player's real Riot ID if the official account API works.
        real_id = self._official_riot_id(puuid) if self.api_key else None
        if real_id:
            data["riotId"] = real_id
            data["source"] = "demo"
            data["sourceDetail"] = "Generated matches • Riot ID verified via account-v1"
        else:
            data["sourceDetail"] = "Generated sample career (no live source reachable)"
        return data

    # -- official API -------------------------------------------------------
    def _official_headers(self) -> dict:
        return {"X-Riot-Token": self.api_key}

    def _official_riot_id(self, puuid: str) -> str | None:
        cluster = _ROUTING.get(self.region, "americas")
        try:
            r = requests.get(
                f"https://{cluster}.api.riotgames.com/riot/account/v1/accounts/by-puuid/{puuid}",
                headers=self._official_headers(), timeout=8)
            if r.ok:
                j = r.json()
                return f"{j.get('gameName')}#{j.get('tagLine')}"
        except requests.RequestException as e:
            _log(f"account-v1 lookup failed: {e}")
        return None

    def _official_overview(self, puuid: str) -> dict | None:
        base = f"https://{self.region}.api.riotgames.com"
        ml = requests.get(f"{base}/val/match/v1/matchlists/by-puuid/{puuid}",
                          headers=self._official_headers(), timeout=10)
        if ml.status_code == 403:
            _log("val-match-v1 not authorized on this key (needs production access)")
            return None
        ml.raise_for_status()
        history = ml.json().get("history", [])[:20]
        matches = []
        for h in history:
            md = requests.get(f"{base}/val/match/v1/matches/{h['matchId']}",
                              headers=self._official_headers(), timeout=10)
            if not md.ok:
                continue
            norm = self._normalize_match(md.json(), puuid)
            if norm:
                matches.append(norm)
        if not matches:
            return None
        tier = self._latest_tier(matches)
        return {
            "puuid": puuid,
            "riotId": self._official_riot_id(puuid) or "Player#NA1",
            "rankTier": tier,
            "rr": 0,
            "peakTier": tier,
            "matches": matches,
            "source": "official",
            "sourceDetail": "Official Riot API (val-match-v1)",
        }

    # -- local client (valclient) ------------------------------------------
    def _local_ready(self) -> bool:
        if self.source_pref not in ("auto", "local"):
            return False
        return LocalAuth.available()

    def _get_valclient(self):
        if self._valclient is not None:
            return self._valclient
        try:
            from valclient.client import Client  # type: ignore
            client = Client(region=self.region)
            client.activate()
            self._valclient = client
            return client
        except Exception as e:  # noqa: BLE001
            _log(f"valclient unavailable: {e}")
            self._valclient = False
            return None

    def _local_overview(self, puuid: str) -> dict | None:
        client = self._get_valclient()
        if not client:
            return None
        hist = client.fetch_match_history(puuid, start_index=0, end_index=20)
        matches = []
        for h in (hist or {}).get("History", [])[:20]:
            try:
                details = client.fetch_match_details(h["MatchID"])
                norm = self._normalize_match(details, puuid)
                if norm:
                    matches.append(norm)
            except Exception as e:  # noqa: BLE001
                _log(f"match detail fetch failed: {e}")
        if not matches:
            return None
        tier, rr = self._local_rank(client, puuid, matches)
        return {
            "puuid": puuid,
            "riotId": self._local_name(client, puuid),
            "rankTier": tier,
            "rr": rr,
            "peakTier": tier,
            "matches": matches,
            "source": "local",
            "sourceDetail": "Local VALORANT client API",
        }

    def _local_name(self, client, puuid: str) -> str:
        try:
            names = client.put_name_service(player_ids=[puuid])
            if names:
                n = names[0]
                return f"{n.get('GameName')}#{n.get('TagLine')}"
        except Exception:  # noqa: BLE001
            pass
        return "You"

    def _local_rank(self, client, puuid: str, matches):
        try:
            updates = client.fetch_competitive_updates(puuid)
            mt = (updates or {}).get("Matches", [])
            if mt:
                return mt[0].get("TierAfterUpdate", 0), mt[0].get("RankedRatingAfterUpdate", 0)
        except Exception as e:  # noqa: BLE001
            _log(f"competitive updates failed: {e}")
        return self._latest_tier(matches), 0

    # -- shared match normalizer (handles official + local shapes) ----------
    def _normalize_match(self, raw: dict, subject: str) -> dict | None:
        info = raw.get("matchInfo", raw)
        players = raw.get("players", [])
        subj = next(
            (p for p in players if p.get("subject") == subject or p.get("puuid") == subject),
            None,
        )
        if not subj:
            return None

        agent_uuid = (subj.get("characterId") or "").lower()
        agent_name = UUID_TO_NAME.get(agent_uuid, "Unknown")
        st = subj.get("stats", {}) or {}
        team_id = subj.get("teamId")

        teams = {t.get("teamId"): t for t in raw.get("teams", []) if t.get("teamId")}
        my_team = teams.get(team_id, {})
        won = my_team.get("won")
        rw = my_team.get("roundsWon", my_team.get("numPoints", 0))
        other = [t for tid, t in teams.items() if tid != team_id]
        rl = other[0].get("roundsWon", other[0].get("numPoints", 0)) if other else 0
        if won is True:
            result = "Victory"
        elif won is False:
            result = "Defeat"
        else:
            result = "Draw"

        teammates = []
        for p in players:
            if p is subj or p.get("teamId") != team_id:
                continue
            pid = p.get("subject") or p.get("puuid")
            name = (f"{p.get('gameName')}#{p.get('tagLine')}"
                    if p.get("gameName") else (pid or "Unknown")[:8])
            teammates.append({
                "puuid": pid,
                "name": name,
                "agent": UUID_TO_NAME.get((p.get("characterId") or "").lower(), "Unknown"),
            })

        queue = (info.get("queueId") or info.get("queueID") or "").lower()
        start_ms = info.get("gameStartMillis") or info.get("gameStartTimeMillis") or 0
        date = (datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).isoformat()
                if start_ms else datetime.now(timezone.utc).isoformat())

        rounds = max(rw + rl, 1)
        return {
            "matchId": info.get("matchId") or raw.get("matchId") or "unknown",
            "map": map_name_from_path(info.get("mapId", "")),
            "mode": _QUEUE_NAMES.get(queue, GAMEMODES.get(queue, "Custom")),
            "date": date,
            "result": result,
            "roundsWon": rw,
            "roundsLost": rl,
            "agent": agent_name,
            "stats": {
                "kills": st.get("kills", 0),
                "deaths": st.get("deaths", 0),
                "assists": st.get("assists", 0),
                "score": st.get("score", 0),
                "acs": round(st.get("score", 0) / rounds) if rounds else 0,
                "hsPct": 0.0,
            },
            "teammates": teammates,
        }

    @staticmethod
    def _latest_tier(matches) -> int:
        for m in matches:
            t = m.get("competitiveTier")
            if t:
                return t
        return 0

    # -- instalock ----------------------------------------------------------
    def instalock(self, agent_identifier: str, mode: str = "lock",
                  dry_run: bool = True, region: str | None = None) -> dict:
        """
        One-shot agent select/lock against the local client (region-aware).

        DRY-RUN (default) just reports what it *would* do. Turning dry-run OFF
        performs the real action — that automates the game client and may
        violate Riot's Terms of Service; you opt in by disabling dry-run.
        """
        agent = resolve_agent(agent_identifier)
        if not agent:
            return {"ok": False, "status": "error",
                    "message": f"Unknown agent '{agent_identifier}'."}

        if dry_run:
            print(f"[INSTALOCK:DRY-RUN] would {mode} {agent['name']}", flush=True)
            return {
                "ok": True, "status": "dry-run", "agent": agent["name"],
                "agentId": agent["uuid"], "mode": mode,
                "message": f"DRY-RUN: would {mode} {agent['name']}. "
                           f"Turn dry-run OFF to actually {mode}.",
            }
        return self._live_instalock(agent, mode, region)

    def _live_instalock(self, agent: dict, mode: str, region=None) -> dict:
        agent_uuid = agent["uuid"]
        try:
            auth = LocalAuth(region)
            auth.headers()
            pre = auth.glz_get(f"/pregame/v1/players/{auth.puuid}")
            match_id = pre.get("MatchID") if isinstance(pre, dict) else None
            if not match_id:
                return {"ok": False, "status": "error",
                        "message": "Not in agent select (no pregame match)."}
            auth.glz_post(f"/pregame/v1/matches/{match_id}/select/{agent_uuid}")
            if mode == "lock":
                auth.glz_post(f"/pregame/v1/matches/{match_id}/lock/{agent_uuid}")
            return {"ok": True, "status": "locked", "agent": agent["name"],
                    "message": f"{mode.title()}ed {agent['name']}."}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "status": "error",
                    "message": f"Instalock failed: {e}"}

    # -- dodge agent select -------------------------------------------------
    def dodge(self, dry_run: bool = True, region: str | None = None) -> dict:
        """
        Leave (dodge) the current agent-select lobby via the pregame quit
        endpoint. Dry-run unless the request turns it off.
        """
        if dry_run:
            print("[DODGE:DRY-RUN] would quit agent select", flush=True)
            return {"ok": True, "status": "dry-run",
                    "message": "DRY-RUN: would dodge agent select. "
                               "Turn dry-run OFF to actually dodge."}
        try:
            auth = LocalAuth(region)
            auth.headers()
            pre = auth.glz_get(f"/pregame/v1/players/{auth.puuid}")
            match_id = pre.get("MatchID") if isinstance(pre, dict) else None
            if not match_id:
                return {"ok": False, "status": "error",
                        "message": "Not in agent select (nothing to dodge)."}
            auth.glz_post(f"/pregame/v1/matches/{match_id}/quit")
            return {"ok": True, "status": "dodged",
                    "message": "Dodged agent select."}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "status": "error", "message": f"Dodge failed: {e}"}
