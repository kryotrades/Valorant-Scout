"""
ws_server.py
============
Local WebSocket bridge for the hosted Valorant Scout frontend.

The Next.js site is served from FRONTEND_URL (Vercel / a custom domain) and can
no longer reach the desktop app through the old same-origin ``/api/*`` proxy. So
the desktop app exposes a WebSocket on loopback that the hosted page connects
back to:

    FRONTEND_URL site  →  ws://127.0.0.1:<WS_PORT>  →  this app  →  VALORANT client

Security model (all enforced here):
  * binds ONLY to 127.0.0.1 — never 0.0.0.0
  * validates the browser ``Origin`` against an allow-list built from FRONTEND_URL
  * requires a valid session token (rotated every launch) as the FIRST message,
    within 5 seconds, or the socket is closed
  * answers the Chrome/Edge Private-Network-Access preflight so a public HTTPS
    page is allowed to connect to a private 127.0.0.1 address
  * every command goes through scout_commands.CommandRouter (validated, rate
    limited, de-duplicated)

State is pushed only when it actually changes (the provider is polled, the JSON
is diffed); a 30s JSON heartbeat keeps the socket warm.

No lockfile contents, auth tokens, entitlements or raw headers are ever sent —
only the same normalized board the existing dashboard already consumes.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import threading
import time
import urllib.request
import webbrowser
from http import HTTPStatus
from urllib.parse import urlparse

# Pin to the stable legacy asyncio server API (consistent process_request /
# extra_headers signature across websockets 12.x and 13.x).
try:
    from websockets.legacy.server import serve as _ws_serve
    from websockets.exceptions import ConnectionClosed
except Exception as e:  # noqa: BLE001 - surfaced clearly at startup
    raise RuntimeError(
        "The 'websockets' package (>=12,<14) is required for local WebSocket "
        "mode. Install it with: pip install 'websockets>=12,<14'"
    ) from e


def _log(msg: str) -> None:
    print(f"[ws] {msg}", flush=True)


# Rotated every launch (see start()).
SESSION_TOKEN: str = ""
# Allow-list built from FRONTEND_URL (see _build_allowed_origins).
ALLOWED_ORIGINS: set[str] = set()
_FRONTEND_URL = "http://localhost:3000"

# Connected, authenticated client sockets.
_CLIENTS: set = set()
_LOOP: asyncio.AbstractEventLoop | None = None


def _build_allowed_origins(frontend_url: str) -> set[str]:
    frontend_url = (frontend_url or "http://localhost:3000").rstrip("/")
    origins = {
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        frontend_url,
    }
    parsed = urlparse(frontend_url)
    if parsed.scheme == "https" and parsed.hostname and not parsed.hostname.startswith("www."):
        origins.add(f"https://www.{parsed.hostname}")
    return origins


def dashboard_url(frontend_url: str, ws_port: int) -> str:
    return (f"{frontend_url.rstrip('/')}/dashboard"
            f"?mode=local&port={ws_port}&s={SESSION_TOKEN}")


# ---------------------------------------------------------------------------
# Handshake gate: origin validation + Private Network Access preflight
# ---------------------------------------------------------------------------
def _process_request(path, request_headers):
    """
    Runs for every incoming request before the WebSocket upgrade.

    * Chrome/Edge send a Private-Network-Access preflight before letting a
      public HTTPS page open a socket to 127.0.0.1. It carries the
      ``Access-Control-Request-Private-Network`` header — we answer it 200 with
      the matching ACA headers. (This is a compatibility measure only; the real
      security is the origin check + token auth on the socket itself.)
    * Otherwise we reject any non-allow-listed Origin with 403 before the
      upgrade completes.

    Returning ``None`` lets the handshake proceed.
    """
    try:
        get = request_headers.get
    except AttributeError:  # pragma: no cover - defensive
        return None

    if get("Access-Control-Request-Private-Network"):
        origin = get("Origin") or _FRONTEND_URL
        headers = [
            ("Access-Control-Allow-Origin", origin),
            ("Access-Control-Allow-Private-Network", "true"),
            ("Access-Control-Allow-Headers", "*"),
            ("Access-Control-Allow-Methods", "GET, OPTIONS"),
            ("Content-Length", "0"),
        ]
        return (HTTPStatus.OK, headers, b"")

    origin = get("Origin")
    if origin is not None and origin not in ALLOWED_ORIGINS:
        _log(f"rejected origin: {origin}")
        return (HTTPStatus.FORBIDDEN,
                [("Content-Type", "text/plain"), ("Content-Length", "16")],
                b"Forbidden origin")
    return None


# ---------------------------------------------------------------------------
# Messaging helpers
# ---------------------------------------------------------------------------
async def _safe_send(ws, obj) -> bool:
    try:
        await ws.send(json.dumps(obj, default=str))
        return True
    except Exception:  # noqa: BLE001 - socket gone
        return False


def _parse(raw) -> dict:
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


async def _broadcast(obj) -> None:
    if not _CLIENTS:
        return
    payload = json.dumps(obj, default=str)
    dead = []
    for ws in list(_CLIENTS):
        try:
            await ws.send(payload)
        except Exception:  # noqa: BLE001
            dead.append(ws)
    for ws in dead:
        _CLIENTS.discard(ws)


# ---------------------------------------------------------------------------
# Server (runs in its own thread + event loop)
# ---------------------------------------------------------------------------
def start(*, board_provider, command_router, frontend_url: str, ws_port: int,
          remote_controller=None, request_handler=None,
          poll_interval: float | None = None,
          open_dashboard: bool = True) -> str:
    """
    Launch the WebSocket server in a daemon thread and return the session token.

    board_provider() -> dict   current dashboard/live board (same shape as /api/live)
    command_router            scout_commands.CommandRouter
    """
    global SESSION_TOKEN, ALLOWED_ORIGINS, _FRONTEND_URL

    SESSION_TOKEN = secrets.token_urlsafe(32)
    _FRONTEND_URL = (frontend_url or "http://localhost:3000").rstrip("/")
    ALLOWED_ORIGINS = _build_allowed_origins(_FRONTEND_URL)
    interval = float(poll_interval if poll_interval is not None
                     else os.getenv("WS_STATE_POLL", "2.0"))

    def _run():
        global _LOOP
        loop = asyncio.new_event_loop()
        _LOOP = loop
        asyncio.set_event_loop(loop)

        async def handler(websocket):
            # Defence in depth: re-check origin on the upgraded socket too.
            origin = websocket.request_headers.get("Origin")
            if origin is not None and origin not in ALLOWED_ORIGINS:
                await websocket.close(code=4403, reason="Forbidden origin")
                return

            client_id = "ws:" + secrets.token_urlsafe(8)

            # First message MUST be auth, within 5 seconds.
            try:
                raw = await asyncio.wait_for(websocket.recv(), timeout=5.0)
            except asyncio.TimeoutError:
                await _safe_send(websocket, {"type": "auth_error",
                                             "message": "Auth timeout"})
                await websocket.close(code=4401, reason="Auth timeout")
                return
            except ConnectionClosed:
                return

            msg = _parse(raw)
            if msg.get("type") != "auth" or msg.get("token") != SESSION_TOKEN:
                await _safe_send(websocket, {"type": "auth_error",
                                             "message": "Invalid token"})
                await websocket.close(code=4401, reason="Invalid token")
                return

            await _safe_send(websocket, {"type": "auth_ok"})
            _CLIENTS.add(websocket)

            # Push the current state immediately so the dashboard renders fast.
            try:
                board = await loop.run_in_executor(None, board_provider)
                await _safe_send(websocket, {"type": "state", "data": board})
            except Exception:  # noqa: BLE001
                pass

            try:
                async for raw in websocket:
                    m = _parse(raw)
                    mtype = m.get("type")
                    if mtype == "pong":
                        continue
                    if mtype == "ping":
                        await _safe_send(websocket, {"type": "pong"})
                        continue
                    if mtype == "request" and request_handler is not None:
                        # Read-only on-demand data (profile / match / encounter)
                        # for the dashboard's drill-in modal.
                        rtype = m.get("request")
                        params = m.get("params") or {}
                        rid = m.get("id")
                        try:
                            data = await loop.run_in_executor(
                                None,
                                lambda r=rtype, p=params: request_handler(r, p))
                            await _safe_send(websocket, {"type": "response",
                                                         "id": rid, "ok": True,
                                                         "data": data})
                        except Exception as e:  # noqa: BLE001
                            await _safe_send(websocket, {"type": "response",
                                                         "id": rid, "ok": False,
                                                         "error": str(e)})
                        continue
                    if mtype == "command":
                        cmd = m.get("command")
                        payload = m.get("payload") or {}
                        cid = m.get("id")
                        result = await loop.run_in_executor(
                            None,
                            lambda c=cmd, p=payload, i=cid: command_router.execute(
                                client_id=client_id, command=c, payload=p,
                                command_id=i))
                        ack = {"type": "command_ack", "id": cid,
                               "ok": bool(result.get("ok")),
                               "message": result.get("message", "")}
                        for k in ("remoteUrl", "remoteSessionId", "side", "map",
                                  "status", "agent", "configured", "perMap",
                                  "rateLimited", "dedup"):
                            if k in result:
                                ack[k] = result[k]
                        await _safe_send(websocket, ack)
            except ConnectionClosed:
                pass
            finally:
                _CLIENTS.discard(websocket)

        async def _broadcast_loop():
            last = None
            while True:
                try:
                    board = await loop.run_in_executor(None, board_provider)
                    data_json = json.dumps(board, sort_keys=True, default=str)
                    if data_json != last:
                        last = data_json
                        await _broadcast({"type": "state", "data": board})
                        if remote_controller is not None:
                            try:
                                remote_controller.publish_state(board)
                            except Exception:  # noqa: BLE001
                                pass
                except Exception:  # noqa: BLE001 - never let the loop die
                    pass
                await asyncio.sleep(interval)

        async def _heartbeat_loop():
            while True:
                await asyncio.sleep(30)
                await _broadcast({"type": "ping"})

        # Headers added to the successful (101) handshake response. Includes the
        # Private-Network-Access grant for browsers that check it before/while
        # connecting from a public HTTPS page to 127.0.0.1.
        handshake_headers = [
            ("Access-Control-Allow-Origin", _FRONTEND_URL),
            ("Access-Control-Allow-Private-Network", "true"),
        ]

        async def _main():
            async with _ws_serve(
                handler, "127.0.0.1", ws_port,
                process_request=_process_request,
                extra_headers=handshake_headers,
                ping_interval=None,          # we run our own JSON heartbeat
                max_queue=16,
            ):
                _log(f"listening on ws://127.0.0.1:{ws_port} "
                     f"(origins: {', '.join(sorted(ALLOWED_ORIGINS))})")
                await asyncio.gather(_broadcast_loop(), _heartbeat_loop())

        try:
            loop.run_until_complete(_main())
        except Exception as e:  # noqa: BLE001
            _log(f"server stopped: {e}")

    threading.Thread(target=_run, daemon=True, name="scout-ws").start()

    url = dashboard_url(_FRONTEND_URL, ws_port)
    print(f"\n[scout] Open dashboard: {url}\n", flush=True)
    if open_dashboard and os.getenv("SCOUT_NO_BROWSER", "").strip() not in ("1", "true"):
        _spawn_opener(_FRONTEND_URL, url)

    return SESSION_TOKEN


def _spawn_opener(frontend_url: str, url: str) -> None:
    """Open the dashboard once the frontend is reachable (best-effort)."""
    def _wait_and_open():
        target = frontend_url.rstrip("/") + "/"
        deadline = time.time() + 90
        opened = False
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(target, timeout=2) as r:
                    if r.status < 500:
                        opened = True
                        break
            except Exception:  # noqa: BLE001 - not up yet / external host
                time.sleep(1.0)
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
        if not opened:
            _log("opened dashboard (frontend health unconfirmed — "
                 "if the page errors, retry once it's up).")

    threading.Thread(target=_wait_and_open, daemon=True, name="scout-open").start()
