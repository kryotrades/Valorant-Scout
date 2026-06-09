"""
scout_commands.py
=================
Shared, transport-agnostic command router for the hosted-frontend bridge.

Both the local WebSocket server (ws_server) and the Ably remote bridge
(remote_ably) feed every inbound command through ONE ``CommandRouter`` so the
security guarantees are identical regardless of where the command came from:

  * server-side validation        — only the known commands run; payloads checked
  * rate limiting                 — max 5 commands / 10s per client
  * de-duplication                — a repeated command id is ignored once seen

The router reuses the existing behaviour exactly:

  instalock  -> InstalockWorker.start / .stop  (or one-shot RiotClient.instalock)
  dodge      -> RiotClient.dodge
  check_side -> read the current live board's ``side``/``map``
  enable_remote / disable_remote -> RemoteController (Ably)

Nothing here ever touches the lockfile, auth tokens or raw headers — it only
calls the same high-level helpers the Flask routes already expose.
"""

from __future__ import annotations

import collections
import threading
import time

from agents import resolve_agent

# The complete set of commands accepted from ANY transport. Anything else is
# rejected before it can reach a handler.
ALLOWED_COMMANDS = {
    "instalock",
    "dodge",
    "check_side",
    "enable_remote",
    "disable_remote",
}

# Rate limit: at most RATE_LIMIT commands inside RATE_WINDOW seconds per client.
RATE_LIMIT = 5
RATE_WINDOW = 10.0
# How long a command id is remembered for de-duplication (seconds) and the cap
# on remembered ids per client so the set can't grow unbounded.
DEDUP_TTL = 120.0
DEDUP_MAX = 256


class CommandRouter:
    """Validate, throttle, de-duplicate and execute Scout commands."""

    def __init__(self, *, instalock_worker, riot_client, board_provider,
                 remote_controller=None):
        self.instalock_worker = instalock_worker
        self.riot_client = riot_client
        # board_provider() -> current dashboard/live board dict (for check_side).
        self.board_provider = board_provider
        self.remote_controller = remote_controller

        self._lock = threading.Lock()
        # client_id -> deque[timestamps] for the sliding-window rate limit.
        self._calls: dict[str, collections.deque] = collections.defaultdict(collections.deque)
        # client_id -> OrderedDict[command_id -> first_seen_ts] for de-dup.
        self._seen: dict[str, "collections.OrderedDict[str, float]"] = \
            collections.defaultdict(collections.OrderedDict)

    # -- guards -------------------------------------------------------------
    def _rate_ok(self, client_id: str) -> bool:
        now = time.time()
        dq = self._calls[client_id]
        while dq and now - dq[0] > RATE_WINDOW:
            dq.popleft()
        if len(dq) >= RATE_LIMIT:
            return False
        dq.append(now)
        return True

    def _is_duplicate(self, client_id: str, command_id) -> bool:
        if not command_id:
            return False
        now = time.time()
        seen = self._seen[client_id]
        # Expire stale ids first.
        for k in [k for k, ts in seen.items() if now - ts > DEDUP_TTL]:
            seen.pop(k, None)
        if command_id in seen:
            return True
        seen[command_id] = now
        while len(seen) > DEDUP_MAX:
            seen.popitem(last=False)
        return False

    # -- entry point --------------------------------------------------------
    def execute(self, *, client_id: str, command: str, payload: dict | None,
                command_id=None) -> dict:
        """Run one command. Returns a JSON-serialisable ack dict.

        Always returns ``{"ok": bool, "message": str, ...}``; never raises.
        """
        payload = payload if isinstance(payload, dict) else {}

        # Validation + throttling under the lock; execution (which does network
        # I/O) happens afterwards so we never hold the lock across a request.
        with self._lock:
            if command not in ALLOWED_COMMANDS:
                return {"ok": False, "message": f"Unknown command '{command}'."}
            if self._is_duplicate(client_id, command_id):
                return {"ok": False, "dedup": True,
                        "message": "Duplicate command ignored."}
            if not self._rate_ok(client_id):
                return {"ok": False, "rateLimited": True,
                        "message": "Rate limit exceeded — max 5 commands / 10s."}

        try:
            if command == "instalock":
                return self._instalock(payload)
            if command == "dodge":
                return self._dodge(payload)
            if command == "check_side":
                return self._check_side(payload)
            if command == "enable_remote":
                return self._enable_remote(payload)
            if command == "disable_remote":
                return self._disable_remote(payload)
        except Exception as e:  # noqa: BLE001 - never propagate to the socket
            return {"ok": False, "message": f"Command failed: {e}"}
        return {"ok": False, "message": f"Unhandled command '{command}'."}

    # -- handlers -----------------------------------------------------------
    def _instalock(self, payload: dict) -> dict:
        """
        Arm/stop the auto-instalock loop, or fire a one-shot lock.

        payload:
          action: "start" (default) | "stop" | "once"
          agent:  agent name/uuid (required for start/once)
          mode:   "lock" | "select"
          delay:  seconds before locking (start)
          dryRun: default True — safe; reports only, no game input
          region: optional region pin
          perMap: optional {map: agent} overrides (start)
        """
        action = (payload.get("action") or "start").lower()
        if action == "stop":
            self.instalock_worker.stop()
            return {"ok": True, "status": "stopped", "message": "Stopped."}

        agent = payload.get("agent")
        if not agent:
            return {"ok": False, "message": "Field 'agent' is required."}
        ag = resolve_agent(agent)
        if not ag:
            return {"ok": False, "message": f"Unknown agent '{agent}'."}

        mode = (payload.get("mode") or "lock").lower()
        dry_run = bool(payload.get("dryRun", True))
        region = payload.get("region")
        delay = payload.get("delay", 0)
        per_map = payload.get("perMap") if isinstance(payload.get("perMap"), dict) else None

        if action == "once":
            r = self.riot_client.instalock(agent, mode=mode, dry_run=dry_run,
                                           region=region)
            return {**r, "ok": bool(r.get("ok"))}

        # action == "start": arm the background loop (or dry-run preview).
        if dry_run:
            for mapn, name in (per_map or {}).items():
                if not resolve_agent(name):
                    return {"ok": False,
                            "message": f"Unknown agent '{name}' for map '{mapn}'."}
            return {"ok": True, "status": "dry-run", "agent": ag["name"],
                    "perMap": per_map or {},
                    "message": f"DRY-RUN: would {mode} {ag['name']} when agent "
                               f"select starts. Turn dry-run OFF to auto-lock."}
        r = self.instalock_worker.start(agent, mode=mode, delay=delay,
                                        region=region, per_map=per_map)
        msg = r.get("message") or ("Armed — waiting for agent select…"
                                   if r.get("ok") else "Couldn't start.")
        return {**r, "ok": bool(r.get("ok")), "message": msg}

    def _dodge(self, payload: dict) -> dict:
        dry_run = bool(payload.get("dryRun", True))
        region = payload.get("region")
        r = self.riot_client.dodge(dry_run=dry_run, region=region)
        return {**r, "ok": bool(r.get("ok"))}

    def _check_side(self, _payload: dict) -> dict:
        board = self.board_provider() or {}
        side = board.get("side")
        mapn = board.get("map")
        if side:
            return {"ok": True, "side": side, "map": mapn,
                    "message": f"You are {side}" + (f" on {mapn}" if mapn else "") + "."}
        return {"ok": True, "side": None, "map": mapn,
                "message": "Not in agent select / a match."}

    # -- remote (Ably) ------------------------------------------------------
    def _enable_remote(self, _payload: dict) -> dict:
        if self.remote_controller is None:
            return {"ok": False, "configured": False,
                    "message": "Remote mode is not configured. Set ABLY_API_KEY "
                               "in the frontend/Vercel environment."}
        return self.remote_controller.enable()

    def _disable_remote(self, _payload: dict) -> dict:
        if self.remote_controller is None:
            return {"ok": True, "message": "Remote mode was not active."}
        return self.remote_controller.disable()
