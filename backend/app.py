"""
app.py
======
Flask API for the VALORANT companion dashboard.

Routes
------
GET  /api/health                 -> liveness + active data source
GET  /api/agents                 -> canonical agent catalogue (for the grid)
GET  /api/player/<puuid>         -> full analysed career (stats, rank, party,
                                    preferred-agent suggestion)
POST /api/instalock              -> trigger an agent select/lock (dry-run by
                                    default; live path is ToS-gated)

The heavy lifting lives in riot_client / party_detector / pick_advisor. This
file only orchestrates them and shapes the JSON response.
"""

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
except Exception:  # noqa: BLE001 - dotenv optional
    pass

import discord_presence
import encounter_log
import pick_advisor
import live_match
import party_detector
import sample_match
from agents import AGENTS, resolve_agent
from instalock_worker import InstalockWorker
from riot_client import REGIONS, LocalAuth, RiotClient
from vconstants import STATES, rank_from_tier

app = Flask(__name__)
CORS(app)

client = RiotClient()
instalock_worker = InstalockWorker()

# Tiny TTL cache so pagination / re-renders don't refetch live data.
_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = float(os.getenv("PLAYER_CACHE_TTL", "60"))


# ---------------------------------------------------------------------------
# Server-side settings mirror (best-effort)
# ---------------------------------------------------------------------------
# A tiny JSON store so the user's panel choices (region/agent/perMap/etc.)
# survive even if the browser's localStorage is cleared. The frontend remains
# the source of truth via localStorage; this is just a convenience mirror.
_SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "data", "settings.json")
_SETTINGS_LOCK = threading.Lock()
# Keys we persist (anything else POSTed is ignored).
_SETTINGS_KEYS = {"region", "agent", "mode", "delay", "dryRun", "perMap",
                  "autoRefresh"}


def _load_settings() -> dict:
    try:
        with open(_SETTINGS_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - missing/corrupt file -> empty settings
        return {}


def _save_settings(data: dict) -> None:
    os.makedirs(os.path.dirname(_SETTINGS_PATH), exist_ok=True)
    tmp = f"{_SETTINGS_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, _SETTINGS_PATH)  # atomic on the same filesystem


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
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
    out.pop("teammates", None)  # internal; party graph uses coPlayers instead
    out["agentMeta"] = {
        "name": meta.get("name", m.get("agent")),
        "role": meta.get("role", "Flex"),
        "color": meta.get("color", "#FF4655"),
        "portrait": meta.get("portrait"),
    }
    out["stats"] = {**st, "kda": kda}
    # Each match carries the preferred-agent suggestion too.
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
    # Re-attach party flags onto the decorated matches.
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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return jsonify({
        "ok": True,
        "service": "valorant-scout",
        "dataSourcePreference": client.source_pref,
        "officialKey": bool(client.api_key),
        "liveInstalockEnabled": client.allow_live_instalock,
        # coarse client state; the detailed "restart your game" hint rides on the
        # live board's `notice` field (see build_live / _client_notice).
        "clientStatus": "ok" if LocalAuth.available() else "not_running",
    })


@app.get("/api/agents")
def agents():
    return jsonify({"agents": AGENTS, "count": len(AGENTS)})


@app.get("/api/settings")
def settings_get():
    """Return the server-side settings mirror (empty dict if never saved)."""
    with _SETTINGS_LOCK:
        return jsonify(_load_settings())


@app.post("/api/settings")
def settings_post():
    """Merge the posted keys into the saved settings and persist them."""
    body = request.get_json(silent=True) or {}
    incoming = {k: v for k, v in body.items() if k in _SETTINGS_KEYS}
    with _SETTINGS_LOCK:
        merged = _load_settings()
        merged.update(incoming)
        try:
            _save_settings(merged)
        except Exception as e:  # noqa: BLE001 - best-effort, never 500 on this
            app.logger.exception("settings save failed")
            return jsonify({"ok": False, "message": str(e),
                            "settings": merged}), 200
    return jsonify({"ok": True, "settings": merged})


# ---------------------------------------------------------------------------
# Live scoreboard
# ---------------------------------------------------------------------------
def _live_enabled() -> bool:
    return client.source_pref != "demo" and LocalAuth.available()


def _attach_encounters(board: dict) -> dict:
    """Tag each player with their cross-session encounter tally (or null).

    Live boards get {withCount, againstCount} from the ledger; demo boards get
    encounter=null so the UI behaves identically without VALORANT running.
    """
    is_live = board.get("source") == "local"
    for p in board.get("players") or []:
        if not isinstance(p, dict):
            continue
        p["encounter"] = encounter_log.encounter_for(p.get("puuid")) if is_live else None
    return board


def _client_notice() -> dict:
    """Friendly hint shown on the website/CLI when the local client can't be read.
    Distinguishes 'game not running' from 'running but unreadable' (restart)."""
    if not LocalAuth.available():
        return {"level": "info", "action": "open_game",
                "message": "Open VALORANT to see live ranks, parties and stats."}
    return {"level": "warn", "action": "restart_game",
            "message": "Couldn't read VALORANT — please restart your game "
                       "(close it completely and relaunch), then try again."}


def build_live(seed: int = 7, want_state: str | None = None) -> dict:
    """Live local-client scoreboard, falling back to a demo lobby/match."""
    notice = None
    if _live_enabled():
        try:
            board = live_match.LiveMatch(LocalAuth()).build_scoreboard(
                include_stats=os.getenv("LIVE_INCLUDE_STATS", "true").lower() != "false"
            )
            board.setdefault("sourceDetail", "Local VALORANT client")
            # Fold this board into the cross-session encounter ledger, then tag
            # each row with its tally — best-effort, never break the response.
            try:
                encounter_log.record_board(board)
                _attach_encounters(board)
            except Exception:  # noqa: BLE001
                app.logger.exception("encounter logging failed")
            return board
        except Exception as e:  # noqa: BLE001
            app.logger.exception("live scoreboard failed")
            notice = _client_notice()  # game running but unreadable -> restart
            if client.source_pref == "local":
                return {"state": "OFFLINE", "stateLabel": "Offline", "source": "local",
                        "error": str(e), "players": [], "teams": {}, "parties": [],
                        "notice": notice}
    elif client.source_pref != "demo" and not LocalAuth.available():
        # auto mode, game not running: show demo but hint to open the game.
        notice = _client_notice()
    # Demo fallback — `?state=menus` previews the lobby view.
    board = (sample_match.generate_lobby(seed)
             if (want_state or "").lower() == "menus"
             else sample_match.generate(seed))
    board = _attach_encounters(board)
    if notice:
        board["notice"] = notice
    return board


@app.get("/api/state")
def state():
    """Lightweight state probe (used for cheap polling)."""
    if _live_enabled():
        try:
            lm = live_match.LiveMatch(LocalAuth())
            st = lm.game_state(lm._presences())
            return jsonify({"state": st, "stateLabel": STATES.get(st, st), "source": "local"})
        except Exception as e:  # noqa: BLE001
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


@app.get("/api/encounters")
def encounters():
    """Cross-session ledger: every player seen, most-encountered first."""
    return jsonify({"players": encounter_log.get_all()})


@app.get("/api/encounters/<puuid>")
def encounter(puuid: str):
    """One player's full encounter record (or null if never seen)."""
    return jsonify(encounter_log.get_one(puuid.strip()))


@app.get("/api/match/<match_id>")
def match(match_id: str):
    """Full scoreboard for one past game (profile drill-in)."""
    subject = request.args.get("subject")
    if _live_enabled():
        try:
            data = live_match.LiveMatch(LocalAuth()).match_detail(match_id, subject)
            if not data.get("error"):
                return jsonify(data)
        except Exception:  # noqa: BLE001
            app.logger.exception("match detail failed")
    return jsonify(sample_match.match_detail(match_id, subject))


@app.get("/api/debug/reveal")
def debug_reveal():
    """Diagnose whether Incognito names are recoverable from match history."""
    if not _live_enabled():
        return jsonify({"error": "Live client not available — open VALORANT."}), 400
    try:
        return jsonify(live_match.LiveMatch(LocalAuth()).diagnose_reveal())
    except Exception as e:  # noqa: BLE001
        app.logger.exception("debug reveal failed")
        return jsonify({"error": str(e)}), 500


@app.get("/api/profile/<puuid>")
def profile(puuid: str):
    """Click-through player profile: recent games, teammates, averages."""
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
                data = None  # nothing live — fall through to demo
        except Exception:  # noqa: BLE001
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
    except Exception as e:  # noqa: BLE001
        app.logger.exception("player payload failed")
        return jsonify({"error": f"Failed to build player profile: {e}"}), 500

    _CACHE[puuid] = (now, payload)
    return jsonify(payload)


@app.get("/api/region")
def region():
    """Auto-detected region + the selectable list (for the UI dropdown)."""
    detected = None
    if LocalAuth.available():
        try:
            detected = LocalAuth().shard
        except Exception:  # noqa: BLE001
            detected = None
    return jsonify({"detected": detected, "regions": REGIONS})


@app.post("/api/dodge")
def dodge():
    body = request.get_json(silent=True) or {}
    result = client.dodge(dry_run=bool(body.get("dryRun", True)),
                          region=body.get("region"))
    return jsonify(result), (200 if result.get("ok") else 400)


@app.post("/api/instalock")
def instalock():
    """One-shot lock attempt (used for a quick dry-run test)."""
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
    """Arm the auto-instalock loop (waits for agent select, then locks).

    Accepts an optional ``perMap`` dict {mapName: agentName} that overrides the
    default ``agent`` when the detected map matches.
    """
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
    """Equipped weapon skins for a player in the CURRENT match (from the live board)."""
    try:
        board = build_live(7, None)
        for p in board.get("players") or []:
            if p.get("puuid") == puuid:
                return p.get("weapons") or []
    except Exception:  # noqa: BLE001
        pass
    return []


def handle_data_request(req_type: str, params: dict | None) -> dict:
    """
    On-demand data for the hosted ProfileModal — past games, match detail and the
    equipped-skins inventory — served over the WebSocket/Ably transport. The
    hosted page can't reach the localhost-only /api/* routes, so the drill-in
    data (which is too big to ride every live-state push) is fetched on demand.
    Mirrors the /api/profile, /api/match and /api/encounters routes; read-only.
    """
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
                except Exception:  # noqa: BLE001
                    app.logger.exception("transport profile failed")
            if data is None:
                data = sample_match.career(puuid)
            out = dict(data)
            # Fold in the current-match skins + the cross-session encounter so the
            # modal has everything (incl. the skins stripped from the live board)
            # in one round-trip.
            out["weapons"] = _current_weapons(puuid)
            try:
                out["encounter"] = encounter_log.get_one(puuid)
            except Exception:  # noqa: BLE001
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
                except Exception:  # noqa: BLE001
                    app.logger.exception("transport match failed")
            return sample_match.match_detail(match_id, subject)

        if req_type == "encounter":
            return encounter_log.get_one((params.get("puuid") or "").strip()) or {}
    except Exception as e:  # noqa: BLE001
        return {"error": f"request failed: {e}"}
    return {"error": f"unknown request '{req_type}'"}


def _start_ws_bridge() -> None:
    """
    Start the local WebSocket bridge + Ably remote controller alongside Flask.

    The hosted frontend (served from FRONTEND_URL) connects back to this socket
    on loopback; commands flow through the shared CommandRouter. All URLs come
    from environment variables — nothing is hardcoded to a production domain.
    """
    import ws_server
    import scout_commands
    import remote_ably

    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000").rstrip("/")
    ws_port = int(os.getenv("WS_PORT", "7878"))
    token_endpoint = os.getenv("ABLY_TOKEN_ENDPOINT",
                               f"{frontend_url}/api/ably-token")

    def ws_state_provider() -> dict:
        # Same board the existing dashboard consumes, plus a few extras the
        # hosted page can no longer fetch over the (localhost-only) /api proxy:
        #   instalock           - live worker status (arming/locked) for the panel
        #   agents              - the static catalogue for the instalock grid
        #   liveInstalockEnabled- whether live locking is allowed (vs dry-run only)
        # `agents` is constant, so it never adds churn to the state diff.
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
    remote_controller.attach_router(router)

    try:
        ws_server.start(board_provider=ws_state_provider, command_router=router,
                        frontend_url=frontend_url, ws_port=ws_port,
                        remote_controller=remote_controller,
                        request_handler=handle_data_request)
    except Exception as e:  # noqa: BLE001 - never block Flask on the bridge
        app.logger.exception("WebSocket bridge failed to start")
        print(f"[app] WebSocket bridge unavailable: {e}", flush=True)


if __name__ == "__main__":
    port = int(os.getenv("BACKEND_PORT", os.getenv("PORT", "5000")))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    discord_presence.maybe_start()   # region auto-detected from the local client
    # The Flask debug reloader runs this module twice; only start the bridge in
    # the reloader's child (or when the reloader is off) so the port binds once.
    if not debug or os.getenv("WERKZEUG_RUN_MAIN") == "true":
        _start_ws_bridge()
    print(f"[app] Valorant Scout API on http://127.0.0.1:{port}  "
          f"(source={client.source_pref}, key={'set' if client.api_key else 'unset'})",
          flush=True)
    app.run(host="127.0.0.1", port=port, debug=debug)
