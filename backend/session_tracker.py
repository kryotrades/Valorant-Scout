from __future__ import annotations

import json
import os
import tempfile
import threading
import time

import encounter_log

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_PATH = os.path.join(_DATA_DIR, "session.json")

_LOCK = threading.Lock()
_IDLE_RESET = 6 * 3600
_RECAP_TTL = 600.0
_MAX_POINTS = 40

_STATE = {
    "prev_state": None,
    "ingame_board": None,
    "recap": None,
    "recap_at": 0.0,
    "recorded": set(),
}

def _load_session() -> dict:
    try:
        with open(_PATH, encoding="utf-8") as fh:
            s = json.load(fh)
        if isinstance(s, dict) and isinstance(s.get("points"), list):
            return s
    except Exception:
        pass
    return {"startedAt": int(time.time()), "lastAt": int(time.time()), "points": []}

def _save_session() -> None:
    pass
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=_DATA_DIR, prefix=".session-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(_SESSION, fh, separators=(",", ":"))
            os.replace(tmp, _PATH)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    except Exception:
        pass

_SESSION = _load_session()
_STATE["recorded"] = {p.get("matchId") for p in _SESSION["points"] if p.get("matchId")}

def _self_rr(lm, match_id: str) -> dict | None:
    pass
    try:
        cu = lm.auth.pd_get(
            f"/mmr/v1/players/{lm.self_puuid}/competitiveupdates"
            f"?startIndex=0&endIndex=5&queue=competitive")
        for m in (cu or {}).get("Matches", []) or []:
            if m.get("MatchID") == match_id:
                return {
                    "delta": m.get("RankedRatingEarned"),
                    "tier": m.get("TierAfterUpdate"),
                    "rr": m.get("RankedRatingAfterUpdate"),
                }
    except Exception:
        pass
    return None

def _build_recap(lm, ingame_board: dict) -> dict | None:
    pass
    match_id = ingame_board.get("matchId")
    if not match_id or match_id == "lobby":
        return None
    detail = lm.match_detail(match_id, lm.self_puuid)
    if not isinstance(detail, dict) or detail.get("error"):
        return None
    players = detail.get("players") or []
    you = next((p for p in players if p.get("isSubject")), None)
    if not you:
        return None
    mvp = players[0] if players else None
    team_mvp = next((p for p in players if p.get("team") == you.get("team")), None)

    self_row = next((p for p in ingame_board.get("players") or []
                     if p.get("isSelf")), {})
    rr = (_self_rr(lm, match_id)
          if (ingame_board.get("mode") or "").lower() == "competitive" else None)
    return {
        "matchId": match_id,
        "map": detail.get("map"),
        "mode": detail.get("mode"),
        "result": detail.get("result"),
        "scores": detail.get("scores"),
        "mvp": mvp,
        "teamMvp": team_mvp if team_mvp is not mvp else None,
        "you": you,
        "yourAvgKd": self_row.get("kd"),
        "rrDelta": (rr or {}).get("delta"),
        "tierAfter": (rr or {}).get("tier"),
        "rrAfter": (rr or {}).get("rr"),
        "players": players[:3],
        "at": int(time.time()),
    }

def _push_session_point(recap: dict) -> None:
    pass
    now = int(time.time())
    if now - _SESSION.get("lastAt", now) > _IDLE_RESET:
        _SESSION["startedAt"] = now
        _SESSION["points"] = []
        _STATE["recorded"] = set()
    _SESSION["lastAt"] = now
    _SESSION["points"].append({
        "matchId": recap["matchId"],
        "ts": now,
        "map": recap.get("map"),
        "result": recap.get("result"),
        "delta": recap.get("rrDelta"),
        "tier": recap.get("tierAfter"),
        "rr": recap.get("rrAfter"),
    })
    _SESSION["points"] = _SESSION["points"][-_MAX_POINTS:]
    _save_session()

def observe(board: dict, lm) -> None:
    pass
    try:
        state = board.get("state")
        prev = _STATE["prev_state"]
        _STATE["prev_state"] = state

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
            if match_id in _STATE["recorded"]:
                return
            _STATE["recorded"].add(match_id)

        def _finish():
            try:
                recap = _build_recap(lm, snap)
                if not recap:
                    return
                _STATE["recap"] = recap
                _STATE["recap_at"] = time.time()
                won = {"Victory": True, "Defeat": False}.get(recap.get("result"))
                try:
                    encounter_log.record_result(snap, won)
                except Exception:
                    pass

                if (recap.get("mode") or "").lower() == "competitive":
                    with _LOCK:
                        _push_session_point(recap)
            except Exception:
                pass

        threading.Thread(target=_finish, daemon=True,
                         name=f"recap-{str(match_id)[:8]}").start()
    except Exception:
        pass

def current_recap() -> dict | None:
    pass
    r = _STATE.get("recap")
    if r and time.time() - _STATE.get("recap_at", 0) < _RECAP_TTL:
        return r
    return None

def attach(board: dict) -> dict:
    pass
    recap = _STATE.get("recap")
    if recap and time.time() - _STATE.get("recap_at", 0) < _RECAP_TTL            and board.get("state") == "MENUS":
        board["recap"] = recap
    with _LOCK:
        pts = list(_SESSION["points"])
    if pts:
        net = sum(p.get("delta") or 0 for p in pts)
        board["session"] = {"startedAt": _SESSION.get("startedAt"),
                            "net": net, "points": pts}
    return board
