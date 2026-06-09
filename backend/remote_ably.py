"""
remote_ably.py
==============
Remote / phone mode over Ably (the agent side that runs inside the desktop app).

Activation flow (driven from the dashboard's "View on phone" button):

  1. Frontend sends ``enable_remote`` over the local WebSocket.
  2. We mint a high-entropy ``remote_session_id``.
  3. We fetch an AGENT-scoped Ably token from ABLY_TOKEN_ENDPOINT and a
     PHONE-scoped token for the shareable URL.  (The desktop app NEVER holds the
     root Ably API key — it only ever receives short-lived, channel-scoped
     tokens minted by the Vercel serverless route.)
  4. We connect to Ably as the agent and:
       * subscribe to  scout:commands:<id>   (commands from the phone)
       * publish to     scout:state:<id>      (board updates)
       * publish to     scout:acks:<id>       (command acknowledgements)
  5. We return the phone URL (with the phone token as ``t``) for the QR/link.

Commands from the phone go through the SAME CommandRouter as the local socket,
so validation / rate-limiting / de-duplication are identical.

This module is optional: if the ``ably`` package isn't installed it degrades to
"not configured" and local desktop mode keeps working untouched.

NOTE: the Ably wire calls are written against ably-python v2 (asyncio). They are
wrapped defensively and run in a dedicated event-loop thread; if your installed
SDK differs, the guards keep the rest of the app alive and surface a clear
message rather than crashing.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import threading
import time
from urllib.parse import quote

import requests

try:  # optional dependency — remote mode only
    from ably import AblyRealtime
except Exception:  # noqa: BLE001
    AblyRealtime = None


def _log(msg: str) -> None:
    print(f"[remote] {msg}", flush=True)


# Ably caps a single message at 64 KB. The full board (~110 KB) carries each
# player's entire weapon inventory in BOTH `players` and `teams`, which alone is
# ~70 KB. The remote scoreboard renders from `skin`/rank/etc., not the full
# inventory (only the desktop ProfileModal uses `weapons`, and it guards for an
# empty list), so we blank `weapons` for the Ably payload. This keeps the phone
# fully functional while dropping the board to ~40 KB. The local WebSocket still
# sends the complete board (no size limit), so the desktop is unaffected.
def _slim_for_ably(board: dict) -> dict:
    if not isinstance(board, dict):
        return board

    def slim_player(p):
        if isinstance(p, dict) and p.get("weapons"):
            q = dict(p)
            q["weapons"] = []
            return q
        return p

    out = dict(board)
    if isinstance(out.get("players"), list):
        out["players"] = [slim_player(p) for p in out["players"]]
    if isinstance(out.get("teams"), dict):
        out["teams"] = {t: [slim_player(p) for p in plist]
                        for t, plist in out["teams"].items()}
    return out


class RemoteConfigError(Exception):
    """Raised when the token endpoint reports Ably isn't configured."""


class RemoteController:
    """Owns the lifecycle of one remote (Ably) session at a time."""

    def __init__(self, *, frontend_url: str, token_endpoint: str, board_provider,
                 data_handler=None):
        self.frontend_url = (frontend_url or "http://localhost:3000").rstrip("/")
        self.token_endpoint = token_endpoint
        self.board_provider = board_provider
        # data_handler(request_type, params) -> dict for on-demand drill-in data.
        self.data_handler = data_handler
        self.router = None
        self._lock = threading.Lock()
        self._session: dict | None = None
        self._active = False

    def attach_router(self, router) -> None:
        self.router = router

    def is_active(self) -> bool:
        return self._active

    # -- token fetch --------------------------------------------------------
    def _fetch_token(self, session_id: str, role: str) -> dict:
        url = (f"{self.token_endpoint}?sessionId={quote(session_id, safe='')}"
               f"&role={role}")
        r = requests.get(url, timeout=10)
        if r.status_code == 501:
            raise RemoteConfigError(
                "Remote mode is not configured. Set ABLY_API_KEY in the "
                "frontend/Vercel environment.")
        if not r.ok:
            raise RuntimeError(f"token endpoint returned {r.status_code}: "
                               f"{r.text[:200]}")
        data = r.json()
        if not isinstance(data, dict) or not data.get("token"):
            raise RuntimeError("token endpoint returned no token")
        return data

    # -- enable / disable ---------------------------------------------------
    def enable(self) -> dict:
        with self._lock:
            if self._active and self._session:
                return {"ok": True, "message": "Remote mode already enabled.",
                        "remoteUrl": self._session["remote_url"],
                        "remoteSessionId": self._session["session_id"]}
            if AblyRealtime is None:
                return {"ok": False, "configured": False,
                        "message": "Remote mode needs the 'ably' package on the "
                                   "desktop app. Install it with: pip install ably"}
            if self.router is None:
                return {"ok": False, "message": "Remote command router not ready."}

            session_id = secrets.token_urlsafe(32)
            try:
                phone_details = self._fetch_token(session_id, "phone")
            except RemoteConfigError as e:
                return {"ok": False, "configured": False, "message": str(e)}
            except Exception as e:  # noqa: BLE001
                return {"ok": False,
                        "message": f"Couldn't reach the Ably token endpoint: {e}"}

            phone_token = phone_details["token"]
            remote_url = (f"{self.frontend_url}/remote/{session_id}"
                          f"?mode=remote&t={quote(phone_token, safe='')}")

            ready = threading.Event()
            sess = {
                "session_id": session_id,
                "remote_url": remote_url,
                "ready": ready,
                "err": {},
                "loop": None,
                "client": None,
                "state_ch": None,
                "ack_ch": None,
                "stop": None,
                "phone_seen": False,
                "started_at": time.time(),
            }
            t = threading.Thread(target=self._agent_thread, args=(sess,),
                                 daemon=True, name="scout-ably")
            sess["thread"] = t
            self._session = sess
            t.start()

            ready.wait(timeout=10)
            if sess["err"].get("error"):
                msg = sess["err"]["error"]
                self._teardown_locked()
                return {"ok": False, "message": f"Ably connect failed: {msg}"}

            self._active = True
            _log(f"remote mode enabled (session {session_id[:8]}…)")
            return {"ok": True, "message": "Remote mode enabled",
                    "remoteUrl": remote_url, "remoteSessionId": session_id}

    def disable(self) -> dict:
        with self._lock:
            if not self._session:
                self._active = False
                return {"ok": True, "message": "Remote mode was not active."}
            return self._teardown_locked()

    def _teardown_locked(self) -> dict:
        sess = self._session
        self._session = None
        self._active = False
        if sess:
            loop = sess.get("loop")
            stop = sess.get("stop")
            if loop is not None and stop is not None:
                try:
                    loop.call_soon_threadsafe(stop.set)
                except Exception:  # noqa: BLE001
                    pass
        _log("remote mode disabled")
        return {"ok": True, "message": "Remote mode disabled."}

    def shutdown(self) -> None:
        """Clean teardown on app exit."""
        try:
            self.disable()
        except Exception:  # noqa: BLE001
            pass

    # -- state publishing (called from the ws broadcast loop on change) -----
    def publish_state(self, board: dict) -> None:
        sess = self._session
        if not (self._active and sess):
            return
        loop = sess.get("loop")
        ch = sess.get("state_ch")
        if loop is None or ch is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                ch.publish("state", _slim_for_ably(board)), loop)
        except Exception:  # noqa: BLE001
            pass

    # -- agent event loop ---------------------------------------------------
    def _agent_thread(self, sess: dict) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        sess["loop"] = loop
        try:
            loop.run_until_complete(self._agent_main(sess))
        except Exception as e:  # noqa: BLE001
            sess["err"]["error"] = str(e)
            sess["ready"].set()
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:  # noqa: BLE001
                pass
            loop.close()

    async def _agent_main(self, sess: dict) -> None:
        session_id = sess["session_id"]
        stop = asyncio.Event()
        sess["stop"] = stop

        async def auth_cb(_token_params):
            # Re-fetch an agent-scoped token whenever Ably needs one (initial +
            # renewal), so a 60-minute TTL never ends an active session.
            loop = asyncio.get_event_loop()
            details = await loop.run_in_executor(
                None, lambda: self._fetch_token(session_id, "agent"))
            return details["token"]

        client = AblyRealtime(auth_callback=auth_cb)
        sess["client"] = client

        state_ch = client.channels.get(f"scout:state:{session_id}")
        cmd_ch = client.channels.get(f"scout:commands:{session_id}")
        ack_ch = client.channels.get(f"scout:acks:{session_id}")
        sess["state_ch"] = state_ch
        sess["ack_ch"] = ack_ch

        async def on_cmd(message):
            await self._handle_remote_command(sess, message)

        await cmd_ch.subscribe(on_cmd)

        # Presence: notice when a phone joins so we can auto-disable if none ever
        # does. Best-effort — if the SDK's presence API differs we fall back to
        # the plain idle timer below.
        try:
            async def on_presence(member):
                cid = str(getattr(member, "client_id", "") or "")
                action = str(getattr(member, "action", "") or "")
                if cid.startswith("phone"):
                    sess["phone_seen"] = True
                    # A (re)joining phone needs the current board right away,
                    # since state is otherwise only pushed on change.
                    if action in ("enter", "present", ""):
                        try:
                            await state_ch.publish(
                                "state", _slim_for_ably(self.board_provider()))
                        except Exception:  # noqa: BLE001
                            pass
            await state_ch.presence.subscribe(on_presence)
            for m in (await state_ch.presence.get()) or []:
                if str(getattr(m, "client_id", "") or "").startswith("phone"):
                    sess["phone_seen"] = True
        except Exception:  # noqa: BLE001
            # TODO: presence unavailable on this SDK build — idle auto-disable
            # then relies solely on whether a command was ever received.
            pass

        sess["ready"].set()

        # Seed the phone with the current board immediately.
        try:
            await state_ch.publish("state", _slim_for_ably(self.board_provider()))
        except Exception:  # noqa: BLE001
            pass

        async def idle_watch():
            await asyncio.sleep(180)  # 3 minutes
            if not stop.is_set() and not sess.get("phone_seen"):
                _log("no phone joined within 3 min — disabling remote mode.")
                # disable() teardown sets `stop`; run it off-loop to avoid lock
                # interplay inside the event loop.
                threading.Thread(target=self.disable, daemon=True).start()

        async def state_pump():
            # Heartbeat the current board onto the state channel so a phone that
            # connects AFTER the initial publish (the common case — the QR is
            # scanned seconds later) still gets a snapshot, even when the board
            # is static (idle lobby / demo) and nothing else triggers a push.
            # On-change updates from the WS broadcast loop still arrive instantly.
            while not stop.is_set():
                try:
                    await state_ch.publish(
                        "state", _slim_for_ably(self.board_provider()))
                except Exception:  # noqa: BLE001
                    pass
                try:
                    await asyncio.wait_for(stop.wait(), timeout=4.0)
                except asyncio.TimeoutError:
                    pass

        idle_task = asyncio.ensure_future(idle_watch())
        pump_task = asyncio.ensure_future(state_pump())
        try:
            await stop.wait()
        finally:
            idle_task.cancel()
            pump_task.cancel()
            try:
                await client.close()
            except Exception:  # noqa: BLE001
                pass

    async def _handle_remote_command(self, sess: dict, message) -> None:
        data = getattr(message, "data", None)
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:  # noqa: BLE001
                data = {}
        if not isinstance(data, dict):
            data = {}

        # Any inbound message also proves a phone is connected.
        sess["phone_seen"] = True

        loop = asyncio.get_event_loop()

        # On-demand data request (profile / match / encounter) — published on the
        # commands channel, answered on the acks channel, same as a command ack.
        if data.get("request") and self.data_handler is not None:
            rid = data.get("id")
            rtype = data.get("request")
            params = data.get("params") or {}
            try:
                result = await loop.run_in_executor(
                    None, lambda: self.data_handler(rtype, params))
                ack = {"id": rid, "ok": True, "data": result}
            except Exception as e:  # noqa: BLE001
                ack = {"id": rid, "ok": False, "error": str(e)}
            try:
                await sess["ack_ch"].publish("ack", ack)
            except Exception:  # noqa: BLE001
                pass
            return

        command = data.get("command")
        payload = data.get("payload") or {}
        cid = data.get("id")
        client_id = "ably:" + sess["session_id"][:8]

        result = await loop.run_in_executor(
            None, lambda: self.router.execute(
                client_id=client_id, command=command, payload=payload,
                command_id=cid))

        ack = {"id": cid, "ok": bool(result.get("ok")),
               "message": result.get("message", "")}
        for k in ("remoteUrl", "remoteSessionId", "side", "map", "status",
                  "agent", "configured", "rateLimited", "dedup"):
            if k in result:
                ack[k] = result[k]
        try:
            await sess["ack_ch"].publish("ack", ack)
        except Exception:  # noqa: BLE001
            pass
