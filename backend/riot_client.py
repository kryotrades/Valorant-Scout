from __future__ import annotations

import base64
import json
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

_CLIENT_VERSION: str | None = None

_ROUTING = {
    "na": "americas", "latam": "americas", "br": "americas",
    "eu": "europe", "ap": "asia", "kr": "asia",
}

REGION_MAP = {
    "na":    ("na",    "na-1", "na"),
    "eu":    ("eu",    "eu-1", "eu"),
    "ap":    ("ap",    "ap-1", "ap"),
    "kr":    ("kr",    "kr-1", "kr"),
    "latam": ("latam", "na-1", "latam"),
    "br":    ("br",    "na-1", "br"),
}
REGIONS = ["na", "eu", "ap", "kr", "latam", "br"]

_QUEUE_NAMES = {
    "competitive": "Competitive", "unrated": "Unrated", "swiftplay": "Swiftplay",
    "spikerush": "Spike Rush", "deathmatch": "Deathmatch", "ggteam": "Escalation",
    "hurm": "Team Deathmatch", "": "Custom",
}

def _log(msg: str) -> None:
    if os.getenv("SCOUT_QUIET"):
        return
    print(f"[riot_client] {msg}", flush=True)

_RIOT_RATE_LOCK = threading.Lock()
try:

    _RIOT_MAX_RPS = max(0.0, float(os.getenv("RIOT_MAX_RPS", "10")))
except ValueError:
    _RIOT_MAX_RPS = 10.0
_RIOT_BURST = _RIOT_MAX_RPS if _RIOT_MAX_RPS > 0 else 1.0
_RIOT_BUCKET = {"tokens": _RIOT_BURST, "at": 0.0}

def _riot_throttle() -> None:
    pass
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

class LocalAuth:
    pass

    def __init__(self, region: str | None = None):
        self.lockfile = self._get_lockfile()

        region = (region or "").strip().lower()
        if region in REGION_MAP:
            shard, ga, gb = REGION_MAP[region]
            self.region = [shard, [ga, gb]]
        else:
            self.region = self._get_region()
        self.pd_url = f"https://pd.{self.region[0]}.a.pvp.net"
        self.glz_url = f"https://glz-{self.region[1][0]}.{self.region[1][1]}.a.pvp.net"
        self.shard = self.region[0]
        self._headers: dict | None = None
        self.puuid = ""
        self.req_count = 0

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
        pass
        global _CLIENT_VERSION
        if _CLIENT_VERSION:
            return _CLIENT_VERSION
        try:
            local = {"Authorization": "Basic " + base64.b64encode(
                ("riot:" + self.lockfile["password"]).encode()).decode()}
            data = requests.get(
                f"https://127.0.0.1:{self.lockfile['port']}/chat/v4/presences",
                headers=local, verify=False, timeout=5).json()
            for pr in (data or {}).get("presences", []) or []:
                if pr.get("product") != "valorant" or not pr.get("private"):
                    continue
                try:
                    priv = json.loads(base64.b64decode(str(pr["private"])).decode("utf-8"))
                except Exception:
                    continue
                v = (priv.get("partyPresenceData") or {}).get("partyClientVersion")                    or priv.get("partyClientVersion")
                if v:
                    _CLIENT_VERSION = v
                    _log(f"client version (local presence): {v}")
                    return v
        except Exception as e:
            _log(f"local presence version lookup failed ({e}); trying valorant-api")
        try:
            data = requests.get("https://valorant-api.com/v1/version", timeout=6).json()
            rcv = (data.get("data") or {}).get("riotClientVersion")
            if rcv:
                _CLIENT_VERSION = rcv
                _log(f"client version (valorant-api): {rcv}")
                return rcv
        except Exception as e:
            _log(f"valorant-api version lookup failed ({e}); using log")
        try:
            path = os.path.join(os.getenv("LOCALAPPDATA", ""),
                                r"VALORANT\Saved\Logs\ShooterGame.log")
            with open(path, "r", encoding="utf8") as f:
                for line in f:
                    if "CI server version:" in line:
                        _CLIENT_VERSION = line.split("CI server version: ")[1].strip()
                        return _CLIENT_VERSION
        except Exception:
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

    def glz_post(self, endpoint: str, json: dict | None = None) -> requests.Response:
        _riot_throttle()
        self.req_count += 1
        return requests.post(self.glz_url + endpoint, headers=self.headers(),
                             json=json, verify=False, timeout=8)

    @staticmethod
    def _json(resp):
        pass
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
        pass
        backoff = 3.0
        for attempt in range(retries + 1):
            _riot_throttle()
            self.req_count += 1
            resp = requests.get(self.pd_url + endpoint, headers=self.headers(refresh),
                                verify=False, timeout=8)
            if resp.status_code == 429:
                if attempt < retries:

                    try:
                        ra = float(resp.headers.get("Retry-After") or 0)
                    except (TypeError, ValueError):
                        ra = 0.0
                    time.sleep(min(ra or backoff, 30.0))
                    backoff += 3.0
                    continue
                return {"errorCode": "RATE_LIMITED", "status": 429}
            return self._json(resp)
        return {"errorCode": "RATE_LIMITED", "status": 429}

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

def _iso_to_epoch(s: str | None) -> float | None:
    pass
    try:
        dt = datetime.fromisoformat((s or "").replace("Z", "+00:00"))
        return dt.timestamp() if dt.year >= 2000 else None
    except Exception:
        return None

_PARTY_LOGGED = False

_QUEUE_STARTED: float | None = None

def _self_presence_private(auth: LocalAuth) -> dict | None:
    pass
    try:
        data = auth.local_get("/chat/v4/presences")
    except Exception:
        return None
    for pr in (data or {}).get("presences", []) or []:
        if pr.get("puuid") != auth.puuid or not pr.get("private"):
            continue
        if pr.get("product") not in (None, "valorant"):
            continue
        try:
            priv = json.loads(base64.b64decode(str(pr["private"])).decode("utf-8"))
            return priv if isinstance(priv, dict) else None
        except Exception:
            return None
    return None

def party_snapshot(auth: LocalAuth) -> dict:
    pass
    global _PARTY_LOGGED
    priv = _self_presence_private(auth)
    if not priv:
        return {"available": False}
    pdata = priv.get("partyPresenceData") or {}
    pid = pdata.get("partyId") or priv.get("partyId")
    if not pid:
        return {"available": False}

    def _label(q):
        return GAMEMODES.get(q, q.replace("_", " ").title())

    state = pdata.get("partyState") or "DEFAULT"
    qid = (priv.get("queueId")
           or (priv.get("matchPresenceData") or {}).get("queueId") or "").lower()
    snap = {
        "available": True,
        "partyId": pid,
        "queueId": qid or None,
        "queueName": _label(qid) if qid else None,
        "eligible": [],
        "state": state,
        "inQueue": "MATCHMAKING" in state,
        "queuedAt": None,
        "partySize": pdata.get("partySize") or priv.get("partySize") or 1,
        "isOwner": bool(pdata.get("isPartyOwner", True)),
        "allReady": True,
    }

    party = auth.glz_get(f"/parties/v1/parties/{pid}")
    if isinstance(party, dict) and party.get("Members"):
        if not _PARTY_LOGGED:
            _PARTY_LOGGED = True
            _log(f"party payload keys: {sorted(party.keys())}")
        members = party.get("Members") or []
        mine = next((m for m in members if m.get("Subject") == auth.puuid), {})
        gqid = ((party.get("MatchmakingData") or {}).get("QueueID") or "").lower()
        if gqid:
            snap["queueId"], snap["queueName"] = gqid, _label(gqid)
        if party.get("State"):
            snap["state"] = party["State"]
            snap["inQueue"] = "MATCHMAKING" in party["State"]
        snap["eligible"] = [{"id": q, "name": _label(q)}
                            for q in (party.get("EligibleQueues") or [])]
        snap["queuedAt"] = _iso_to_epoch(party.get("QueueEntryTime"))
        snap["partySize"] = len(members)
        if "IsOwner" in mine:
            snap["isOwner"] = bool(mine.get("IsOwner"))
        snap["allReady"] = all(bool(m.get("IsReady", True)) for m in members)
    elif isinstance(party, dict) and party.get("status") == 429:
        snap["throttled"] = True

    global _QUEUE_STARTED
    if snap["inQueue"]:
        now = time.time()
        glz_at = snap["queuedAt"]
        if glz_at and glz_at <= now:
            _QUEUE_STARTED = glz_at
        elif _QUEUE_STARTED is None:
            _QUEUE_STARTED = now
        snap["queuedAt"] = _QUEUE_STARTED

        snap["queueElapsed"] = max(0, round(now - _QUEUE_STARTED, 1))
    else:
        _QUEUE_STARTED = None
        snap["queuedAt"] = None
    return snap

class RiotClient:
    def __init__(self):
        self.api_key = os.getenv("RIOT_API_KEY", "").strip()
        self.region = os.getenv("RIOT_REGION", "na").strip().lower()
        self.source_pref = os.getenv("DATA_SOURCE", "auto").strip().lower()
        self.allow_live_instalock = os.getenv("ALLOW_LIVE_INSTALOCK", "true").lower() == "true"
        self._valclient = None

    def get_player_overview(self, puuid: str) -> dict:
        pass
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
            except Exception as e:
                last_err = e
                _log(f"source '{src}' failed: {e}")

        _log(f"falling back to demo (last error: {last_err})")
        return self._demo_overview(puuid)

    def _demo_overview(self, puuid: str) -> dict:
        data = sample_data.generate_player(puuid)

        real_id = self._official_riot_id(puuid) if self.api_key else None
        if real_id:
            data["riotId"] = real_id
            data["source"] = "demo"
            data["sourceDetail"] = "Generated matches • Riot ID verified via account-v1"
        else:
            data["sourceDetail"] = "Generated sample career (no live source reachable)"
        return data

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

    def _local_ready(self) -> bool:
        if self.source_pref not in ("auto", "local"):
            return False
        return LocalAuth.available()

    def _get_valclient(self):
        if self._valclient is not None:
            return self._valclient
        try:
            from valclient.client import Client
            client = Client(region=self.region)
            client.activate()
            self._valclient = client
            return client
        except Exception as e:
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
            except Exception as e:
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
        except Exception:
            pass
        return "You"

    def _local_rank(self, client, puuid: str, matches):
        try:
            updates = client.fetch_competitive_updates(puuid)
            mt = (updates or {}).get("Matches", [])
            if mt:
                return mt[0].get("TierAfterUpdate", 0), mt[0].get("RankedRatingAfterUpdate", 0)
        except Exception as e:
            _log(f"competitive updates failed: {e}")
        return self._latest_tier(matches), 0

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

    def instalock(self, agent_identifier: str, mode: str = "lock",
                  dry_run: bool = True, region: str | None = None) -> dict:
        pass
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
        except Exception as e:
            return {"ok": False, "status": "error",
                    "message": f"Instalock failed: {e}"}

    def dodge(self, dry_run: bool = True, region: str | None = None) -> dict:
        pass
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
        except Exception as e:
            return {"ok": False, "status": "error", "message": f"Dodge failed: {e}"}

    def _party_live(self) -> bool:
        pass
        return self.source_pref != "demo" and LocalAuth.available()

    def party_state(self, region: str | None = None) -> dict:
        pass
        if not self._party_live():
            import sample_match
            return sample_match.demo_queue_state()
        try:
            auth = LocalAuth(region)
            auth.headers()
            return party_snapshot(auth)
        except Exception as e:
            return {"available": False, "message": str(e)}

    def set_queue(self, queue_id: str, dry_run: bool = True,
                  region: str | None = None) -> dict:
        pass
        qid = (queue_id or "").strip().lower()
        if not qid or qid == "custom":
            return {"ok": False, "message": f"Unknown gamemode '{queue_id}'."}
        label = GAMEMODES.get(qid, qid.replace("_", " ").title())
        if not self._party_live():
            import sample_match
            if qid not in GAMEMODES:
                return {"ok": False, "message": f"Unknown gamemode '{queue_id}'."}
            return sample_match.demo_queue_set(qid)
        if dry_run:
            return {"ok": True, "status": "dry-run",
                    "message": f"DRY-RUN: would switch to {label}. "
                               f"Turn dry-run OFF to actually switch."}
        try:
            auth = LocalAuth(region)
            auth.headers()
            snap = party_snapshot(auth)
            if not snap.get("available"):
                return {"ok": False, "message": "Not in a party — is VALORANT "
                                                "fully loaded into the menus?"}
            elig = {e["id"] for e in snap.get("eligible") or []}
            if elig and qid not in elig:
                return {"ok": False,
                        "message": f"{label} isn't selectable right now."}
            r = auth.glz_post(f"/parties/v1/parties/{snap['partyId']}/queue",
                              json={"queueID": qid})
            if r.status_code >= 400:
                return {"ok": False,
                        "message": f"Riot refused the change (HTTP {r.status_code})."}
            return {"ok": True, "status": "selected", "queueId": qid,
                    "message": f"Gamemode set to {label}."}
        except Exception as e:
            return {"ok": False, "message": f"Change gamemode failed: {e}"}

    def start_queue(self, dry_run: bool = True, region: str | None = None) -> dict:
        pass
        if not self._party_live():
            import sample_match
            return sample_match.demo_queue_start()
        if dry_run:
            return {"ok": True, "status": "dry-run",
                    "message": "DRY-RUN: would start the queue. "
                               "Turn dry-run OFF to actually queue."}
        try:
            auth = LocalAuth(region)
            auth.headers()
            snap = party_snapshot(auth)
            if not snap.get("available"):
                return {"ok": False, "message": "Not in a party — is VALORANT "
                                                "fully loaded into the menus?"}
            if snap.get("inQueue"):
                return {"ok": True, "status": "queued", "inQueue": True,
                        "message": "Already in queue."}
            if not snap.get("isOwner"):
                return {"ok": False, "message": "Only the party owner can start the queue."}
            if not snap.get("allReady"):
                return {"ok": False, "message": "Not everyone in the party is ready."}
            r = auth.glz_post(f"/parties/v1/parties/{snap['partyId']}/matchmaking/join")
            if r.status_code >= 400:
                return {"ok": False,
                        "message": f"Riot refused the queue (HTTP {r.status_code})."}
            return {"ok": True, "status": "queued", "inQueue": True,
                    "message": f"Queue started — {snap.get('queueName') or 'matchmaking'}."}
        except Exception as e:
            return {"ok": False, "message": f"Start queue failed: {e}"}

    def stop_queue(self, dry_run: bool = True, region: str | None = None) -> dict:
        pass
        if not self._party_live():
            import sample_match
            return sample_match.demo_queue_stop()
        if dry_run:
            return {"ok": True, "status": "dry-run",
                    "message": "DRY-RUN: would cancel the queue. "
                               "Turn dry-run OFF to actually cancel."}
        try:
            auth = LocalAuth(region)
            auth.headers()
            snap = party_snapshot(auth)
            if not snap.get("available") or not snap.get("inQueue"):
                return {"ok": True, "status": "idle", "inQueue": False,
                        "message": "Not in a queue."}
            r = auth.glz_post(f"/parties/v1/parties/{snap['partyId']}/matchmaking/leave")
            if r.status_code >= 400:
                return {"ok": False,
                        "message": f"Riot refused the cancel (HTTP {r.status_code})."}
            return {"ok": True, "status": "idle", "inQueue": False,
                    "message": "Queue cancelled."}
        except Exception as e:
            return {"ok": False, "message": f"Cancel queue failed: {e}"}
