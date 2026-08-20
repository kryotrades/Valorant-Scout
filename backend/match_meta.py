from __future__ import annotations

import json
import threading
import time
from typing import Any

from common import data_path, write_atomic

_PATH = data_path("match_meta.json")
_LOCK = threading.RLock()


def _load() -> dict[str, Any]:
    try:
        with open(_PATH, encoding="utf-8") as fh:
            raw = json.load(fh)
        if (
            isinstance(raw, dict)
            and raw.get("version") == 1
            and isinstance(raw.get("accounts"), dict)
        ):
            return raw
    except Exception:
        pass
    return {"version": 1, "accounts": {}}


def _save() -> None:
    write_atomic(_PATH, _STORE, prefix=".match-meta-")


_STORE = _load()


def get_all(puuid: str | None) -> dict[str, Any]:
    if not puuid:
        return {}
    with _LOCK:
        account = _STORE.setdefault("accounts", {}).get(str(puuid), {})
        return {key: dict(value) for key, value in account.items()}


def get_one(puuid: str | None, match_id: str) -> dict[str, Any]:
    return get_all(puuid).get(match_id, {"note": "", "tags": [], "bookmarked": False})


def update(puuid: str | None, match_id: str, payload: object) -> dict[str, Any]:
    if not puuid or not match_id:
        return {"ok": False, "message": "An active account and match are required."}
    body = payload if isinstance(payload, dict) else {}
    note = str(body.get("note") or "").strip()[:500]
    tags: list[str] = []
    for raw in body.get("tags") or []:
        tag = str(raw).strip()[:24]
        if tag and tag.lower() not in {item.lower() for item in tags}:
            tags.append(tag)
        if len(tags) == 5:
            break
    meta = {
        "note": note,
        "tags": tags,
        "bookmarked": bool(body.get("bookmarked")),
        "updatedAt": int(time.time()),
    }
    with _LOCK:
        account = _STORE.setdefault("accounts", {}).setdefault(str(puuid), {})
        if note or tags or meta["bookmarked"]:
            account[match_id] = meta
        else:
            account.pop(match_id, None)
        _save()
    return {"ok": True, "matchId": match_id, "meta": meta}
