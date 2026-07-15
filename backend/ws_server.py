from __future__ import annotations

import asyncio
import hmac
import json
import os
import secrets
import threading
import time
import urllib.request
import webbrowser
from http import HTTPStatus
from urllib.parse import urlparse

try:
    from websockets.legacy.server import serve as _ws_serve
    from websockets.exceptions import ConnectionClosed
except Exception as e:
    raise RuntimeError(
        "The 'websockets' package is required for local WebSocket mode. "
        "Run install.bat to repair the installation."
    ) from e

import scoutlog
from vconstants import APP_VERSION

LOG = scoutlog.get_logger("ws", "websocket")

# Bumped when the frame contract changes incompatibly. The server accepts any
# protocol in SUPPORTED_PROTOCOLS so a new hosted frontend can keep talking to
# the previous installed client generation (and vice versa).
PROTOCOL_VERSION = 1
SUPPORTED_PROTOCOLS = {1}
CAPABILITIES = ["state", "commands", "requests", "remote"]

# Structured close codes (shared contract with frontend/lib/transport.js):
#   4401 auth timeout or bad token · 4403 forbidden origin · 4406 incompatible protocol
CLOSE_AUTH = 4401
CLOSE_ORIGIN = 4403
CLOSE_PROTOCOL = 4406

def _log(msg: str) -> None:
    print(f"[ws] {msg}", flush=True)
    LOG.info("%s", msg)

SESSION_TOKEN: str = ""

ALLOWED_ORIGINS: set[str] = set()
_FRONTEND_URL = "http://localhost:3000"

_CLIENTS: set = set()
_LOOP: asyncio.AbstractEventLoop | None = None
_READY = threading.Event()
_WS_PORT: int | None = None

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

async def _process_request(path, request_headers):
    try:
        get = request_headers.get
    except AttributeError:
        return None

    origin = get("Origin")
    if origin is not None and origin not in ALLOWED_ORIGINS:
        _log(f"rejected origin: {origin}")
        return (HTTPStatus.FORBIDDEN,
                [("Content-Type", "text/plain"), ("Content-Length", "16")],
                b"Forbidden origin")

    if get("Access-Control-Request-Private-Network"):
        if origin is None:
            return (HTTPStatus.FORBIDDEN,
                    [("Content-Type", "text/plain"), ("Content-Length", "16")],
                    b"Forbidden origin")
        headers = [
            ("Access-Control-Allow-Origin", origin),
            ("Access-Control-Allow-Private-Network", "true"),
            ("Access-Control-Allow-Headers", "*"),
            ("Access-Control-Allow-Methods", "GET, OPTIONS"),
            ("Content-Length", "0"),
        ]
        return (HTTPStatus.OK, headers, b"")

    return None

def handshake_headers(path, request_headers):
    # Extra headers on the actual WS upgrade response. Only echo CORS / the
    # Private-Network grant to a TRUSTED origin; an untrusted or missing Origin
    # gets nothing (the handler still closes it with 4403).
    origin = request_headers.get("Origin")
    if origin in ALLOWED_ORIGINS:
        return [
            ("Access-Control-Allow-Origin", origin),
            ("Access-Control-Allow-Private-Network", "true"),
        ]
    return []

def is_ready() -> bool:
    return _READY.is_set()

def listening_port() -> int | None:
    return _WS_PORT if _READY.is_set() else None

async def _safe_send(ws, obj) -> bool:
    try:
        await ws.send(json.dumps(obj, default=str))
        return True
    except Exception:
        return False

def _parse(raw) -> dict:
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

async def _broadcast(obj) -> None:
    if not _CLIENTS:
        return
    payload = json.dumps(obj, default=str)
    dead = []
    for ws in list(_CLIENTS):
        try:
            await ws.send(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _CLIENTS.discard(ws)

def _self_handshake(ws_port: int, timeout: float = 6.0) -> None:
    """Prove the server actually accepts an authenticated client before anyone
    is sent to the dashboard. Raises on failure."""
    from websockets.sync.client import connect as _sync_connect

    with _sync_connect(f"ws://127.0.0.1:{ws_port}", open_timeout=timeout,
                       close_timeout=2) as ws:
        ws.send(json.dumps({"type": "auth", "token": SESSION_TOKEN,
                            "protocol": PROTOCOL_VERSION}))
        reply = json.loads(ws.recv(timeout=timeout))
        if reply.get("type") != "auth_ok":
            raise RuntimeError(f"self-handshake got {reply.get('type')!r}")

def start(*, board_provider, command_router, frontend_url: str, ws_port: int,
          remote_controller=None, request_handler=None,
          poll_interval: float | None = None,
          open_dashboard: bool = True, backend_port: int | None = None) -> str:
    """Start the bridge. Blocks until the server is bound and has passed an
    authenticated self-handshake; raises RuntimeError (VS-WS-001) otherwise.
    Only then is the token considered live / the browser opened."""
    global SESSION_TOKEN, ALLOWED_ORIGINS, _FRONTEND_URL, _WS_PORT

    _READY.clear()
    _WS_PORT = None
    SESSION_TOKEN = secrets.token_urlsafe(32)
    _FRONTEND_URL = (frontend_url or "http://localhost:3000").rstrip("/")
    ALLOWED_ORIGINS = _build_allowed_origins(_FRONTEND_URL)
    interval = float(poll_interval if poll_interval is not None
                     else os.getenv("WS_STATE_POLL", "2.0"))

    ready = threading.Event()
    boot_error: list[BaseException] = []

    def _run():
        global _LOOP, _WS_PORT
        loop = asyncio.new_event_loop()
        _LOOP = loop
        asyncio.set_event_loop(loop)

        async def handler(websocket):

            origin = websocket.request_headers.get("Origin")
            if origin is not None and origin not in ALLOWED_ORIGINS:
                await websocket.close(code=CLOSE_ORIGIN, reason="Forbidden origin")
                return

            client_id = "ws:" + secrets.token_urlsafe(8)

            try:
                raw = await asyncio.wait_for(websocket.recv(), timeout=5.0)
            except asyncio.TimeoutError:
                await _safe_send(websocket, {"type": "auth_error", "code": "timeout",
                                             "message": "Auth timeout"})
                await websocket.close(code=CLOSE_AUTH, reason="Auth timeout")
                return
            except ConnectionClosed:
                return

            msg = _parse(raw)
            if msg.get("type") != "auth" or not hmac.compare_digest(
                    str(msg.get("token") or ""), SESSION_TOKEN):
                await _safe_send(websocket, {"type": "auth_error", "code": "bad_token",
                                             "message": "Invalid token"})
                await websocket.close(code=CLOSE_AUTH, reason="Invalid token")
                return

            # Optional protocol negotiation: clients that send one must overlap
            # with ours; clients that send none are treated as protocol 1.
            client_proto = msg.get("protocol", 1)
            if not isinstance(client_proto, int) or client_proto not in SUPPORTED_PROTOCOLS:
                LOG.warning("rejected client protocol %r (supported: %s)",
                            client_proto, sorted(SUPPORTED_PROTOCOLS))
                await _safe_send(websocket, {
                    "type": "auth_error", "code": "incompatible_protocol",
                    "supported": sorted(SUPPORTED_PROTOCOLS),
                    "appVersion": APP_VERSION,
                    "message": "This dashboard is not compatible with the installed "
                               "Valorant Scout version. Run UPDATE.bat to update the app."})
                await websocket.close(code=CLOSE_PROTOCOL, reason="Incompatible protocol")
                return

            await _safe_send(websocket, {"type": "auth_ok",
                                         "protocol": PROTOCOL_VERSION,
                                         "supported": sorted(SUPPORTED_PROTOCOLS),
                                         "appVersion": APP_VERSION,
                                         "capabilities": CAPABILITIES})
            _CLIENTS.add(websocket)

            try:
                board = await loop.run_in_executor(None, board_provider)
                await _safe_send(websocket, {"type": "state", "data": board})
            except Exception:
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
                        except Exception as e:
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
                                  "rateLimited", "dedup",
                                  "queue", "queueId", "inQueue"):
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
                            except Exception:
                                pass
                except Exception:
                    pass
                await asyncio.sleep(interval)

        async def _heartbeat_loop():
            while True:
                await asyncio.sleep(30)
                await _broadcast({"type": "ping"})

        async def _main():
            # Bind first; only a successful bind signals readiness. A failure
            # (port grabbed between our check and here) lands in boot_error.
            async with _ws_serve(
                handler, "127.0.0.1", ws_port,
                process_request=_process_request,
                extra_headers=handshake_headers,
                ping_interval=None,
                max_queue=16,
            ):
                _log(f"listening on ws://127.0.0.1:{ws_port} "
                     f"(protocol {PROTOCOL_VERSION}, origins: {', '.join(sorted(ALLOWED_ORIGINS))})")
                ready.set()
                await asyncio.gather(_broadcast_loop(), _heartbeat_loop())

        try:
            loop.run_until_complete(_main())
        except Exception as e:
            boot_error.append(e)
            LOG.error("VS-WS-001 server stopped: %s", e)
            _log(f"server stopped: {e}")
            ready.set()
        finally:
            _READY.clear()
            _WS_PORT = None

    threading.Thread(target=_run, daemon=True, name="scout-ws").start()

    if not ready.wait(timeout=15) or boot_error:
        reason = str(boot_error[0]) if boot_error else "timed out waiting for the listener"
        raise RuntimeError(f"VS-WS-001 local WebSocket bridge failed to start "
                           f"on 127.0.0.1:{ws_port}: {reason}")

    try:
        _self_handshake(ws_port)
        LOG.info("authenticated self-handshake ok on port %s", ws_port)
    except Exception as e:
        raise RuntimeError(f"VS-WS-001 WebSocket self-handshake failed on "
                           f"127.0.0.1:{ws_port}: {e}") from e

    _WS_PORT = ws_port
    _READY.set()
    url = dashboard_url(_FRONTEND_URL, ws_port)
    print("\n[scout] Dashboard authentication ready; opening your browser.\n", flush=True)
    if open_dashboard and os.getenv("SCOUT_NO_BROWSER", "").strip() not in ("1", "true"):
        _spawn_opener(_FRONTEND_URL, url, backend_port)

    return SESSION_TOKEN

def _spawn_opener(frontend_url: str, url: str, backend_port: int | None) -> None:
    def _show_fallback() -> None:
        # The URL contains the per-launch authentication secret. Show it only
        # in a local dialog, never in stdout/stderr or persistent log files.
        if os.name != "nt":
            _log("couldn't open your browser automatically; restart after setting a default browser")
            return
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None,
                "Valorant Scout couldn't open your default browser.\n\n"
                "Copy this private one-time dashboard URL (Ctrl+C copies this dialog):\n\n"
                + url,
                "Valorant Scout", 0x30)
        except Exception:
            _log("couldn't open your browser automatically; set a default browser and restart")

    def _wait(target: str, deadline: float) -> bool:
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(target, timeout=2) as r:
                    if r.status < 500:
                        return True
            except Exception:
                time.sleep(1.0)
        return False

    def _wait_and_open():
        # The dashboard is only useful once the backend answers; don't send the
        # user to a board that immediately errors.
        if backend_port and not _wait(f"http://127.0.0.1:{backend_port}/api/health",
                                      time.time() + 60):
            LOG.warning("backend health not confirmed before opening dashboard")
        opened = _wait(frontend_url.rstrip("/") + "/", time.time() + 90)
        try:
            if not webbrowser.open(url):
                raise RuntimeError("webbrowser.open returned False")
        except Exception:
            LOG.warning("VS-BROWSER-001 couldn't open a browser automatically")
            _show_fallback()
        if not opened:
            _log("opened dashboard (frontend health unconfirmed — "
                 "if the page errors, retry once it's up).")

    threading.Thread(target=_wait_and_open, daemon=True, name="scout-open").start()
