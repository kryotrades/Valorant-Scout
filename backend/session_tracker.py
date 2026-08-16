from __future__ import annotations


import json
import os
import tempfile
import threading
import time
import uuid

import encounter_log
import history

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_PATH = os.path.join(_DATA_DIR, "sessions.json")
_LEGACY_PATH = os.path.join(_DATA_DIR, "session.json")
_LOCK = threading.RLock()
_RECAP_TTL = 600.0
_MAX_POINTS = 40
_MAX_ARCHIVE = 100

_STATE = {
    "prev_state": None, "ingame_board": None, "recap": None,
    "recap_at": 0.0, "recorded": set(), "generation": 0,
    "active_puuid": None,
}


def _empty_store() -> dict:
    return {"version": 2, "accounts": {}, "discardedLegacySessions": 0}


def _normalise_store(raw: object) -> dict:
    if isinstance(raw, dict) and raw.get("version") == 2 and isinstance(raw.get("accounts"), dict):
        return raw
    out = _empty_store()
    if isinstance(raw, dict) and isinstance(raw.get("points"), list):
        puuid = raw.get("puuid")
        if puuid:
            started = int(raw.get("startedAt") or time.time())
            session = {
                "id": f"legacy-{started}", "startedAt": started,
                "lastAt": int(raw.get("lastAt") or started), "goal": None,
                "points": raw.get("points", [])[-_MAX_POINTS:],
            }
            out["accounts"][str(puuid)] = {"active": session, "archive": []}
        elif raw.get("points"):
            out["discardedLegacySessions"] = 1
    return out


def _load() -> dict:
    for path in (_PATH, _LEGACY_PATH):
        try:
            with open(path, encoding="utf-8") as fh:
                return _normalise_store(json.load(fh))
        except FileNotFoundError:
            continue
        except Exception:
            return _empty_store()
    return _empty_store()


def _save() -> None:
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=_DATA_DIR, prefix=".sessions-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(_STORE, fh, separators=(",", ":"))
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


def _account(puuid: str) -> dict:
    return _STORE.setdefault("accounts", {}).setdefault(str(puuid), {"active": None, "archive": []})


def _summary(points: list[dict]) -> dict:
    rated = [p for p in points if p.get("result") in ("Victory", "Defeat")]
    wins = sum(p.get("result") == "Victory" for p in rated)
    losses = sum(p.get("result") == "Defeat" for p in rated)
    deltas = [p.get("delta") for p in points if isinstance(p.get("delta"), (int, float))]
    latest = next((p for p in reversed(points) if p.get("tier") is not None), None)
    best = max(points, key=lambda p: p.get("acs") or -1, default=None)
    worst = min(points, key=lambda p: p.get("delta") if isinstance(p.get("delta"), (int, float)) else 9999, default=None)
    return {
        "matches": len(rated), "wins": wins, "losses": losses,
        "winRate": round(100 * wins / len(rated)) if rated else None,
        "net": sum(deltas), "currentTier": latest.get("tier") if latest else None,
        "currentRr": latest.get("rr") if latest else None,
        "bestMatchId": best.get("matchId") if best else None,
        "worstMatchId": worst.get("matchId") if worst else None,
    }


def _session_view(session: dict | None) -> dict | None:
    if not session:
        return None
    points = list(session.get("points") or [])
    return {**session, "points": points, "summary": _summary(points)}


def _clean_goal(goal: object) -> dict | None:
    if not isinstance(goal, dict) or goal.get("type") not in {"rr", "rank", "matches", "stopLoss"}:
        return None
    kind = goal["type"]
    target = goal.get("target")
    if kind == "rank":
        target = str(target or "").strip()[:32]
        return {"type": kind, "target": target} if target else None
    try:
        target = max(1, min(999, int(target)))
    except (TypeError, ValueError):
        return None
    return {"type": kind, "target": target}


def list_for(puuid: str | None) -> dict:
    if not puuid:
        return {"active": None, "archive": []}
    with _LOCK:
        account = _account(str(puuid))
        return {
            "active": _session_view(account.get("active")),
            "archive": [_session_view(s) for s in reversed(account.get("archive") or [])],
        }


def start(puuid: str | None, goal: object = None, baseline: dict | None = None) -> dict:
    if not puuid:
        return {"ok": False, "message": "Open VALORANT before starting a session."}
    now = int(time.time())
    with _LOCK:
        account = _account(str(puuid))
        current = account.get("active")
        if current and current.get("points"):
            current["endedAt"] = now
            current["summary"] = _summary(current.get("points") or [])
            account.setdefault("archive", []).append(current)
            account["archive"] = account["archive"][-_MAX_ARCHIVE:]
        baseline = baseline or {}
        current = baseline.get("current") if isinstance(baseline.get("current"), dict) else baseline
        session = {
            "id": uuid.uuid4().hex, "startedAt": now, "lastAt": now,
            "goal": _clean_goal(goal), "points": [],
            "startTier": current.get("tier"),
            "startRr": current.get("rr"),
            "baseline": {key: baseline.get(key) for key in ("matches", "winRate", "avgWin", "avgLoss")
                         if baseline.get(key) is not None},
        }
        account["active"] = session
        _STATE["generation"] += 1
        _STATE["recorded"] = set()
        _save()
        return {"ok": True, "message": "Session started.", "session": _session_view(session)}


def ensure_active(puuid: str | None, baseline: dict | None = None) -> dict:
    if not puuid:
        return {"ok": False, "message": "No Riot account is active."}
    with _LOCK:
        current = _account(str(puuid)).get("active")
        if current:
            return {"ok": True, "message": "Session already active.",
                    "session": _session_view(current), "existing": True}
    return start(puuid, None, baseline)


def end(puuid: str | None) -> dict:
    if not puuid:
        return {"ok": False, "message": "No Riot account is active."}
    now = int(time.time())
    with _LOCK:
        account = _account(str(puuid))
        current = account.get("active")
        if not current:
            return {"ok": False, "message": "No session is running."}
        current["endedAt"] = now
        current["summary"] = _summary(current.get("points") or [])
        account.setdefault("archive", []).append(current)
        account["archive"] = account["archive"][-_MAX_ARCHIVE:]
        account["active"] = None
        _STATE["generation"] += 1
        _save()
        return {"ok": True, "message": "Session ended.", "session": _session_view(current)}


def delete(puuid: str | None, session_id: str | None) -> dict:
    if not puuid or not session_id:
        return {"ok": False, "message": "Session not found."}
    with _LOCK:
        account = _account(str(puuid))
        archive = account.get("archive") or []
        kept = [session for session in archive if session.get("id") != session_id]
        if len(kept) == len(archive):
            return {"ok": False, "message": "Session not found."}
        account["archive"] = kept
        _save()
        return {"ok": True, "message": "Session deleted.", "sessionId": session_id}


def reset(puuid: str | None = None, goal: object = None) -> dict:
    owner = puuid or _STATE.get("active_puuid")
    baseline = history.payload(owner).get("summary", {}) if owner else None
    result = start(owner, goal, baseline)
    if result.get("ok"):
        result["deprecated"] = True
        result["message"] = "Previous session archived. New session started."
    return result


def _self_rr(lm, match_id: str) -> dict | None:
    try:
        cu = lm.auth.pd_get(
            f"/mmr/v1/players/{lm.self_puuid}/competitiveupdates"
            f"?startIndex=0&endIndex=5&queue=competitive")
        for match in (cu or {}).get("Matches", []) or []:
            if match.get("MatchID") == match_id:
                return {"delta": match.get("RankedRatingEarned"),
                        "tier": match.get("TierAfterUpdate"),
                        "rr": match.get("RankedRatingAfterUpdate")}
    except Exception:
        pass
    return None


def _build_recap(lm, ingame_board: dict) -> dict | None:
    match_id = ingame_board.get("matchId")
    if not match_id or match_id == "lobby":
        return None
    detail = lm.match_detail(match_id, lm.self_puuid)
    if not isinstance(detail, dict) or detail.get("error"):
        return None
    detail_players = detail.get("players") or []
    board_players = {p.get("puuid"): p for p in ingame_board.get("players") or []}
    players = [{**board_players.get(p.get("puuid"), {}), **p} for p in detail_players]
    you = next((p for p in players if p.get("isSubject")), None)
    if not you:
        return None
    mvp = players[0] if players else None
    team_mvp = next((p for p in players if p.get("team") == you.get("team")), None)
    self_row = next((p for p in ingame_board.get("players") or [] if p.get("isSelf")), {})
    rr = _self_rr(lm, match_id) if (ingame_board.get("mode") or "").lower() == "competitive" else None
    return {
        "matchId": match_id, "puuid": lm.self_puuid, "riotId": you.get("name"),
        "map": detail.get("map"), "mode": detail.get("mode"),
        "result": detail.get("result"), "scores": detail.get("scores"),
        "mvp": mvp, "teamMvp": team_mvp if team_mvp is not mvp else None,
        "you": you, "yourAvgKd": self_row.get("kd"),
        "rrDelta": (rr or {}).get("delta"), "tierAfter": (rr or {}).get("tier"),
        "rrAfter": (rr or {}).get("rr"), "players": players,
        "mapSplash": detail.get("mapSplash") or ingame_board.get("mapSplash"),
        "teamStats": detail.get("teamStats") or ingame_board.get("teamStats"),
        "at": int(time.time()),
    }


def _point_from_recap(recap: dict) -> dict:
    you = recap.get("you") or {}
    return {
        "matchId": recap.get("matchId"), "puuid": recap.get("puuid"),
        "riotId": recap.get("riotId"), "ts": recap.get("at") or int(time.time()),
        "map": recap.get("map"), "mode": recap.get("mode"),
        "result": recap.get("result"), "delta": recap.get("rrDelta"),
        "tier": recap.get("tierAfter"), "rr": recap.get("rrAfter"),
        "agent": you.get("agent"), "agentPortrait": you.get("agentPortrait"),
        "kills": you.get("kills"), "deaths": you.get("deaths"),
        "assists": you.get("assists"), "kd": you.get("kd"),
        "acs": you.get("acs"), "hsPct": you.get("hsPct"),
        "scores": recap.get("scores"), "resultExact": True,
    }


def _push_session_point(recap: dict) -> None:
    owner = recap.get("puuid")
    if not owner:
        return
    point = _point_from_recap(recap)
    account = _account(str(owner))
    active = account.get("active")
    if not active:
        return
    if any(p.get("matchId") == point.get("matchId") for p in active.get("points") or []):
        return
    active.setdefault("points", []).append(point)
    active["points"] = active["points"][-_MAX_POINTS:]
    active["lastAt"] = int(time.time())
    _save()


def observe(board: dict, lm) -> None:
    try:
        state = board.get("state")
        prev = _STATE["prev_state"]
        _STATE["prev_state"] = state
        owner = getattr(lm, "self_puuid", None) or board.get("selfPuuid")
        _STATE["active_puuid"] = owner
        if state == "INGAME" and board.get("matchId"):
            _STATE["ingame_board"] = board
            return
        if state == "PREGAME":
            _STATE["recap"] = None
            return
        if state != "MENUS" or prev != "INGAME":
            return
        snap = _STATE["ingame_board"]
        _STATE["ingame_board"] = None
        if not snap:
            return
        match_id = snap.get("matchId")
        with _LOCK:
            key = (owner, match_id)
            if key in _STATE["recorded"]:
                return
            _STATE["recorded"].add(key)
            generation = _STATE["generation"]

        def finish():
            try:
                recap = _build_recap(lm, snap)
                if not recap:
                    return
                won = {"Victory": True, "Defeat": False}.get(recap.get("result"))
                encounter_log.record_result(snap, won)
                point = _point_from_recap(recap)
                history.record(point, puuid=recap.get("puuid"), riot_id=recap.get("riotId"))
                with _LOCK:
                    if generation == _STATE["generation"]:
                        _STATE["recap"] = recap
                        _STATE["recap_at"] = time.time()
                        if (recap.get("mode") or "").lower() == "competitive":
                            _push_session_point(recap)
            except Exception:
                pass

        threading.Thread(target=finish, daemon=True, name=f"recap-{str(match_id)[:8]}").start()
    except Exception:
        pass


def current_recap() -> dict | None:
    recap = _STATE.get("recap")
    if recap and time.time() - _STATE.get("recap_at", 0) < _RECAP_TTL:
        return recap
    return None


def attach(board: dict) -> dict:
    recap = current_recap()
    if recap and board.get("state") == "MENUS":
        board["recap"] = recap
    owner = board.get("selfPuuid") or _STATE.get("active_puuid")
    if owner:
        try:
            existing = list_for(owner)
            if not existing.get("active"):
                ensure_active(owner, history.payload(owner).get("summary", {}))
        except Exception:
            ensure_active(owner)
    sessions = list_for(owner)
    board["session"] = sessions.get("active")
    board["sessionArchiveCount"] = len(sessions.get("archive") or [])
    return board
