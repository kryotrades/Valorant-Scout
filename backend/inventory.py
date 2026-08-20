from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any

import valapi

if TYPE_CHECKING:
    from riot_client import LocalAuth

ITEM_TYPES = {
    "skins": "e7c63390-eda7-46e0-bb7a-a6abdacd2433",
    "buddies": "dd3bf334-87f3-40bd-b043-682a57a8dc3a",
    "cards": "3f296c07-64c3-494c-923b-fe692a4fa1bd",
    "sprays": "d5f120f8-ff8c-4aac-92ea-f2b5acbe9475",
    "agents": "01bb38e1-da47-4e6a-9b3d-945fe4655707",
}
_CUR_VP = "85ad13f7-3d1b-5128-9eb2-7cd8ee0b5741"
_CUR_RAD = "e59aa87c-4cbf-517a-5983-6e81511be9b7"
_CUR_KC = "85ca954a-41f2-ce94-9b45-8ca3dd39a00d"

_TIER_VP = {
    "12683d76-48d7-84a3-4e09-6985794f0445": 875,
    "0cebb8be-46d7-c12a-d306-e9907bfc5a25": 1275,
    "60bca009-4182-7998-dee7-b8a2558dc369": 1775,
    "e046854e-406c-37f4-6607-19a9ba8426fc": 2175,
    "411e4a55-4e59-7757-41f0-86a53f101bb5": 2475,
}
_TIER_NAMES = {
    "12683d76-48d7-84a3-4e09-6985794f0445": "Select",
    "0cebb8be-46d7-c12a-d306-e9907bfc5a25": "Deluxe",
    "60bca009-4182-7998-dee7-b8a2558dc369": "Premium",
    "e046854e-406c-37f4-6607-19a9ba8426fc": "Exclusive",
    "411e4a55-4e59-7757-41f0-86a53f101bb5": "Ultra",
}

_MELEE_VP = 3550

_VP_PER_USD = 107.0

_LOCK = threading.Lock()
_CACHE: dict[str, dict[str, Any]] = {}
_TTL = 600.0


def last_good(puuid: str | None = None) -> dict[str, Any] | None:
    with _LOCK:
        cached = _CACHE.get(str(puuid)) if puuid else None
        if not cached or not cached.get("data"):
            return None
        return {**cached["data"], "stale": True}


def _contract_rewards() -> set[Any]:
    if "_contractskins" in valapi._cache:
        return valapi._cache["_contractskins"]
    out = set()
    for c in valapi._get("contracts") or []:
        for ch in (c.get("content") or {}).get("chapters") or []:
            rewards = list(ch.get("freeRewards") or [])
            rewards += [lv.get("reward") or {} for lv in ch.get("levels") or []]
            for r in rewards:
                if r.get("type") == "EquippableSkinLevel" and r.get("uuid"):
                    out.add(r["uuid"].lower())
    valapi._cache["_contractskins"] = out
    return out


def _base_levels() -> dict[str, Any]:
    if "_baselevels" in valapi._cache:
        return valapi._cache["_baselevels"]
    out = {}
    for w in valapi._get("weapons") or []:
        melee = (w.get("displayName") or "").lower() == "melee"
        for skin in w.get("skins") or []:
            vp = _TIER_VP.get((skin.get("contentTierUuid") or "").lower())
            levels = skin.get("levels") or []
            if not vp or not levels:
                continue
            base = levels[0]
            tier_uuid = (skin.get("contentTierUuid") or "").lower()
            out[base["uuid"].lower()] = {
                "name": (skin.get("displayName") or "").strip(),
                "icon": base.get("displayIcon") or skin.get("displayIcon"),
                "vp": _MELEE_VP if melee else vp,
                "tier": _TIER_NAMES.get(tier_uuid, "Exclusive" if melee else "Other"),
            }
    valapi._cache["_baselevels"] = out
    return out


def _owned_ids(auth: LocalAuth, item_type: str) -> list[str]:
    data = auth.pd_get(f"/store/v1/entitlements/{auth.puuid}/{item_type}")
    return [
        (e.get("ItemID") or "").lower()
        for e in (data or {}).get("Entitlements", []) or []
        if e.get("ItemID")
    ]


def snapshot(auth: LocalAuth) -> dict[str, Any]:
    now = time.time()
    auth.headers()
    owner = str(auth.puuid)
    with _LOCK:
        cached = _CACHE.get(owner)
        if cached and cached.get("data") and now - cached.get("at", 0) < _TTL:
            return cached["data"]

    owned = _owned_ids(auth, ITEM_TYPES["skins"])
    base = _base_levels()
    freebies = _contract_rewards()

    total = 0
    priced = []
    earned = 0
    for sid in owned:
        meta = base.get(sid)
        if not meta:
            continue
        if sid in freebies:
            earned += 1
            continue
        total += meta["vp"]
        priced.append(meta)
    priced.sort(key=lambda s: s["vp"], reverse=True)
    tiers: dict[str, Any] = {}
    for item in priced:
        bucket = tiers.setdefault(item.get("tier") or "Other", {"skins": 0, "vp": 0})
        bucket["skins"] += 1
        bucket["vp"] += item["vp"]

    previous_ids = set((cached or {}).get("ownedIds") or [])
    current_ids = {sid for sid in owned if sid in base and sid not in freebies}
    recent_ids = current_ids - previous_ids if previous_ids else set()
    recent = [base[sid] for sid in recent_ids if sid in base]
    recent.sort(key=lambda item: item["vp"], reverse=True)

    wallet = (auth.pd_get(f"/store/v1/wallet/{auth.puuid}") or {}).get("Balances") or {}

    counts: dict[str, int | None] = {"skins": len(priced), "earned": earned}
    for key in ("buddies", "cards", "sprays", "agents"):
        try:
            counts[key] = len(_owned_ids(auth, ITEM_TYPES[key]))
        except Exception:
            counts[key] = None

    data = {
        "available": True,
        "totalVp": total,
        "usdApprox": round(total / _VP_PER_USD),
        "wallet": {
            "vp": wallet.get(_CUR_VP, 0),
            "rad": wallet.get(_CUR_RAD, 0),
            "kc": wallet.get(_CUR_KC, 0),
        },
        "counts": counts,
        "tiers": tiers,
        "top": priced[:20],
        "recent": recent[:8],
        "at": int(now),
    }
    with _LOCK:
        _CACHE[owner] = {"at": now, "data": data, "ownedIds": sorted(current_ids)}
    return data


if __name__ == "__main__":
    _VP_BY_NAME = {"Select": 875, "Deluxe": 1275, "Premium": 1775, "Exclusive": 2175, "Ultra": 2475}
    tiers = valapi._get("contenttiers") or []
    assert tiers, "couldn't fetch contenttiers"
    for t in tiers:
        uuid, name = (t.get("uuid") or "").lower(), t.get("devName")
        assert uuid in _TIER_VP, f"{name} ({uuid}) missing from _TIER_VP"
        assert _TIER_VP[uuid] == _VP_BY_NAME[name], (
            f"{name} priced {_TIER_VP[uuid]}, should be {_VP_BY_NAME[name]}"
        )
    assert len(_TIER_VP) == len(tiers), "_TIER_VP has uuids the API doesn't know"

    base = _base_levels()
    knife = next(v for k, v in base.items() if "RGX 11z Pro Blade" in v["name"])
    assert knife["vp"] == _MELEE_VP, knife

    assert _contract_rewards() & set(base), "reward exclusion matched nothing"

    print(
        f"inventory self-check OK ({len(base)} priced skins, "
        f"{len(_contract_rewards())} reward skins excluded)"
    )
