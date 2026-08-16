from __future__ import annotations

import json
import os
import threading
import time

from flask import Flask, jsonify, request
from flask_cors import CORS

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import discord_presence
import encounter_log
import scoutlog
import sync
import pick_advisor
import live_match
import party_detector
import sample_match
import session_tracker
import history
import inventory
import match_meta
from agents import AGENTS, resolve_agent
from instalock_worker import InstalockWorker
from riot_client import REGIONS, LocalAuth, RiotClient, ClientNotReady
from vconstants import APP_VERSION, STATES, rank_from_tier

app = Flask(__name__)
CORS(app)
_COMMAND_ROUTER = None

for _h in scoutlog.get_logger("backend").handlers:
    app.logger.addHandler(_h)

client = RiotClient()
instalock_worker = InstalockWorker()

_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = float(os.getenv("PLAYER_CACHE_TTL", "60"))
_ENCOUNTER_BACKFILL_AT: dict[str, float] = {}
_ENCOUNTER_BACKFILL_LOCK = threading.Lock()

_SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "data", "settings.json")
_SETTINGS_LOCK = threading.Lock()

_SETTINGS_KEYS = {"region", "agent", "mode", "delay", "dryRun", "perMap",
                  "autoRefresh"}

def _load_settings() -> dict:
    try:
        with open(_SETTINGS_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def _save_settings(data: dict) -> None:
    os.makedirs(os.path.dirname(_SETTINGS_PATH), exist_ok=True)
    tmp = f"{_SETTINGS_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, _SETTINGS_PATH)

def _summarize(matches: list[dict]) -> dict:
    n = len(matches)
    if n == 0:
        return {"matchesAnalyzed": 0, "kills": 0, "deaths": 0, "assists": 0,
                "kd": 0, "kda": 0, "hsPct": 0, "winRate": 0, "wins": 0, "losses": 0}
    k = sum(m["stats"]["kills"] for m in matches)
    d = sum(m["stats"]["deaths"] for m in matches)
    a = sum(m["stats"]["assists"] for m in matches)
    hs = [m["stats"].get("hsPct", 0) for m in matches if m["stats"].get("hsPct")]
    wins = sum(1 for m in matches if m["result"] == "Victory")
    losses = sum(1 for m in matches if m["result"] == "Defeat")
    return {
        "matchesAnalyzed": n,
        "kills": round(k / n, 1),
        "deaths": round(d / n, 1),
        "assists": round(a / n, 1),
        "kd": round(k / d, 2) if d else float(k),
        "kda": round((k + a) / d, 2) if d else float(k + a),
        "hsPct": round(sum(hs) / len(hs), 1) if hs else 0,
        "winRate": round(100 * wins / n, 1),
        "wins": wins,
        "losses": losses,
    }

def _decorate_match(m: dict, suggestion: dict) -> dict:
    meta = resolve_agent(m.get("agent")) or {}
    st = m["stats"]
    kda = round((st["kills"] + st["assists"]) / st["deaths"], 2) if st["deaths"] else float(st["kills"] + st["assists"])
    out = dict(m)
    out.pop("teammates", None)
    out["agentMeta"] = {
        "name": meta.get("name", m.get("agent")),
        "role": meta.get("role", "Flex"),
        "color": meta.get("color", "#FF4655"),
        "portrait": meta.get("portrait"),
    }
    out["stats"] = {**st, "kda": kda}

    out["pickSuggestion"] = {
        "agent": suggestion.get("agent"),
        "times": suggestion.get("times", 0),
    }
    return out

def build_player_payload(puuid: str) -> dict:
    raw = client.get_player_overview(puuid)
    matches = raw.get("matches", [])

    party = party_detector.analyze(matches, top_n=5)
    suggestion = pick_advisor.recommend(matches)
    rank = rank_from_tier(raw.get("rankTier"))
    peak = rank_from_tier(raw.get("peakTier"))

    decorated = [_decorate_match(m, suggestion) for m in party["matches"]]

    for dm, pm in zip(decorated, party["matches"]):
        dm["partyMembers"] = pm.get("partyMembers", [])

    return {
        "puuid": puuid,
        "riotId": raw.get("riotId", "Player"),
        "currentRank": rank["name"],
        "rankTier": rank["tier"],
        "rankGroup": rank["group"],
        "rankColor": rank["color"],
        "rr": raw.get("rr", 0),
        "peakRank": peak["name"],
        "peakColor": peak["color"],
        "source": raw.get("source", "demo"),
        "sourceDetail": raw.get("sourceDetail", ""),
        "averages": _summarize(matches),
        "pickSuggestion": suggestion,
        "coPlayers": party["coPlayers"],
        "partyCount": party["partyCount"],
        "matches": decorated,
    }

@app.get("/api/health")
def health():
    import ws_server as _ws
    return jsonify({
        "ok": True,
        "service": "valorant-scout",
        "appVersion": APP_VERSION,
        "protocol": _ws.PROTOCOL_VERSION,
        "wsReady": _ws.is_ready(),
        "wsPort": _ws.listening_port(),
        "dataSourcePreference": client.source_pref,
        "officialKey": bool(client.api_key),
        "liveInstalockEnabled": client.allow_live_instalock,

        "clientStatus": "ok" if LocalAuth.available() else "not_running",
    })

@app.get("/api/agents")
def agents():
    return jsonify({"agents": AGENTS, "count": len(AGENTS)})

@app.get("/api/settings")
def settings_get():
    pass
    with _SETTINGS_LOCK:
        return jsonify(_load_settings())

@app.post("/api/settings")
def settings_post():
    pass
    body = request.get_json(silent=True) or {}
    incoming = {k: v for k, v in body.items() if k in _SETTINGS_KEYS}
    with _SETTINGS_LOCK:
        merged = _load_settings()
        merged.update(incoming)
        try:
            _save_settings(merged)
        except Exception as e:
            app.logger.exception("settings save failed")
            return jsonify({"ok": False, "message": str(e),
                            "settings": merged}), 200
    return jsonify({"ok": True, "settings": merged})

def _live_enabled() -> bool:
    return client.source_pref != "demo" and LocalAuth.available()

def _attach_encounters(board: dict) -> dict:
    pass
    is_live = board.get("source") == "local"
    self_team = board.get("selfTeam")
    for p in board.get("players") or []:
        if not isinstance(p, dict):
            continue
        enc = encounter_log.encounter_for(board.get("selfPuuid"), p.get("puuid")) if is_live else None

        if enc:
            if self_team is not None and p.get("team") == self_team:
                enc["withCount"] = max(0, enc["withCount"] - 1)
            else:
                enc["againstCount"] = max(0, enc["againstCount"] - 1)
        p["encounter"] = enc
    return board

def _client_notice() -> dict:
    pass
    if not LocalAuth.available():
        return {"level": "info", "action": "open_game",
                "message": "Open VALORANT to see live ranks, parties and stats."}
    return {"level": "warn", "action": "restart_game",
            "message": "Couldn't read VALORANT — please restart your game "
                       "(close it completely and relaunch), then try again."}

_LAST_GOOD = {"board": None, "at": 0.0, "notReady": False}
_HOLD_SECS = 12

_BUILD_LOCK = threading.Lock()
_BUILD_FRESH = 3.5

def build_live(seed: int = 7, want_state: str | None = None) -> dict:
    pass
    notice = None
    if _live_enabled():
        with _BUILD_LOCK:
            if _LAST_GOOD["board"] and time.time() - _LAST_GOOD["at"] < _BUILD_FRESH:
                return _LAST_GOOD["board"]
            try:
                lm = live_match.LiveMatch(LocalAuth())
                board = lm.build_scoreboard(
                    include_stats=os.getenv("LIVE_INCLUDE_STATS", "true").lower() != "false"
                )
                board.setdefault("sourceDetail", "Local VALORANT client")
                board["selfPuuid"] = lm.self_puuid

                try:
                    session_tracker.observe(board, lm)
                    session_tracker.attach(board)
                except Exception:
                    app.logger.exception("session tracking failed")

                try:
                    encounter_log.record_board(board)
                    _attach_encounters(board)
                except Exception:
                    app.logger.exception("encounter logging failed")
                board["appVersion"] = APP_VERSION
                sync.observe(board)
                _LAST_GOOD["board"], _LAST_GOOD["at"] = board, time.time()
                _LAST_GOOD["notReady"] = False
                return board
            except Exception as e:
                if isinstance(e, ClientNotReady):
                    if not _LAST_GOOD["notReady"]:
                        app.logger.info("live scoreboard: %s — waiting for sign-in", e)
                        _LAST_GOOD["notReady"] = True
                else:
                    app.logger.exception("live scoreboard failed")

                if _LAST_GOOD["board"] and time.time() - _LAST_GOOD["at"] < _HOLD_SECS:
                    return _LAST_GOOD["board"]
                notice = _client_notice()
                if client.source_pref == "local":
                    return {"state": "OFFLINE", "stateLabel": "Offline", "source": "local",
                            "error": str(e), "players": [], "teams": {}, "parties": [],
                            "notice": notice, "appVersion": APP_VERSION}
    elif client.source_pref != "demo" and not LocalAuth.available():

        notice = _client_notice()

    board = (sample_match.generate_lobby(seed)
             if (want_state or "").lower() == "menus"
             else sample_match.generate(seed))
    board = _attach_encounters(board)
    if notice:
        board["notice"] = notice
    board["appVersion"] = APP_VERSION
    return board

@app.get("/api/state")
def state():
    pass
    if _live_enabled():
        try:
            lm = live_match.LiveMatch(LocalAuth())
            st = lm.game_state(lm._presences())
            return jsonify({"state": st, "stateLabel": STATES.get(st, st), "source": "local"})
        except Exception as e:
            return jsonify({"state": "OFFLINE", "stateLabel": "Offline",
                            "source": "local", "error": str(e)})
    return jsonify({"state": "INGAME", "stateLabel": "In Game", "source": "demo"})

@app.get("/api/live")
def live():
    try:
        seed = int(request.args.get("seed", 7))
    except (TypeError, ValueError):
        seed = 7
    return jsonify(build_live(seed, request.args.get("state")))


def _refresh_encounter_history(owner: str | None) -> None:
    if not owner or not _live_enabled():
        return
    now = time.time()
    with _ENCOUNTER_BACKFILL_LOCK:
        if now - _ENCOUNTER_BACKFILL_AT.get(owner, 0) < 600:
            return
        _ENCOUNTER_BACKFILL_AT[owner] = now
        try:
            lm = live_match.LiveMatch(LocalAuth())
            career = lm.player_career(owner, count=10)
            encounter_log.backfill_career(owner, career.get("matches") or [])

            season = lm.season_id()
            previous_season = lm.prev_season_id()
            for teammate in (career.get("coPlayers") or [])[:6]:
                if int(teammate.get("sharedMatches") or 0) < 2:
                    continue
                puuid = teammate.get("puuid")
                if not puuid:
                    continue
                rank = lm.rank_info(puuid, season, previous_season)
                tier = int(rank.get("tier") or 0)
                if tier <= 0:
                    continue
                current = rank_from_tier(tier)
                peak = rank_from_tier(rank.get("peak") or tier)
                encounter_log.enrich_player(owner, puuid, {
                    "name": teammate.get("name"),
                    "rank": current["name"],
                    "peakRank": peak["name"],
                    "rankTier": current["tier"],
                    "peakTier": peak["tier"],
                    "rankColor": current["color"],
                    "winRate": rank.get("wr"),
                })
        except Exception:
            _ENCOUNTER_BACKFILL_AT.pop(owner, None)
            app.logger.exception("encounter history backfill failed")

@app.get("/api/encounters")
def encounters():
    pass
    if client.source_pref == "demo":
        return jsonify({"players": sample_match.encounters(), "accountCount": 1,
                        "scope": request.args.get("scope", "current")})
    owner = _current_puuid()
    _refresh_encounter_history(owner)
    scope = "all" if request.args.get("scope") == "all" else "current"
    return jsonify({"players": encounter_log.get_all_accounts(owner) if scope == "all"
                    else encounter_log.get_all(owner),
                    "accountCount": encounter_log.account_count(), "scope": scope})

@app.get("/api/recap")
def recap():
    pass
    live_recap = session_tracker.current_recap() if _live_enabled() else None
    try:
        seed = int(request.args.get("seed", 7))
    except (TypeError, ValueError):
        seed = 7
    return jsonify(live_recap or sample_match.recap(seed))


def _current_puuid() -> str | None:
    if not _live_enabled():
        return None
    try:
        auth = LocalAuth()
        auth.headers()
        return auth.puuid
    except Exception:
        return None


@app.get("/api/sessions")
def sessions_get():
    return jsonify(session_tracker.list_for(_current_puuid()))


@app.post("/api/session/start")
def session_start():
    owner = _current_puuid()
    baseline = history.payload(owner).get("summary", {}) if owner else None
    return jsonify(session_tracker.start(owner, (request.get_json(silent=True) or {}).get("goal"), baseline))


@app.post("/api/session/end")
def session_end():
    return jsonify(session_tracker.end(_current_puuid()))


@app.delete("/api/sessions/<session_id>")
def session_delete(session_id: str):
    return jsonify(session_tracker.delete(_current_puuid(), session_id.strip()))


@app.post("/api/session/reset")
def session_reset():
    body = request.get_json(silent=True) or {}
    return jsonify(session_tracker.reset(_current_puuid(), body.get("goal")))


@app.post("/api/remote-mode")
def remote_mode():
    if _COMMAND_ROUTER is None:
        return jsonify({"ok": False, "configured": False,
                        "message": "The local command bridge is still starting."}), 503
    body = request.get_json(silent=True) or {}
    action = "disable_remote" if body.get("action") == "disable" else "enable_remote"
    result = _COMMAND_ROUTER.execute(
        client_id=f"http:{request.remote_addr or 'local'}", command=action,
        payload={}, command_id=body.get("id"))
    return jsonify(result)


def _insights_payload(timezone_name: str | None = None) -> dict:
    puuid = None
    if _live_enabled():
        try:
            auth = LocalAuth()
            auth.headers()
            puuid = auth.puuid
            threading.Thread(target=history.refresh,
                             args=(auth, timezone_name), daemon=True,
                             name=f"rr-refresh-{str(puuid)[:8]}").start()
        except Exception:
            app.logger.exception("rr history refresh failed")
    return history.payload(puuid, timezone_name)


def _performance_payload(timezone_name: str | None = None, rich_limit: int = 20) -> dict:
    payload = _insights_payload(timezone_name)
    owner = (payload.get("account") or {}).get("puuid")
    if owner and _live_enabled():
        def enrich_recent():
            try:
                history.enrich(live_match.LiveMatch(LocalAuth()), owner, rich_limit)
            except Exception:
                app.logger.exception("performance enrichment failed")
        threading.Thread(target=enrich_recent, daemon=True,
                         name=f"perf-enrich-{str(owner)[:8]}").start()
    if owner:
        session_tracker.ensure_active(owner, payload.get("summary", {}))
    payload["sessions"] = session_tracker.list_for(owner)
    payload["matchMeta"] = match_meta.get_all(owner)
    payload["encounters"] = encounter_log.get_all(owner)
    return payload

@app.get("/api/insights")
def insights():
    return jsonify(_insights_payload(request.args.get("tz")))


@app.get("/api/performance")
def performance():
    try:
        rich_limit = int(request.args.get("richLimit", 20))
    except (TypeError, ValueError):
        rich_limit = 20
    return jsonify(_performance_payload(request.args.get("tz"), rich_limit))

def _inventory_payload() -> dict:
    if not _live_enabled():
        return {
            "available": False,
            "retryable": client.source_pref != "demo",
            "error": "Live client not available.",
        }
    auth = LocalAuth()
    owner = None
    try:
        data = inventory.snapshot(auth)
        owner = getattr(auth, "puuid", None)
        return data
    except ClientNotReady:
        owner = getattr(auth, "puuid", None)
        cached = inventory.last_good(owner)
        if cached:
            return cached
        return {"available": False, "retryable": True,
                "error": "Your collection is still loading from Riot."}
    except Exception:
        app.logger.exception("inventory snapshot failed")
        cached = inventory.last_good(owner)
        if cached:
            return cached
        return {"available": False,
                "retryable": True,
                "error": "Couldn't read your collection from the Riot client."}

@app.get("/api/inventory")
def inventory_route():
    return jsonify(_inventory_payload())

@app.get("/api/encounters/<puuid>")
def encounter(puuid: str):
    pass
    return jsonify(encounter_log.get_one(_current_puuid(), puuid.strip()))


@app.put("/api/matches/<match_id>/meta")
def match_meta_update(match_id: str):
    return jsonify(match_meta.update(_current_puuid(), match_id.strip(), request.get_json(silent=True) or {}))

@app.get("/api/match/<match_id>")
def match(match_id: str):
    pass
    subject = request.args.get("subject")
    if _live_enabled():
        try:
            data = live_match.LiveMatch(LocalAuth()).match_detail(match_id, subject)
            if not data.get("error"):
                return jsonify(data)
        except Exception:
            app.logger.exception("match detail failed")
    return jsonify(sample_match.match_detail(match_id, subject))

@app.get("/api/debug/reveal")
def debug_reveal():
    pass
    if not _live_enabled():
        return jsonify({"error": "Live client not available — open VALORANT."}), 400
    try:
        return jsonify(live_match.LiveMatch(LocalAuth()).diagnose_reveal())
    except Exception as e:
        app.logger.exception("debug reveal failed")
        return jsonify({"error": str(e)}), 500

@app.get("/api/profile/<puuid>")
def profile(puuid: str):
    pass
    puuid = puuid.strip()
    if not puuid:
        return jsonify({"error": "puuid required"}), 400

    now = time.time()
    cached = _CACHE.get(f"profile:{puuid}")
    if cached and now - cached[0] < _CACHE_TTL:
        return jsonify(cached[1])

    data = None
    if _live_enabled():
        try:
            data = live_match.LiveMatch(LocalAuth()).player_career(puuid)
            if not data.get("matches"):
                data = None
        except Exception:
            app.logger.exception("live profile failed")
            data = None
    if data is None:
        data = sample_match.career(puuid)

    _CACHE[f"profile:{puuid}"] = (now, data)
    return jsonify(data)

@app.get("/api/player/<puuid>")
def player(puuid: str):
    puuid = puuid.strip()
    if not puuid or len(puuid) < 6:
        return jsonify({"error": "A valid PUUID (or Riot identifier) is required."}), 400

    now = time.time()
    cached = _CACHE.get(puuid)
    if cached and now - cached[0] < _CACHE_TTL:
        return jsonify(cached[1])

    try:
        payload = build_player_payload(puuid)
    except Exception as e:
        app.logger.exception("player payload failed")
        return jsonify({"error": f"Failed to build player profile: {e}"}), 500

    _CACHE[puuid] = (now, payload)
    return jsonify(payload)

@app.get("/api/region")
def region():
    pass
    detected = None
    if LocalAuth.available():
        try:
            detected = LocalAuth().shard
        except Exception:
            detected = None
    return jsonify({"detected": detected, "regions": REGIONS})

@app.post("/api/dodge")
def dodge():
    body = request.get_json(silent=True) or {}
    result = client.dodge(dry_run=bool(body.get("dryRun", True)),
                          region=body.get("region"))
    return jsonify(result), (200 if result.get("ok") else 400)

@app.post("/api/launch-offline")
def launch_offline():
    import offline_launch
    body = request.get_json(silent=True) or {}
    result = offline_launch.launch(body.get("status"))
    return jsonify(result), (200 if result.get("ok") else 400)

@app.get("/api/offline-status")
def offline_status():
    import offline_launch
    return jsonify(offline_launch.status())

@app.post("/api/offline-toggle")
def offline_toggle():
    import offline_launch
    body = request.get_json(silent=True) or {}
    if "status" in body:
        result = offline_launch.set_status(str(body["status"]))
    else:
        result = offline_launch.set_enabled(bool(body.get("enabled", True)))
    return jsonify(result), (200 if result.get("ok") else 400)

@app.get("/api/queue")
def queue_get():
    pass
    return jsonify(client.party_state())

@app.post("/api/queue")
def queue_post():
    pass
    body = request.get_json(silent=True) or {}
    action = (body.get("action") or "").lower()
    dry = bool(body.get("dryRun", True))
    region = body.get("region")
    if action == "select":
        result = client.set_queue(body.get("queueId"), dry_run=dry, region=region)
    elif action == "start":
        result = client.start_queue(dry_run=dry, region=region)
    elif action == "stop":
        result = client.stop_queue(dry_run=dry, region=region)
    else:
        return jsonify({"ok": False,
                        "message": "action must be start|stop|select"}), 400
    result["queue"] = client.party_state(region)
    return jsonify(result), (200 if result.get("ok") else 400)

@app.post("/api/instalock")
def instalock():
    pass
    body = request.get_json(silent=True) or {}
    agent = body.get("agent")
    mode = (body.get("mode") or "lock").lower()
    dry_run = bool(body.get("dryRun", True))
    if not agent:
        return jsonify({"ok": False, "message": "Field 'agent' is required."}), 400
    result = client.instalock(agent, mode=mode, dry_run=dry_run,
                              region=body.get("region"))
    return jsonify(result), (200 if result.get("ok") else 400)

@app.post("/api/instalock/start")
def instalock_start():
    pass
    body = request.get_json(silent=True) or {}
    agent = body.get("agent")
    mode = (body.get("mode") or "lock").lower()
    delay = body.get("delay", 0)
    dry_run = bool(body.get("dryRun", True))
    region = body.get("region")
    per_map = body.get("perMap") if isinstance(body.get("perMap"), dict) else None
    if not agent:
        return jsonify({"ok": False, "message": "Field 'agent' is required."}), 400
    if dry_run:
        ag = resolve_agent(agent)
        if not ag:
            return jsonify({"ok": False, "message": f"Unknown agent '{agent}'."}), 400
        for mapn, name in (per_map or {}).items():
            if not resolve_agent(name):
                return jsonify({"ok": False,
                                "message": f"Unknown agent '{name}' for map '{mapn}'."}), 400
        return jsonify({"ok": True, "status": "dry-run", "agent": ag["name"],
                        "perMap": per_map or {},
                        "message": f"DRY-RUN: would {mode} {ag['name']} (per-map "
                                   f"overrides applied) when agent select starts. "
                                   f"Turn dry-run OFF to auto-lock."})
    result = instalock_worker.start(agent, mode=mode, delay=delay, region=region,
                                    per_map=per_map)
    return jsonify(result), (200 if result.get("ok") else 400)

@app.post("/api/instalock/stop")
def instalock_stop():
    return jsonify(instalock_worker.stop())

@app.get("/api/instalock/status")
def instalock_status():
    return jsonify(instalock_worker.status())

@app.get("/")
def index():
    return jsonify({
        "service": "Valorant Scout API",
        "endpoints": ["/api/health", "/api/live", "/api/profile/<puuid>", "/api/agents",
                      "/api/instalock/start", "/api/settings", "/api/encounters"],
    })

def _current_weapons(puuid: str) -> list:
    pass
    try:
        board = build_live(7, None)
        for p in board.get("players") or []:
            if p.get("puuid") == puuid:
                return p.get("weapons") or []
    except Exception:
        pass
    return []

def handle_data_request(req_type: str, params: dict | None) -> dict:
    pass
    params = params or {}
    try:
        if req_type == "profile":
            puuid = (params.get("puuid") or "").strip()
            if not puuid:
                return {"error": "puuid required"}
            data = None
            if _live_enabled():
                try:
                    d = live_match.LiveMatch(LocalAuth()).player_career(puuid)
                    if d.get("matches"):
                        data = d
                except Exception:
                    app.logger.exception("transport profile failed")
            if data is None:
                data = sample_match.career(puuid)
            out = dict(data)

            out["weapons"] = _current_weapons(puuid)
            try:
                out["encounter"] = encounter_log.get_one(_current_puuid(), puuid)
            except Exception:
                out["encounter"] = None
            return out

        if req_type == "match":
            match_id = (params.get("matchId") or "").strip()
            subject = params.get("subject")
            if not match_id:
                return {"error": "matchId required"}
            if _live_enabled():
                try:
                    d = live_match.LiveMatch(LocalAuth()).match_detail(match_id, subject)
                    if not d.get("error"):
                        return d
                except Exception:
                    app.logger.exception("transport match failed")
            return sample_match.match_detail(match_id, subject)

        if req_type == "encounter":
            return encounter_log.get_one(_current_puuid(), (params.get("puuid") or "").strip()) or {}

        if req_type == "recap":

            live_recap = session_tracker.current_recap() if _live_enabled() else None
            return live_recap or sample_match.recap(int(params.get("seed") or 7))

        if req_type == "encounters":
            owner = _current_puuid()
            _refresh_encounter_history(owner)
            scope = "all" if params.get("scope") == "all" else "current"
            return {"players": encounter_log.get_all_accounts(owner) if scope == "all"
                    else encounter_log.get_all(owner),
                    "accountCount": encounter_log.account_count(), "scope": scope}

        if req_type == "insights":
            return _insights_payload(params.get("tz"))

        if req_type == "performance":
            return _performance_payload(params.get("tz"), int(params.get("richLimit") or 20))

        if req_type == "sessions":
            return session_tracker.list_for(_current_puuid())

        if req_type == "match_meta":
            return match_meta.get_one(_current_puuid(), (params.get("matchId") or "").strip())

        if req_type == "inventory":
            return _inventory_payload()
    except Exception as e:
        return {"error": f"request failed: {e}"}
    return {"error": f"unknown request '{req_type}'"}

def _start_ws_bridge() -> None:
    pass
    global _COMMAND_ROUTER
    import ws_server
    import scout_commands
    import remote_ably

    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000").rstrip("/")
    ws_port = int(os.getenv("WS_PORT", "7878"))
    token_endpoint = os.getenv("ABLY_TOKEN_ENDPOINT",
                               f"{frontend_url}/api/ably-token")

    def ws_state_provider() -> dict:

        board = dict(build_live(7, None))
        board["instalock"] = instalock_worker.status()
        board["agents"] = AGENTS
        board["liveInstalockEnabled"] = client.allow_live_instalock
        return board

    remote_controller = remote_ably.RemoteController(
        frontend_url=frontend_url, token_endpoint=token_endpoint,
        board_provider=ws_state_provider, data_handler=handle_data_request)
    router = scout_commands.CommandRouter(
        instalock_worker=instalock_worker, riot_client=client,
        board_provider=ws_state_provider, remote_controller=remote_controller)
    _COMMAND_ROUTER = router
    remote_controller.attach_router(router)

    try:
        token = ws_server.start(board_provider=ws_state_provider, command_router=router,
                                frontend_url=frontend_url, ws_port=ws_port,
                                remote_controller=remote_controller,
                                request_handler=handle_data_request,
                                backend_port=int(os.getenv("BACKEND_PORT",
                                                           os.getenv("PORT", "5000"))))
    except Exception as e:
        app.logger.exception("VS-WS-001 WebSocket bridge failed to start")
        print(f"[app] VS-WS-001 WebSocket bridge failed: {e}", flush=True)
        raise SystemExit(1)
    _write_bridge_file(ws_port, token)

def _write_bridge_file(ws_port: int, token: str) -> None:
    import tempfile
    import ws_server
    try:
        scout_dir = scoutlog.SCOUT_DIR
        scout_dir.mkdir(exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(scout_dir), prefix=".bridge-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"wsPort": ws_port, "token": token,
                           "protocol": ws_server.PROTOCOL_VERSION,
                           "pid": os.getpid()}, fh)
            os.replace(tmp, str(scout_dir / "bridge.json"))
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    except Exception:
        app.logger.exception("bridge.json write failed — CLI bridge unavailable")

if __name__ == "__main__":
    port = int(os.getenv("BACKEND_PORT", os.getenv("PORT", "5000")))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    discord_presence.maybe_start()
    sync.maybe_start()

    if not debug or os.getenv("WERKZEUG_RUN_MAIN") == "true":
        _start_ws_bridge()
    print(f"[app] Valorant Scout API on http://127.0.0.1:{port}  "
          f"(source={client.source_pref}, key={'set' if client.api_key else 'unset'})",
          flush=True)
    try:
        app.run(host="127.0.0.1", port=port, debug=debug)
    except OSError as e:
        app.logger.error("VS-BACKEND-001 could not bind 127.0.0.1:%s: %s", port, e)
        print(f"[app] VS-BACKEND-001 could not bind 127.0.0.1:{port}: {e}", flush=True)
        raise SystemExit(1)
