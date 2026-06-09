"""
encounter_log.py
================
Persistent "people you've met" ledger. Every player you actually share a live
match/lobby with is logged across sessions, with how often you've been WITH
(same team) or AGAINST them, plus their most recent stats snapshot.

Storage
-------
A single JSON file at backend/data/encounters.json, keyed by PUUID:

  { "<puuid>": {
      puuid, name, withCount, againstCount, lastSeen (epoch seconds),
      lastMatchId, agents (list of agent names seen), rank, peakRank, kd,
      winRate, level } }

Dedup: a player's with/against counter only increments when the board's matchId
differs from their stored `lastMatchId`, so the 5s polling of the SAME match
never double-counts. Demo boards (source != "local") are ignored entirely.

Thread-safe (a single module lock guards both the in-memory map and the file).
Loaded once on import; saved (best-effort) after every recorded board.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time

# data/ lives next to this module so it works regardless of the cwd.
_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_PATH = os.path.join(_DATA_DIR, "encounters.json")

_LOCK = threading.Lock()
_STORE: dict[str, dict] = {}
_LOADED = False


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def _load() -> None:
    """Read the ledger from disk once. Tolerates a missing/corrupt file."""
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
    except Exception:  # noqa: BLE001 - corrupt file -> start fresh, don't crash
        pass
    _LOADED = True


def _save() -> None:
    """Atomically persist the ledger. Caller holds _LOCK."""
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        # Write to a temp file in the same dir, then replace — never leaves a
        # half-written encounters.json behind on a crash/interrupt.
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
    except Exception:  # noqa: BLE001 - persistence is best-effort
        pass


# Load on import.
_load()


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------
def record_board(board: dict | None) -> None:
    """
    Fold one finalized LIVE scoreboard into the ledger.

    Only `source == "local"` boards count (demo boards are skipped). For every
    player that is NOT self:
      - if this board's matchId is NEW for that puuid (!= stored lastMatchId),
        bump withCount (same team as self) or againstCount (other team);
      - always refresh name/rank/peakRank/kd/winRate/level/agents/lastSeen and
        set lastMatchId so the same match's repeated polls don't recount.
    """
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

            # Count once per (puuid, matchId): only when this is a new match.
            if entry.get("lastMatchId") != match_id:
                if self_team is not None and p.get("team") == self_team:
                    entry["withCount"] = int(entry.get("withCount") or 0) + 1
                else:
                    entry["againstCount"] = int(entry.get("againstCount") or 0) + 1
                entry["lastMatchId"] = match_id

            # Always refresh the latest-stats snapshot.
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


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------
def get_all(limit: int = 200) -> list[dict]:
    """All ledger entries, most-encountered first (withCount + againstCount)."""
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
    """The full ledger entry for one PUUID, or None."""
    if not puuid:
        return None
    with _LOCK:
        entry = _STORE.get(puuid)
        return dict(entry) if entry else None


def encounter_for(puuid: str) -> dict | None:
    """
    Cheap {withCount, againstCount} for attaching to a live row, or None if the
    player has never been seen before.
    """
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
